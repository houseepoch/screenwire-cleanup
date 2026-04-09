"""
ScreenWire AI — FastAPI Core Engine (MVP single-file server)
Headless pipeline backend: manifest reconciliation, file sentinel,
agent process management, and Layer 1 programmatic gateways.
"""

import asyncio
import atexit
import base64
import json
import mimetypes
import os
import re
import signal
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

APP_DIR = Path(__file__).resolve().parent
load_dotenv(APP_DIR / ".env")

_project_dir_env = os.getenv("PROJECT_DIR")
if not _project_dir_env:
    print("ERROR: PROJECT_DIR environment variable is required. "
          "Set it to the project directory path, or let run_pipeline.py pass it.")
    sys.exit(1)
PROJECT_DIR = Path(_project_dir_env)

REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN", "")


def log(module: str, message: str) -> None:
    print(f"[{datetime.now().isoformat()}] [{module}] {message}")


# ---------------------------------------------------------------------------
# Module A: ManifestReconciler
# ---------------------------------------------------------------------------

class ManifestReconciler:
    """Single-writer pattern for project_manifest.json."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.manifest_path = project_dir / "project_manifest.json"
        self.queue_dir = project_dir / "dispatch" / "manifest_queue"
        self.dead_letters_dir = self.queue_dir / "dead_letters"
        self.queue: asyncio.Queue[dict] = asyncio.Queue()
        self.manifest: dict[str, Any] = {}
        self.observer: Optional[Observer] = None
        self._writer_task: Optional[asyncio.Task] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    # -- bootstrap ----------------------------------------------------------

    def load_manifest(self) -> None:
        if self.manifest_path.exists():
            self.manifest = json.loads(self.manifest_path.read_text())
            log("ManifestReconciler", f"Loaded manifest v{self.manifest.get('version', 0)}")
        else:
            self.manifest = {"version": 0, "frames": [], "cast": [], "locations": [], "props": [], "phases": {}}
            log("ManifestReconciler", "Initialized empty manifest")

    def start_watcher(self) -> None:
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        self.dead_letters_dir.mkdir(parents=True, exist_ok=True)

        handler = _ManifestQueueHandler(self)
        self.observer = Observer()
        self.observer.schedule(handler, str(self.queue_dir), recursive=False)
        self.observer.daemon = True
        self.observer.start()
        log("ManifestReconciler", f"Watching {self.queue_dir}")

    async def start_writer(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._writer_task = asyncio.create_task(self._writer_loop())

    async def stop(self) -> None:
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=3)
        if self._writer_task:
            self._writer_task.cancel()
            try:
                await self._writer_task
            except asyncio.CancelledError:
                pass

    # -- queue ingestion (called from watchdog thread) ----------------------

    def ingest_file(self, filepath: Path) -> None:
        if not filepath.suffix == ".json":
            return
        try:
            text = filepath.read_text()
            text = re.sub(r"^```\w*\n|\n```$", "", text.strip())
            data = json.loads(text)
        except (json.JSONDecodeError, Exception) as exc:
            dest = self.dead_letters_dir / filepath.name
            filepath.rename(dest)
            log("ManifestReconciler", f"Bad file → dead_letters: {filepath.name} ({exc})")
            return

        # Push into async queue from sync context (thread-safe)
        try:
            if self.loop is not None:
                self.loop.call_soon_threadsafe(self.queue.put_nowait, data)
            else:
                self.queue.put_nowait(data)
        except asyncio.QueueFull:
            log("ManifestReconciler", f"Queue full, dropping {filepath.name}")
        filepath.unlink(missing_ok=True)
        log("ManifestReconciler", f"Queued update from {filepath.name}")

    # -- writer loop --------------------------------------------------------

    async def _writer_loop(self) -> None:
        log("ManifestReconciler", "Writer loop started")
        while True:
            data = await self.queue.get()
            try:
                self._apply_updates(data)
                self.manifest["version"] = self.manifest.get("version", 0) + 1
                self._atomic_write()
                log("ManifestReconciler", f"Manifest updated to v{self.manifest['version']}")
            except Exception as exc:
                log("ManifestReconciler", f"Error applying update: {exc}")

    def _apply_updates(self, data: dict) -> None:
        for update in data.get("updates", []):
            target = update.get("target")
            set_dict = update.get("set", {})

            if target == "frame":
                self._merge_by_key("frames", "frameId", update.get("frameId"), set_dict)
            elif target == "cast":
                self._merge_by_key("cast", "castId", update.get("castId"), set_dict)
            elif target == "location":
                self._merge_by_key("locations", "locationId", update.get("locationId"), set_dict)
            elif target == "prop":
                self._merge_by_key("props", "propId", update.get("propId"), set_dict)
            elif target == "dialogue":
                self._update_dialogue(update.get("dialogueId"), set_dict)
            elif target == "phase":
                phases = self.manifest.setdefault("phases", {})
                phase_id = update.get("phaseId")
                phases.setdefault(phase_id, {}).update(set_dict)
            elif target == "project":
                self.manifest.update(set_dict)
            else:
                log("ManifestReconciler", f"Unknown target: {target}")

    def _merge_by_key(self, collection: str, key_field: str, key_value: str, set_dict: dict) -> None:
        items = self.manifest.setdefault(collection, [])
        for item in items:
            if item.get(key_field) == key_value:
                item.update(set_dict)
                return
        # Not found — create entry
        new_item = {key_field: key_value}
        new_item.update(set_dict)
        items.append(new_item)

    def _update_dialogue(self, dialogue_id: str, set_dict: dict) -> None:
        dialogue_path = self.project_dir / "dialogue.json"
        if dialogue_path.exists():
            dialogue_data = json.loads(dialogue_path.read_text())
        else:
            dialogue_data = []

        for item in dialogue_data:
            if item.get("dialogueId") == dialogue_id:
                item.update(set_dict)
                break
        else:
            new_item = {"dialogueId": dialogue_id}
            new_item.update(set_dict)
            dialogue_data.append(new_item)

        tmp = dialogue_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(dialogue_data, indent=2))
        os.replace(tmp, dialogue_path)

    def _atomic_write(self) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.manifest, indent=2))
        os.replace(tmp, self.manifest_path)


class _ManifestQueueHandler(FileSystemEventHandler):
    def __init__(self, reconciler: ManifestReconciler) -> None:
        self.reconciler = reconciler

    def on_created(self, event) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        self.reconciler.ingest_file(Path(event.src_path))


# ---------------------------------------------------------------------------
# Module B: Sentinel (simplified MVP — logging only)
# ---------------------------------------------------------------------------

class Sentinel:
    """Watches key project directories and logs file creation events.
    Also auto-tags images in cast/location/prop directories with entity names.
    """

    WATCH_SUBDIRS = ["video/clips", "frames/composed", "audio/dialogue", "dispatch/flags"]
    # Directories where images get auto-tagged with entity names
    TAG_SUBDIRS = {
        "cast/composites": "cast",
        "locations/primary": "location",
        "props/generated": "prop",
        "assets/active/mood": "mood",
    }

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.observer: Optional[Observer] = None

    def start(self) -> None:
        handler = _SentinelHandler()
        self.observer = Observer()
        for subdir in self.WATCH_SUBDIRS:
            watch_path = self.project_dir / subdir
            watch_path.mkdir(parents=True, exist_ok=True)
            self.observer.schedule(handler, str(watch_path), recursive=False)
            log("Sentinel", f"Watching {watch_path}")

        # Auto-tag watchers for image directories
        for subdir, entity_type in self.TAG_SUBDIRS.items():
            watch_path = self.project_dir / subdir
            watch_path.mkdir(parents=True, exist_ok=True)
            tag_handler = _ImageTagHandler(entity_type, self.project_dir)
            self.observer.schedule(tag_handler, str(watch_path), recursive=False)
            log("Sentinel", f"Auto-tag watching: {watch_path}")

        self.observer.daemon = True
        self.observer.start()

    def stop(self) -> None:
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=3)


class _SentinelHandler(FileSystemEventHandler):
    def on_created(self, event) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix == ".tmp":
            return
        try:
            if path.stat().st_size == 0:
                return
        except OSError:
            return
        log("Sentinel", f"New file: {path}")


class _ImageTagHandler(FileSystemEventHandler):
    """Auto-tags images with entity names when they appear in watched directories."""

    def __init__(self, entity_type: str, project_dir: Path) -> None:
        self.entity_type = entity_type
        self.project_dir = project_dir

    def on_created(self, event) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            return
        if path.suffix == ".tmp":
            return
        # Delay to ensure file is fully written
        import time
        time.sleep(0.5)
        try:
            if not path.exists() or path.stat().st_size == 0:
                return
        except OSError:
            return
        try:
            from image_tagger import resolve_label, tag_image
            label = resolve_label(path, self.entity_type, self.project_dir)
            tag_image(path, label)
        except Exception as exc:
            log("ImageTagger", f"Failed to tag {path.name}: {exc}")


# ---------------------------------------------------------------------------
# Module C: Agent Process Manager
# ---------------------------------------------------------------------------

class AgentProcessManager:
    """Spawn, message, and kill Claude CLI agent subprocesses."""

    def __init__(self) -> None:
        self.registry: dict[str, asyncio.subprocess.Process] = {}

    async def spawn_agent(
        self,
        agent_id: str,
        system_prompt: str,
        cwd: str,
        model: str = "claude-opus-4-6",
    ) -> asyncio.subprocess.Process:
        cmd = [
            "claude",
            "-p", system_prompt,
            "--dangerously-skip-permissions",
            "--output-format", "stream-json",
            "--model", model,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        self.registry[agent_id] = proc
        log("AgentProcessManager", f"Spawned agent '{agent_id}' (pid={proc.pid})")
        return proc

    async def send_directive(self, agent_id: str, message: str) -> None:
        proc = self.registry.get(agent_id)
        if proc is None or proc.stdin is None:
            raise ValueError(f"Agent '{agent_id}' not found or stdin unavailable")
        proc.stdin.write(f"{message}\n".encode())
        await proc.stdin.drain()
        log("AgentProcessManager", f"Sent directive to '{agent_id}'")

    async def kill_agent(self, agent_id: str) -> None:
        proc = self.registry.pop(agent_id, None)
        if proc is None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        log("AgentProcessManager", f"Killed agent '{agent_id}'")

    async def kill_all(self) -> None:
        agent_ids = list(self.registry.keys())
        for agent_id in agent_ids:
            await self.kill_agent(agent_id)
        log("AgentProcessManager", "All agents killed")

    def get_status(self, agent_id: str) -> str:
        proc = self.registry.get(agent_id)
        if proc is None:
            return "dead"
        return "alive" if proc.returncode is None else "dead"


# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------

reconciler = ManifestReconciler(PROJECT_DIR)
sentinel = Sentinel(PROJECT_DIR)
agent_mgr = AgentProcessManager()
http_client: httpx.AsyncClient  # initialized in lifespan


# ---------------------------------------------------------------------------
# Retry predicate — only retry on 429 / 500 / 503
# ---------------------------------------------------------------------------

def _retryable_status(retry_state) -> bool:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 503)
    return False


_gateway_retry = retry(
    retry=_retryable_status,
    wait=wait_exponential(min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)


# ---------------------------------------------------------------------------
# Layer 1: Programmatic Gateway — Request / Response Models
# ---------------------------------------------------------------------------

class GenerateImageRequest(BaseModel):
    prompt: str
    image_size: str = "landscape_16_9"
    output_path: str
    seed: Optional[int] = None
    output_format: str = "png"


class GenerateImageReduxRequest(BaseModel):
    image_url: str
    prompt: str = ""
    image_size: str = "landscape_16_9"
    output_path: str
    seed: Optional[int] = None


class EditImageRequest(BaseModel):
    """Edit an existing image via nano-banana model chain (nano-banana-2 → pro → base).

    The source image is uploaded as the primary image_input, and the prompt
    describes the desired edit/transformation.
    """
    input_path: str  # source image to edit
    prompt: str      # edit instruction
    image_size: str = "landscape_16_9"
    output_path: str
    seed: Optional[int] = None
    output_format: str = "png"
    image_search: bool = False  # Google Image Search grounding for visual context
    google_search: bool = False  # Google Web Search grounding for real-time info


class FreshGenerationRequest(BaseModel):
    """Generate a new image via nano-banana model chain with optional reference images.

    Optionally accepts reference_images (local paths or URLs) for style/subject
    guidance without requiring a source image to edit.
    """
    prompt: str
    image_size: str = "landscape_16_9"
    output_path: str
    seed: Optional[int] = None
    output_format: str = "png"
    reference_images: list[str] = Field(default_factory=list)
    image_search: bool = False  # Google Image Search grounding for visual context
    google_search: bool = False  # Google Web Search grounding for real-time info


class DesignVoiceRequest(BaseModel):
    pass


class SaveVoiceRequest(BaseModel):
    pass


class GenerateTTSRequest(BaseModel):
    pass


class GenerateDialogueRequest(BaseModel):
    pass


class GenerateVideoRequest(BaseModel):
    model: str  # "prunaai/p-video" or "xai/grok-imagine-video"
    prompt: str
    image_path: Optional[str] = None
    audio_path: Optional[str] = None
    duration: int = 5
    resolution: str = "720p"
    output_path: str
    extra_params: dict[str, Any] = Field(default_factory=dict)


class UploadToReplicateRequest(BaseModel):
    file_path: str


class SpawnAgentRequest(BaseModel):
    agent_id: str
    system_prompt: str
    cwd: str
    model: str = "claude-opus-4-6"


class SendDirectiveRequest(BaseModel):
    agent_id: str
    message: str


class KillAgentRequest(BaseModel):
    agent_id: str


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    http_client = httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10))

    # Manifest reconciler
    reconciler.load_manifest()
    reconciler.start_watcher()
    await reconciler.start_writer()

    # Sentinel
    sentinel.start()

    log("Engine", f"ScreenWire AI engine started — project: {PROJECT_DIR}")

    yield

    # Shutdown
    await reconciler.stop()
    sentinel.stop()
    await agent_mgr.kill_all()
    await http_client.aclose()
    log("Engine", "Engine shut down cleanly")


app = FastAPI(title="ScreenWire AI Engine", version="0.1.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Graceful shutdown helpers
# ---------------------------------------------------------------------------

def _sync_shutdown() -> None:
    """atexit handler — best-effort cleanup."""
    log("Engine", "atexit shutdown triggered")


atexit.register(_sync_shutdown)


def _signal_handler(sig, frame) -> None:
    log("Engine", f"Received signal {sig}, shutting down")
    sys.exit(0)


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ---------------------------------------------------------------------------
# Helper: resolve output paths relative to PROJECT_DIR
# ---------------------------------------------------------------------------

def _resolve_output(output_path: str) -> Path:
    p = Path(output_path)
    if not p.is_absolute():
        p = PROJECT_DIR / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Public API Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/project/current")
async def get_current_project():
    return reconciler.manifest


@app.post("/api/images/tag-all")
async def tag_all_images():
    """Tag all existing cast/location/prop/mood images with entity name overlays."""
    try:
        from image_tagger import tag_all_project_images
        count = tag_all_project_images(PROJECT_DIR)
        return {"success": True, "tagged": count}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Agent Management Routes
# ---------------------------------------------------------------------------

@app.post("/api/agents/spawn")
async def api_spawn_agent(req: SpawnAgentRequest):
    proc = await agent_mgr.spawn_agent(req.agent_id, req.system_prompt, req.cwd, req.model)
    return {"agent_id": req.agent_id, "pid": proc.pid, "status": "alive"}


@app.post("/api/agents/directive")
async def api_send_directive(req: SendDirectiveRequest):
    try:
        await agent_mgr.send_directive(req.agent_id, req.message)
        return {"agent_id": req.agent_id, "sent": True}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/agents/kill")
async def api_kill_agent(req: KillAgentRequest):
    await agent_mgr.kill_agent(req.agent_id)
    return {"agent_id": req.agent_id, "killed": True}


@app.post("/api/agents/kill-all")
async def api_kill_all_agents():
    await agent_mgr.kill_all()
    return {"killed": True}


@app.get("/api/agents/{agent_id}/status")
async def api_agent_status(agent_id: str):
    status = agent_mgr.get_status(agent_id)
    return {"agent_id": agent_id, "status": status}


# ---------------------------------------------------------------------------
# Layer 1: POST /internal/generate-image
# ---------------------------------------------------------------------------

@app.post("/internal/generate-image")
async def generate_image(req: GenerateImageRequest):
    """Reference image generation via prunaai/p-image (sub-1s, used for mood/cast/location/prop assets)."""
    output = _resolve_output(req.output_path)

    # Map image_size to p-image aspect_ratio format
    aspect_map = {
        "landscape_16_9": "16:9", "landscape_4_3": "4:3", "landscape_3_2": "3:2",
        "portrait_9_16": "9:16", "portrait_3_4": "3:4", "portrait_2_3": "2:3",
        "square": "1:1", "square_hd": "1:1",
    }
    aspect_ratio = aspect_map.get(req.image_size, "16:9")

    pred_input: dict[str, Any] = {
        "prompt": req.prompt,
        "aspect_ratio": aspect_ratio,
        "disable_safety_checker": True,
    }
    if req.seed is not None:
        pred_input["seed"] = req.seed

    headers = {
        "Authorization": f"Bearer {REPLICATE_API_TOKEN}",
        "Content-Type": "application/json",
        "Prefer": "wait",
    }

    try:
        pred_data = await _replicate_predict("prunaai/p-image", pred_input, headers)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)

    # Poll if not yet succeeded
    prediction_id = pred_data.get("id", "")
    if pred_data.get("status") != "succeeded":
        pred_data = await _poll_replicate_prediction(prediction_id, headers)

    if pred_data.get("status") != "succeeded":
        error_detail = _build_prediction_error(pred_data, req.prompt)
        raise HTTPException(status_code=502, detail=error_detail)

    output_url = pred_data.get("output")
    if isinstance(output_url, list):
        output_url = output_url[0]

    seed_val = pred_data.get("metrics", {}).get("seed") or pred_data.get("input", {}).get("seed")

    await _download_file(output_url, output)

    return {"success": True, "path": str(output), "seed": seed_val, "prediction_id": prediction_id}


# ---------------------------------------------------------------------------
# Layer 1: POST /internal/generate-frame
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Fallback chain: nano-banana-2 → nano-banana-pro → nano-banana
# ---------------------------------------------------------------------------

IMAGE_MODEL_CHAIN = [
    "google/nano-banana-2",
    "google/nano-banana-pro",
    "google/nano-banana",
]


def _adapt_input_for_model(model: str, base_input: dict[str, Any]) -> dict[str, Any]:
    """Adapt prediction input params for each model's supported schema."""
    inp = dict(base_input)
    if model == "google/nano-banana-pro":
        # Rename grounding params: google_search → google_search_grounding, image_search → google_image_search
        if inp.pop("google_search", None):
            inp["google_search_grounding"] = True
        if inp.pop("image_search", None):
            inp["google_image_search"] = True
        inp.setdefault("safety_tolerance", 4)
    elif model == "google/nano-banana":
        # Base model only supports: prompt, image_input, aspect_ratio, output_format
        allowed = {"prompt", "image_input", "aspect_ratio", "output_format"}
        inp = {k: v for k, v in inp.items() if k in allowed}
    return inp


async def _generate_with_fallback(
    pred_input: dict[str, Any],
    headers: dict,
    output: Path,
    prompt: str,
    reference_images: list[str],
    aspect_ratio: str,
    t0: float,
) -> dict:
    """Try each model in IMAGE_MODEL_CHAIN until one succeeds."""
    import time as _time
    last_error = None

    for model in IMAGE_MODEL_CHAIN:
        adapted = _adapt_input_for_model(model, pred_input)
        try:
            pred_data = await _replicate_predict(model, adapted, headers)
        except httpx.HTTPStatusError as exc:
            log("Fallback", f"{model} HTTP error: {exc.response.status_code}")
            _log_composition(
                output_path=str(output), prompt=prompt, model=model,
                prediction_id="", reference_images=reference_images, success=False,
                aspect_ratio=aspect_ratio, error=str(exc),
                duration_ms=int((_time.monotonic() - t0) * 1000),
            )
            last_error = exc
            continue

        prediction_id = pred_data.get("id", "")
        if pred_data.get("status") != "succeeded":
            pred_data = await _poll_replicate_prediction(prediction_id, headers)

        if pred_data.get("status") != "succeeded":
            error_detail = _build_prediction_error(pred_data, prompt)
            log("Fallback", f"{model} failed: {error_detail.get('failure_type', 'UNKNOWN')} — trying next model")
            _log_composition(
                output_path=str(output), prompt=prompt, model=model,
                prediction_id=prediction_id, reference_images=reference_images, success=False,
                aspect_ratio=aspect_ratio, error=error_detail.get("failure_type", "UNKNOWN"),
                duration_ms=int((_time.monotonic() - t0) * 1000),
            )
            last_error = error_detail
            continue

        # Success
        output_url = pred_data.get("output")
        if isinstance(output_url, list):
            output_url = output_url[0]

        seed_val = pred_data.get("metrics", {}).get("seed") or pred_data.get("input", {}).get("seed")
        await _download_file(output_url, output)

        elapsed = int((_time.monotonic() - t0) * 1000)
        _log_composition(
            output_path=str(output), prompt=prompt, model=model,
            prediction_id=prediction_id, reference_images=reference_images, success=True,
            aspect_ratio=aspect_ratio, seed=seed_val, duration_ms=elapsed,
        )

        return {"success": True, "path": str(output), "seed": seed_val,
                "prediction_id": prediction_id, "model": model}

    # All models exhausted
    if isinstance(last_error, httpx.HTTPStatusError):
        raise HTTPException(status_code=last_error.response.status_code, detail=last_error.response.text)
    raise HTTPException(status_code=502, detail=last_error if isinstance(last_error, dict) else {"error": "All models failed"})


# ---------------------------------------------------------------------------
# Layer 1: POST /internal/generate-frame (nano-banana-2 + fallback chain)
# ---------------------------------------------------------------------------

class GenerateFrameRequest(BaseModel):
    prompt: str
    image_size: str = "landscape_16_9"
    output_path: str
    seed: Optional[int] = None
    output_format: str = "png"
    reference_images: list[str] = Field(default_factory=list)  # paths to cast/location ref images


@app.post("/internal/generate-frame")
async def generate_frame(req: GenerateFrameRequest):
    """Frame generation with automatic fallback: nano-banana-2 → nano-banana-pro → nano-banana.

    Accepts reference_images — local file paths to cast composites, location refs, etc.
    These are uploaded as data URIs and passed via image_input parameter (up to 14 images)
    for character/scene consistency.
    """
    import time as _time
    _t0 = _time.monotonic()

    output = _resolve_output(req.output_path)

    aspect_map = {
        "landscape_16_9": "16:9", "landscape_4_3": "4:3", "landscape_3_2": "3:2",
        "portrait_9_16": "9:16", "portrait_3_4": "3:4", "portrait_2_3": "2:3",
        "square": "1:1", "square_hd": "1:1",
    }
    aspect_ratio = aspect_map.get(req.image_size, "16:9")

    pred_input: dict[str, Any] = {
        "prompt": req.prompt,
        "aspect_ratio": aspect_ratio,
        "resolution": "1K",
        "output_format": req.output_format if req.output_format in ("jpg", "png") else "png",
    }

    # Upload reference images as data URIs for character/scene consistency
    if req.reference_images:
        image_input = []
        for ref_path in req.reference_images:
            p = Path(ref_path)
            if not p.is_absolute():
                p = PROJECT_DIR / p
            if p.exists():
                data_uri = await _upload_to_replicate(p)
                image_input.append(data_uri)
                log("FrameGen", f"Attached reference: {p.name}")
            else:
                log("FrameGen", f"WARNING: Reference image not found: {ref_path}")
        if image_input:
            pred_input["image_input"] = image_input

    headers = {
        "Authorization": f"Bearer {REPLICATE_API_TOKEN}",
        "Content-Type": "application/json",
        "Prefer": "wait",
    }

    return await _generate_with_fallback(
        pred_input, headers, output, req.prompt,
        req.reference_images, aspect_ratio, _t0,
    )


# ---------------------------------------------------------------------------
# Layer 1: POST /internal/generate-location-direction  (prunaai/p-image-edit)
# ---------------------------------------------------------------------------

P_IMAGE_EDIT_MODEL = "prunaai/p-image-edit"


class GenerateLocationDirectionRequest(BaseModel):
    prompt: str
    input_path: str                                     # Primary location image (north-facing)
    image_size: str = "landscape_16_9"
    output_path: str
    output_format: str = "png"
    reference_images: list[str] = Field(default_factory=list)  # Currently unused; reserved
    seed: Optional[int] = None


@app.post("/internal/generate-location-direction")
async def generate_location_direction(req: GenerateLocationDirectionRequest):
    """Generate a location direction view using prunaai/p-image-edit.

    Takes the primary (north-facing) location image and edits it into the
    requested cardinal direction view while preserving architecture, materials,
    and lighting.
    """
    import time as _time
    _t0 = _time.monotonic()

    output = _resolve_output(req.output_path)

    # Resolve the source image (primary/north view)
    source = Path(req.input_path)
    if not source.is_absolute():
        source = PROJECT_DIR / source
    if not source.exists():
        raise HTTPException(status_code=400, detail=f"Source image not found: {source}")

    # Upload source image as data URI
    source_uri = await _upload_to_replicate(source)

    # Map size preset to aspect ratio
    aspect_map = {
        "landscape_16_9": "16:9", "landscape_4_3": "4:3", "landscape_3_2": "3:2",
        "portrait_9_16": "9:16", "portrait_3_4": "3:4", "portrait_2_3": "2:3",
        "square": "1:1", "square_hd": "1:1",
    }
    aspect_ratio = aspect_map.get(req.image_size, "match_input_image")

    pred_input: dict[str, Any] = {
        "prompt": req.prompt,
        "images": [source_uri],
        "turbo": False,         # Perspective changes are complex; disable turbo
        "aspect_ratio": aspect_ratio,
    }
    if req.seed is not None:
        pred_input["seed"] = req.seed

    headers = {
        "Authorization": f"Bearer {REPLICATE_API_TOKEN}",
        "Content-Type": "application/json",
        "Prefer": "wait",
    }

    try:
        pred_data = await _replicate_predict(P_IMAGE_EDIT_MODEL, pred_input, headers)
    except httpx.HTTPStatusError as exc:
        log("LocDirection", f"p-image-edit HTTP error: {exc.response.status_code}")
        _log_composition(
            output_path=str(output), prompt=req.prompt, model=P_IMAGE_EDIT_MODEL,
            prediction_id="", reference_images=[req.input_path], success=False,
            aspect_ratio=aspect_ratio,
            error=str(exc),
            duration_ms=int((_time.monotonic() - _t0) * 1000),
        )
        raise HTTPException(status_code=502, detail=f"p-image-edit HTTP error: {exc.response.status_code}")

    prediction_id = pred_data.get("id", "")
    if pred_data.get("status") != "succeeded":
        pred_data = await _poll_replicate_prediction(prediction_id, headers)

    if pred_data.get("status") != "succeeded":
        error_detail = _build_prediction_error(pred_data, req.prompt)
        _log_composition(
            output_path=str(output), prompt=req.prompt, model=P_IMAGE_EDIT_MODEL,
            prediction_id=prediction_id, reference_images=[req.input_path], success=False,
            aspect_ratio=aspect_ratio,
            error=error_detail.get("failure_type", "UNKNOWN"),
            duration_ms=int((_time.monotonic() - _t0) * 1000),
        )
        raise HTTPException(status_code=502, detail=error_detail)

    # Download result
    output_url = pred_data.get("output")
    if isinstance(output_url, list):
        output_url = output_url[0]

    seed_val = pred_data.get("metrics", {}).get("seed") or pred_data.get("input", {}).get("seed")
    await _download_file(output_url, output)

    elapsed = int((_time.monotonic() - _t0) * 1000)
    _log_composition(
        output_path=str(output), prompt=req.prompt, model=P_IMAGE_EDIT_MODEL,
        prediction_id=prediction_id, reference_images=[req.input_path], success=True,
        aspect_ratio=aspect_ratio, seed=seed_val, duration_ms=elapsed,
    )

    return {
        "success": True,
        "path": str(output),
        "seed": seed_val,
        "prediction_id": prediction_id,
        "model": P_IMAGE_EDIT_MODEL,
    }


# ---------------------------------------------------------------------------
# Layer 1: POST /internal/generate-image-redux
# ---------------------------------------------------------------------------

@app.post("/internal/generate-image-redux")
async def generate_image_redux(req: GenerateImageReduxRequest):
    """Image-to-image variation via Flux 2 Pro redux (used for frame variations from reference images)."""
    output = _resolve_output(req.output_path)

    aspect_map = {
        "landscape_16_9": "16:9", "landscape_4_3": "4:3", "landscape_3_2": "3:2",
        "portrait_9_16": "9:16", "portrait_3_4": "3:4", "portrait_2_3": "2:3",
        "square": "1:1", "square_hd": "1:1",
    }
    aspect_ratio = aspect_map.get(req.image_size, "16:9")

    pred_input: dict[str, Any] = {
        "prompt": req.prompt,
        "input_images": [req.image_url],
        "aspect_ratio": aspect_ratio,
        "resolution": "1 MP",
        "output_format": "png",
        "output_quality": 80,
        "safety_tolerance": 5,
    }
    if req.seed is not None:
        pred_input["seed"] = req.seed

    headers = {
        "Authorization": f"Bearer {REPLICATE_API_TOKEN}",
        "Content-Type": "application/json",
        "Prefer": "wait",
    }

    try:
        pred_data = await _replicate_predict("black-forest-labs/flux-2-pro", pred_input, headers)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)

    prediction_id = pred_data.get("id", "")
    if pred_data.get("status") != "succeeded":
        pred_data = await _poll_replicate_prediction(prediction_id, headers)

    if pred_data.get("status") != "succeeded":
        error_detail = _build_prediction_error(pred_data, req.prompt)
        raise HTTPException(status_code=502, detail=error_detail)

    output_url = pred_data.get("output")
    if isinstance(output_url, list):
        output_url = output_url[0]

    seed_val = pred_data.get("metrics", {}).get("seed") or pred_data.get("input", {}).get("seed")

    await _download_file(output_url, output)

    return {"success": True, "path": str(output), "seed": seed_val, "prediction_id": prediction_id}


# ---------------------------------------------------------------------------
# Layer 1: POST /internal/edit-image (nano-banana-2 image editing)
# ---------------------------------------------------------------------------

@app.post("/internal/edit-image")
async def edit_image(req: EditImageRequest):
    """Edit an existing image with fallback chain: nano-banana-2 → nano-banana-pro → nano-banana.

    The source image is passed as the primary image_input. The prompt describes
    the desired modification (e.g. "Add heavy sweat streaking through face paint").
    """
    import time as _time
    _t0 = _time.monotonic()

    output = _resolve_output(req.output_path)

    # Resolve and upload the source image
    input_path = Path(req.input_path)
    if not input_path.is_absolute():
        input_path = PROJECT_DIR / input_path
    if not input_path.exists():
        raise HTTPException(status_code=400, detail=f"Source image not found: {input_path}")

    source_uri = await _upload_to_replicate(input_path)

    aspect_map = {
        "landscape_16_9": "16:9", "landscape_4_3": "4:3", "landscape_3_2": "3:2",
        "portrait_9_16": "9:16", "portrait_3_4": "3:4", "portrait_2_3": "2:3",
        "square": "1:1", "square_hd": "1:1",
    }
    aspect_ratio = aspect_map.get(req.image_size, "16:9")

    pred_input: dict[str, Any] = {
        "prompt": req.prompt,
        "image_input": [source_uri],
        "aspect_ratio": aspect_ratio,
        "resolution": "1K",
        "output_format": req.output_format if req.output_format in ("jpg", "png") else "png",
    }
    if req.seed is not None:
        pred_input["seed"] = req.seed
    if req.image_search:
        pred_input["image_search"] = True
    if req.google_search:
        pred_input["google_search"] = True

    headers = {
        "Authorization": f"Bearer {REPLICATE_API_TOKEN}",
        "Content-Type": "application/json",
        "Prefer": "wait",
    }

    result = await _generate_with_fallback(
        pred_input, headers, output, req.prompt,
        [], aspect_ratio, _t0,
    )

    grounding = []
    if req.image_search:
        grounding.append("image_search")
    if req.google_search:
        grounding.append("google_search")

    log("EditImage", f"Edited {input_path.name} → {output.name}" + (f" (grounding: {','.join(grounding)})" if grounding else ""))
    result["source"] = str(input_path)
    result["grounding"] = grounding
    return result


# ---------------------------------------------------------------------------
# Layer 1: POST /internal/fresh-generation (nano-banana-2 from scratch)
# ---------------------------------------------------------------------------

@app.post("/internal/fresh-generation")
async def fresh_generation(req: FreshGenerationRequest):
    """Generate a new image with fallback chain: nano-banana-2 → nano-banana-pro → nano-banana.

    Higher quality than p-image for hero assets. Optionally accepts reference
    images for style/subject guidance.
    """
    import time as _time
    _t0 = _time.monotonic()

    output = _resolve_output(req.output_path)

    aspect_map = {
        "landscape_16_9": "16:9", "landscape_4_3": "4:3", "landscape_3_2": "3:2",
        "portrait_9_16": "9:16", "portrait_3_4": "3:4", "portrait_2_3": "2:3",
        "square": "1:1", "square_hd": "1:1",
    }
    aspect_ratio = aspect_map.get(req.image_size, "16:9")

    pred_input: dict[str, Any] = {
        "prompt": req.prompt,
        "aspect_ratio": aspect_ratio,
        "resolution": "1K",
        "output_format": req.output_format if req.output_format in ("jpg", "png") else "png",
    }
    if req.seed is not None:
        pred_input["seed"] = req.seed
    if req.image_search:
        pred_input["image_search"] = True
    if req.google_search:
        pred_input["google_search"] = True

    # Attach reference images if provided
    if req.reference_images:
        image_input = []
        for ref in req.reference_images:
            ref_path = Path(ref)
            if not ref_path.is_absolute():
                ref_path = PROJECT_DIR / ref_path
            if ref_path.exists():
                data_uri = await _upload_to_replicate(ref_path)
                image_input.append(data_uri)
                log("FreshGen", f"Attached reference: {ref_path.name}")
            else:
                log("FreshGen", f"WARNING: Reference image not found: {ref}")
        if image_input:
            pred_input["image_input"] = image_input

    headers = {
        "Authorization": f"Bearer {REPLICATE_API_TOKEN}",
        "Content-Type": "application/json",
        "Prefer": "wait",
    }

    result = await _generate_with_fallback(
        pred_input, headers, output, req.prompt,
        req.reference_images, aspect_ratio, _t0,
    )

    grounding = []
    if req.image_search:
        grounding.append("image_search")
    if req.google_search:
        grounding.append("google_search")

    log("FreshGen", f"Generated → {output.name}" + (f" (grounding: {','.join(grounding)})" if grounding else ""))
    result["reference_count"] = len(req.reference_images)
    result["grounding"] = grounding
    return result


# ---------------------------------------------------------------------------
# Layer 1: POST /internal/design-voice  (TTS provider removed)
# ---------------------------------------------------------------------------

@app.post("/internal/design-voice")
async def design_voice(req: DesignVoiceRequest):
    return {"status": "skipped", "reason": "TTS provider removed"}


# ---------------------------------------------------------------------------
# Layer 1: POST /internal/save-voice  (TTS provider removed)
# ---------------------------------------------------------------------------

@app.post("/internal/save-voice")
async def save_voice(req: SaveVoiceRequest):
    return {"status": "skipped", "reason": "TTS provider removed"}


# ---------------------------------------------------------------------------
# Layer 1: POST /internal/generate-tts  (TTS provider removed)
# ---------------------------------------------------------------------------

@app.post("/internal/generate-tts")
async def generate_tts(req: GenerateTTSRequest):
    return {"status": "skipped", "reason": "TTS provider removed"}


# ---------------------------------------------------------------------------
# Layer 1: POST /internal/generate-dialogue  (TTS provider removed)
# ---------------------------------------------------------------------------

@app.post("/internal/generate-dialogue")
async def generate_dialogue(req: GenerateDialogueRequest):
    return {"status": "skipped", "reason": "TTS provider removed"}


# ---------------------------------------------------------------------------
# Layer 1: POST /internal/generate-video
# ---------------------------------------------------------------------------

@app.post("/internal/generate-video")
async def generate_video(req: GenerateVideoRequest):
    output = _resolve_output(req.output_path)

    # Upload local files to Replicate if provided
    image_url = None
    audio_url = None

    if req.image_path:
        image_url = await _upload_to_replicate(Path(req.image_path))
    if req.audio_path:
        audio_url = await _upload_to_replicate(Path(req.audio_path))

    # Build prediction input
    if req.model == "prunaai/p-video":
        # p-video clamp 3-10s
        effective_duration = min(max(req.duration, 3), 10)
        pred_input: dict[str, Any] = {
            "prompt": req.prompt,
            "resolution": req.resolution,
            "num_frames": effective_duration * 24,  # explicit frame count = duration * fps
            "fps": 24,
            "save_audio": True,
            "draft": False,
            "prompt_upsampling": True,
        }
        if image_url:
            pred_input["image"] = image_url
        if audio_url:
            pred_input["audio"] = audio_url
        pred_input.update(req.extra_params)

    elif req.model == "xai/grok-imagine-video":
        pred_input = {
            "prompt": req.prompt,
            "duration": min(max(req.duration, 3), 15),  # clamp 3-15s
            "resolution": req.resolution,
            "aspect_ratio": "auto",
        }
        if image_url:
            pred_input["image"] = image_url
        pred_input.update(req.extra_params)

    else:
        raise HTTPException(status_code=400, detail=f"Unknown video model: {req.model}")

    headers = {
        "Authorization": f"Bearer {REPLICATE_API_TOKEN}",
        "Content-Type": "application/json",
        "Prefer": "wait",
    }

    try:
        pred_data = await _replicate_predict(req.model, pred_input, headers)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)

    # Poll if not yet succeeded
    prediction_id = pred_data.get("id", "")
    if pred_data.get("status") not in ("succeeded",):
        pred_data = await _poll_replicate_prediction(prediction_id, headers)

    if pred_data.get("status") != "succeeded":
        error_detail = _build_prediction_error(pred_data, req.prompt)
        raise HTTPException(status_code=502, detail=error_detail)

    # Download output
    output_url = pred_data.get("output")
    if isinstance(output_url, list):
        output_url = output_url[0]

    if output_url:
        await _download_file(output_url, output)

    return {"success": True, "path": str(output), "prediction_id": prediction_id}


# ---------------------------------------------------------------------------
# Layer 1: POST /internal/upload-to-replicate
# ---------------------------------------------------------------------------

@app.post("/internal/upload-to-replicate")
async def api_upload_to_replicate(req: UploadToReplicateRequest):
    file_path = Path(req.file_path)
    if not file_path.is_absolute():
        file_path = PROJECT_DIR / file_path
    url = await _upload_to_replicate(file_path)
    return {"url": url}




# ---------------------------------------------------------------------------
# Internal helpers: Replicate prediction error reporting
# ---------------------------------------------------------------------------

# Known Replicate error codes and their meanings
_REPLICATE_ERROR_CODES = {
    "E005": "NSFW/safety filter triggered — the prompt or generated output was flagged as sensitive content",
    "E004": "Model timeout — prediction took too long",
    "E003": "Model error — internal model failure",
}


def _classify_replicate_error(error_msg: str, logs: str) -> dict:
    """Parse Replicate error message and logs to classify the failure type."""
    error_msg = error_msg or ""
    logs = logs or ""

    # Extract error code (E005, E004, etc.)
    import re as _re
    code_match = _re.search(r'\(E(\d+)\)', error_msg)
    error_code = f"E{code_match.group(1)}" if code_match else "UNKNOWN"

    # Detect specific failure types
    is_safety = ("NSFW" in logs or "flagged as sensitive" in error_msg or error_code == "E005"
                 or "Content Blocked" in error_msg or "IMAGE_SAFETY" in error_msg
                 or "blockReason" in error_msg)
    is_timeout = "timeout" in error_msg.lower() or error_code == "E004"

    failure_type = "SAFETY_FILTER" if is_safety else ("TIMEOUT" if is_timeout else "MODEL_ERROR")

    # Build trigger words list for safety failures
    trigger_hints = []
    if is_safety:
        # Common words that trigger safety filters in military/action contexts
        trigger_hints = [
            "Avoid: blood, wound, gunshot, gore, corpse, dead body, kill",
            "Avoid: weapon aimed at camera, violence in progress",
            "Rephrase: 'injured soldier' → 'battle-worn soldier with torn uniform'",
            "Rephrase: 'gunshot wound' → 'damaged combat gear'",
            "Rephrase: 'blood' → 'dirt and grime'",
            "Tip: Focus on emotion and atmosphere rather than explicit injury",
            "Tip: Use 'war photography' or 'documentary style' framing",
        ]

    return {
        "failure_type": failure_type,
        "error_code": error_code,
        "description": _REPLICATE_ERROR_CODES.get(error_code, error_msg),
        "is_retryable": is_safety,  # safety failures can be retried with rephrased prompt
        "rephrase_hints": trigger_hints,
    }


def _log_composition(
    output_path: str,
    prompt: str,
    model: str,
    prediction_id: str,
    reference_images: list[str],
    success: bool,
    aspect_ratio: str = "",
    seed: Any = None,
    error: str | None = None,
    duration_ms: int | None = None,
) -> None:
    """Write a composition ledger entry for every frame generation attempt.

    Creates two outputs:
    1. Individual prompt file: frames/composed/prompts/{frameId}_prompt.txt
    2. Append to ledger JSONL: logs/production_coordinator/composition_ledger.jsonl
    """
    out_p = Path(output_path)
    frame_id = out_p.stem.replace("_gen", "").split("_v")[0]  # f_001_gen.png → f_001
    timestamp = datetime.now().isoformat()

    # --- Individual prompt file ---
    prompts_dir = out_p.parent / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = prompts_dir / f"{out_p.stem}_prompt.txt"
    try:
        prompt_file.write_text(prompt, encoding="utf-8")
    except Exception as exc:
        log("CompositionLedger", f"Failed to write prompt file: {exc}")

    # --- Ledger JSONL entry ---
    ledger_dir = PROJECT_DIR / "logs" / "production_coordinator"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = ledger_dir / "composition_ledger.jsonl"

    # Shorten ref image paths for readability
    short_refs = [Path(r).name for r in reference_images]

    entry = {
        "timestamp": timestamp,
        "frame_id": frame_id,
        "output_path": output_path,
        "model": model,
        "prediction_id": prediction_id,
        "success": success,
        "prompt": prompt,
        "prompt_length": len(prompt),
        "prompt_language": "zh" if any("\u4e00" <= c <= "\u9fff" for c in prompt[:50]) else "en",
        "reference_images": short_refs,
        "ref_count": len(short_refs),
        "aspect_ratio": aspect_ratio,
        "seed": seed,
    }
    if error:
        entry["error"] = error
    if duration_ms is not None:
        entry["duration_ms"] = duration_ms

    try:
        with open(ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        log("CompositionLedger", f"{'✓' if success else '✗'} {frame_id} [{model.split('/')[-1]}] refs={len(short_refs)} lang={entry['prompt_language']}")
    except Exception as exc:
        log("CompositionLedger", f"Failed to write ledger: {exc}")


def _build_prediction_error(pred_data: dict, original_prompt: str) -> dict:
    """Build a structured error response from a failed Replicate prediction."""
    error_msg = pred_data.get("error", "Unknown error")
    logs = pred_data.get("logs", "")
    prediction_id = pred_data.get("id", "")
    model = pred_data.get("model", "")

    classification = _classify_replicate_error(error_msg, logs)

    # Write failure report to project dispatch for agent visibility
    report = {
        "prediction_id": prediction_id,
        "model": model,
        "original_prompt": original_prompt,
        "error": error_msg,
        "failure_type": classification["failure_type"],
        "error_code": classification["error_code"],
        "is_retryable": classification["is_retryable"],
        "rephrase_hints": classification["rephrase_hints"],
        "timestamp": datetime.now().isoformat(),
    }

    # Write to project failures log
    failures_dir = PROJECT_DIR / "logs" / "prediction_failures"
    failures_dir.mkdir(parents=True, exist_ok=True)
    failure_path = failures_dir / f"{prediction_id or 'unknown'}.json"
    try:
        failure_path.write_text(json.dumps(report, indent=2))
        log("PredictionError", f"{classification['failure_type']} → {failure_path.name}")
    except Exception as exc:
        log("PredictionError", f"Failed to write failure report: {exc}")

    return report


# ---------------------------------------------------------------------------
# Internal helpers: Replicate API
# ---------------------------------------------------------------------------

@_gateway_retry
async def _replicate_predict(model: str, pred_input: dict, headers: dict) -> dict:
    """Create a Replicate prediction with retry on 429/500/503."""
    resp = await http_client.post(
        f"https://api.replicate.com/v1/models/{model}/predictions",
        json={"input": pred_input},
        headers=headers,
    )
    if resp.status_code in (429, 500, 503):
        resp.raise_for_status()
    elif resp.status_code >= 400:
        resp.raise_for_status()
    return resp.json()


async def _upload_to_replicate(file_path: Path) -> str:
    """Convert a local file to a data URI for Replicate prediction inputs.

    Replicate's files API returns metadata URLs that models cannot fetch directly.
    Data URIs work reliably across all Replicate-hosted models.
    """
    if not file_path.exists():
        raise HTTPException(status_code=400, detail=f"File not found: {file_path}")

    mime_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    file_bytes = file_path.read_bytes()
    size_mb = len(file_bytes) / (1024 * 1024)
    if size_mb > 10:
        log("Upload", f"WARNING: Large file ({size_mb:.1f} MB) being sent as data URI: {file_path.name}")
    b64 = base64.b64encode(file_bytes).decode()
    return f"data:{mime_type};base64,{b64}"


async def _poll_replicate_prediction(prediction_id: str, headers: dict) -> dict:
    """Poll a Replicate prediction until terminal state."""
    url = f"https://api.replicate.com/v1/predictions/{prediction_id}"
    for _ in range(120):  # up to 10 minutes
        await asyncio.sleep(5)
        resp = await http_client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status")
        log("VideoGateway", f"Prediction {prediction_id}: {status}")
        if status in ("succeeded", "failed", "canceled"):
            return data
    return {"status": "timeout", "error": "Polling timed out after 10 minutes"}


# ---------------------------------------------------------------------------
# Internal helpers: File download
# ---------------------------------------------------------------------------

async def _download_file(url: str, output: Path) -> None:
    """Download a URL to a local path using atomic write."""
    tmp = output.with_suffix(output.suffix + ".tmp")
    async with http_client.stream("GET", url) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as f:
            async for chunk in resp.aiter_bytes(8192):
                f.write(chunk)
    os.replace(tmp, output)
    log("Download", f"Saved {output}")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("SW_PORT", "8000")))
