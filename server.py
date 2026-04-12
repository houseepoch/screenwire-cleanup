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
import shutil
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

from llm.xai_client import DEFAULT_REASONING_MODEL
from telemetry import activate_run_context, current_phase, current_run_id, emit_event

from handlers import (
    get_handler,
    CastImageInput,
    CastImageOutput,
    FrameInput,
    FrameOutput,
    VideoClipInput,
    VideoClipOutput,
    LocationGridInput,
    LocationGridOutput,
    StoryboardInput,
    StoryboardOutput,
)

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
XAI_API_KEY = os.getenv("XAI_API_KEY", "")
ENABLE_MANIFEST_QUEUE = os.getenv("SCREENWIRE_ENABLE_MANIFEST_QUEUE", "").strip().lower() in {"1", "true", "yes", "on"}


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
            dialogue_data = {"dialogue": []}

        if isinstance(dialogue_data, dict):
            lines = dialogue_data.setdefault("dialogue", [])
        elif isinstance(dialogue_data, list):
            lines = dialogue_data
            dialogue_data = {"dialogue": lines}
        else:
            lines = []
            dialogue_data = {"dialogue": lines}

        for item in lines:
            if item.get("dialogueId") == dialogue_id:
                item.update(set_dict)
                break
        else:
            new_item = {"dialogueId": dialogue_id}
            new_item.update(set_dict)
            lines.append(new_item)

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
    """Spawn, message, and kill local Grok-backed agent subprocesses."""

    def __init__(self) -> None:
        self.registry: dict[str, asyncio.subprocess.Process] = {}

    async def spawn_agent(
        self,
        agent_id: str,
        system_prompt: str,
        cwd: str,
        model: str = DEFAULT_REASONING_MODEL,
    ) -> asyncio.subprocess.Process:
        env = dict(os.environ)
        existing_pythonpath = env.get("PYTHONPATH", "")
        repo_root = str(APP_DIR)
        env["PYTHONPATH"] = repo_root if not existing_pythonpath else f"{repo_root}{os.pathsep}{existing_pythonpath}"
        cmd = [
            sys.executable,
            "-m", "llm.agent_runner",
            "--system-prompt", system_prompt,
            "--dangerously-skip-permissions",
            "--output-format", "stream-json",
            "--model", model,
            "--task-hint", agent_id,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
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
    size: str = "landscape_16_9"
    output_path: str
    seed: Optional[int] = None
    output_format: str = "png"
    cast_id: Optional[str] = None       # handler: identifies cast member for output naming
    media_style: Optional[str] = None   # handler: triggers live-action upscale when set
    run_id: Optional[str] = None
    phase: str = ""


class EditImageRequest(BaseModel):
    """Edit an existing image via nano-banana model chain (nano-banana-2 → pro → base).

    The source image is uploaded as the primary image_input, and the prompt
    describes the desired edit/transformation.
    """
    input_path: str  # source image to edit
    prompt: str      # edit instruction
    size: str = "landscape_16_9"
    output_path: str
    seed: Optional[int] = None
    output_format: str = "png"
    image_search: bool = False  # Google Image Search grounding for visual context
    google_search: bool = False  # Google Web Search grounding for real-time info
    run_id: Optional[str] = None
    phase: str = ""


class FreshGenerationRequest(BaseModel):
    """Generate a new image via nano-banana model chain with optional reference images.

    Optionally accepts reference_images (local paths or URLs) for style/subject
    guidance without requiring a source image to edit.
    """
    prompt: str
    size: str = "landscape_16_9"
    output_path: str
    seed: Optional[int] = None
    output_format: str = "png"
    reference_images: list[str] = Field(default_factory=list)
    image_search: bool = False  # Google Image Search grounding for visual context
    google_search: bool = False  # Google Web Search grounding for real-time info
    run_id: Optional[str] = None
    phase: str = ""


class GenerateVideoRequest(BaseModel):
    prompt: str
    image_path: Optional[str] = None
    duration: int = 5
    resolution: str = "720p"
    output_path: str
    extra_params: dict[str, Any] = Field(default_factory=dict)
    frame_id: Optional[str] = None       # handler: identifies frame for output naming
    dialogue_text: Optional[str] = None  # handler: prefixed before prompt for lip-sync
    run_id: Optional[str] = None
    phase: str = ""


class UploadToReplicateRequest(BaseModel):
    file_path: str


class SpawnAgentRequest(BaseModel):
    agent_id: str
    system_prompt: str
    cwd: str
    model: str = DEFAULT_REASONING_MODEL


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
    http_client = httpx.AsyncClient(timeout=None)

    reconciler.load_manifest()
    if ENABLE_MANIFEST_QUEUE:
        reconciler.start_watcher()
        await reconciler.start_writer()
        log("Engine", "Manifest queue reconciler enabled")
    else:
        log("Engine", "Manifest queue reconciler disabled; graph materialization is authoritative")

    # Sentinel
    sentinel.start()

    log("Engine", f"ScreenWire AI engine started — project: {PROJECT_DIR}")

    yield

    # Shutdown
    if ENABLE_MANIFEST_QUEUE:
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
    if reconciler.manifest_path.exists():
        reconciler.manifest = json.loads(
            reconciler.manifest_path.read_text(encoding="utf-8")
        )
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
    """Reference image generation via cast_image handler (prunaai/p-image + optional upscale)."""
    output = _resolve_output(req.output_path)
    cast_id = req.cast_id or output.stem

    handler = get_handler(
        "cast_image",
        replicate_token=REPLICATE_API_TOKEN,
        xai_key=XAI_API_KEY,
        http_client=http_client,
    )
    try:
        with activate_run_context(run_id=req.run_id or "", phase=req.phase):
            result = await handler.generate(CastImageInput(
                cast_id=cast_id,
                prompt=req.prompt,
                media_style=req.media_style or "",
                output_dir=PROJECT_DIR,
                seed=req.seed,
                run_id=req.run_id,
                phase=req.phase,
            ))
    finally:
        await handler.close()

    if not result.success:
        emit_event(
            PROJECT_DIR,
            event="asset_generation_failed",
            level="ERROR",
            run_id=req.run_id or "",
            phase=req.phase,
            asset_id=cast_id,
            handler="cast_image",
            details={"error": result.error, "model": result.model_used},
        )
        error_detail = result.error_detail or {"error": result.error, "failure_type": "MODEL_ERROR"}
        raise HTTPException(status_code=502, detail=error_detail)

    # Move handler output to the requested location if different
    if result.image_path and result.image_path.resolve() != output.resolve():
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(result.image_path), str(output))

    emit_event(
        PROJECT_DIR,
        event="asset_generated",
        run_id=req.run_id or "",
        phase=req.phase,
        asset_id=cast_id,
        handler="cast_image",
        details={"path": str(output), "model": result.model_used, "upscaled": result.upscaled},
    )
    return {
        "success": True,
        "path": str(output),
        "seed": req.seed,
        "prediction_id": "",
        "model": result.model_used,
        "upscaled": result.upscaled,
    }


# ---------------------------------------------------------------------------
# Layer 1: POST /internal/generate-frame
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Fallback chain: nano-banana-2 → nano-banana-pro → nano-banana
# ---------------------------------------------------------------------------

IMAGE_MODEL_CHAIN = [
    "google/nano-banana-2",
    "google/nano-banana-pro",
]


def _adapt_input_for_model(model: str, base_input: dict[str, Any]) -> dict[str, Any]:
    """Adapt prediction input params for each model's supported schema.

    nano-banana-2:  prompt, image_input, aspect_ratio, resolution, google_search, image_search, output_format
    nano-banana-pro: prompt, image_input, aspect_ratio, resolution, safety_filter_level, allow_fallback_model, output_format
    nano-banana:     prompt, image_input, aspect_ratio, output_format
    """
    inp = dict(base_input)
    if model == "google/nano-banana-2":
        # nano-banana-2 supports google_search + image_search natively
        # Strip params it doesn't know about
        inp.pop("safety_filter_level", None)
        inp.pop("allow_fallback_model", None)
    elif model == "google/nano-banana-pro":
        # Pro has safety_filter_level but NOT google_search/image_search
        inp.pop("google_search", None)
        inp.pop("image_search", None)
        inp.setdefault("safety_filter_level", "block_only_high")
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
    max_retries = 3
    last_error = None
    last_error_status: int | None = None

    for model in IMAGE_MODEL_CHAIN:
        adapted = _adapt_input_for_model(model, pred_input)
        succeeded = False
        for attempt in range(1, max_retries + 1):
            try:
                log("Fallback", f"Trying {model}..." + (f" (attempt {attempt})" if attempt > 1 else ""))
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
                last_error_status = exc.response.status_code
                if exc.response.status_code in (502, 503, 429) and attempt < max_retries:
                    await asyncio.sleep(5 * attempt)
                    continue
                break

            prediction_id = pred_data.get("id", "")
            if pred_data.get("status") != "succeeded":
                pred_data = await _poll_replicate_prediction(prediction_id, headers)

            if pred_data.get("status") != "succeeded":
                error_detail = _build_prediction_error(pred_data, prompt)
                err_type = error_detail.get("failure_type", "UNKNOWN")
                err_msg = str(pred_data.get("error", ""))
                log("Fallback", f"{model} failed: {err_type}")
                _log_composition(
                    output_path=str(output), prompt=prompt, model=model,
                    prediction_id=prediction_id, reference_images=reference_images, success=False,
                    aspect_ratio=aspect_ratio, error=err_type,
                    duration_ms=int((_time.monotonic() - t0) * 1000),
                )
                last_error = error_detail
                last_error_status = 503 if error_detail.get("failure_type") == "UPSTREAM_TRANSIENT" else 502
                if _is_retryable_prediction_failure(error_detail, err_msg) and attempt < max_retries:
                    await asyncio.sleep(5 * attempt)
                    continue
                break
            succeeded = True
            break

        if not succeeded and _can_use_pro_capacity_rescue(model, adapted, last_error if isinstance(last_error, dict) else {}):
            rescue_input = _build_pro_capacity_rescue_input(adapted)
            log("Fallback", f"{model} transient 4K failure -> retrying with 2K + allow_fallback_model=true")
            try:
                pred_data = await _replicate_predict(model, rescue_input, headers)
                prediction_id = pred_data.get("id", "")
                if pred_data.get("status") != "succeeded":
                    pred_data = await _poll_replicate_prediction(prediction_id, headers)
                if pred_data.get("status") == "succeeded":
                    succeeded = True
                    log("Fallback", f"{model} rescue path succeeded")
                else:
                    error_detail = _build_prediction_error(pred_data, prompt)
                    last_error = error_detail
                    last_error_status = 503 if error_detail.get("failure_type") == "UPSTREAM_TRANSIENT" else 502
                    log("Fallback", f"{model} rescue path failed: {error_detail.get('failure_type', 'UNKNOWN')}")
            except httpx.HTTPStatusError as exc:
                last_error = exc
                last_error_status = 503 if exc.response.status_code in (429, 502, 503) else exc.response.status_code
                log("Fallback", f"{model} rescue HTTP error: {exc.response.status_code}")
        if not succeeded:
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
        if last_error.response.status_code in (429, 502, 503):
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "Replicate image service is temporarily unavailable",
                    "failure_type": "UPSTREAM_TRANSIENT",
                    "is_retryable": True,
                    "upstream_status": last_error.response.status_code,
                },
            )
        raise HTTPException(status_code=last_error.response.status_code, detail=last_error.response.text)
    raise HTTPException(
        status_code=last_error_status or 502,
        detail=last_error if isinstance(last_error, dict) else {"error": "All models failed"},
    )


# ---------------------------------------------------------------------------
# Layer 1: POST /internal/generate-frame (nano-banana-2 + fallback chain)
# ---------------------------------------------------------------------------

class GenerateFrameRequest(BaseModel):
    prompt: str
    size: str = "landscape_16_9"
    output_path: str
    seed: Optional[int] = None
    output_format: str = "png"
    reference_images: list[str] = Field(default_factory=list)  # paths to cast/location/prop ref images
    storyboard_image: Optional[str] = None  # explicit storyboard cell path (PRIMARY composition ref)
    frame_id: Optional[str] = ""  # handler: identifies frame for output naming and ledger
    run_id: Optional[str] = None
    phase: str = ""
    sensitive_context: bool = False


@app.post("/internal/generate-frame")
async def generate_frame(req: GenerateFrameRequest):
    """Frame generation via frame handler (nano-banana-2 → nano-banana-pro, 4K, capacity rescue)."""
    output = _resolve_output(req.output_path)
    frame_id = req.frame_id or output.stem

    def _resolve_ref_path(ref: str) -> Path | None:
        p = Path(ref)
        if not p.is_absolute():
            p = PROJECT_DIR / p
        return p if p.exists() else None

    # Separate storyboard cell from generic reference images.
    # Priority: explicit storyboard_image field > auto-detect from reference_images[0]
    # (resolve_ref_images always puts the storyboard cell/composite first).
    storyboard_path: Path | None = None
    remaining_refs: list[str] = list(req.reference_images)

    if req.storyboard_image:
        storyboard_path = _resolve_ref_path(req.storyboard_image)
        if not storyboard_path:
            log("FrameGen", f"WARNING: Storyboard image not found: {req.storyboard_image}")
    elif remaining_refs and "storyboards/" in remaining_refs[0].replace("\\", "/"):
        # Auto-extract: first ref is a storyboard cell/composite
        storyboard_path = _resolve_ref_path(remaining_refs.pop(0))
        if not storyboard_path:
            log("FrameGen", "WARNING: Auto-detected storyboard ref not found on disk")

    ref_paths: list[Path] = []
    for ref in remaining_refs:
        p = _resolve_ref_path(ref)
        if p:
            ref_paths.append(p)
        else:
            log("FrameGen", f"WARNING: Reference image not found: {ref}")

    handler = get_handler(
        "frame",
        replicate_token=REPLICATE_API_TOKEN,
        xai_key=XAI_API_KEY,
        http_client=http_client,
    )
    try:
        with activate_run_context(run_id=req.run_id or "", phase=req.phase):
            result = await handler.generate(FrameInput(
                frame_id=frame_id,
                prompt=req.prompt,
                reference_images=ref_paths,
                storyboard_image=storyboard_path,
                output_dir=PROJECT_DIR,
                seed=req.seed,
                output_format=req.output_format if req.output_format in ("jpg", "png") else "png",
                run_id=req.run_id,
                phase=req.phase,
                sensitive_context=req.sensitive_context,
            ))
    finally:
        await handler.close()

    if not result.success:
        emit_event(
            PROJECT_DIR,
            event="frame_generation_failed",
            level="ERROR",
            run_id=req.run_id or "",
            phase=req.phase,
            frame_id=frame_id,
            handler="frame",
            details={"error": result.error, "model": result.model_used},
        )
        error_detail = result.error_detail or {"error": result.error, "failure_type": "MODEL_ERROR"}
        raise HTTPException(status_code=502, detail=error_detail)

    # Move handler output to the requested location if different
    if result.image_path and result.image_path.resolve() != output.resolve():
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(result.image_path), str(output))

    emit_event(
        PROJECT_DIR,
        event="frame_generated",
        run_id=req.run_id or "",
        phase=req.phase,
        frame_id=frame_id,
        handler="frame",
        details={
            "path": str(output),
            "model": result.model_used,
            "downshifted": result.downshifted,
            "reference_count": len(ref_paths) + (1 if storyboard_path else 0),
        },
    )
    return {
        "success": True,
        "path": str(output),
        "seed": req.seed,
        "prediction_id": "",
        "model": result.model_used,
        "downshifted": result.downshifted,
    }


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
    aspect_ratio = aspect_map.get(req.size, "16:9")

    pred_input: dict[str, Any] = {
        "prompt": req.prompt,
        "image_input": [source_uri],
        "aspect_ratio": aspect_ratio,
        "resolution": "4K",
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

    with activate_run_context(run_id=req.run_id or "", phase=req.phase):
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
    aspect_ratio = aspect_map.get(req.size, "16:9")

    pred_input: dict[str, Any] = {
        "prompt": req.prompt,
        "aspect_ratio": aspect_ratio,
        "resolution": "4K",
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

    with activate_run_context(run_id=req.run_id or "", phase=req.phase):
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
# Layer 1: POST /internal/generate-video
# ---------------------------------------------------------------------------

@app.post("/internal/generate-video")
async def generate_video(req: GenerateVideoRequest):
    """Video generation via video_clip handler (xai/grok-imagine-video)."""
    output = _resolve_output(req.output_path)
    frame_id = req.frame_id or output.stem

    # Resolve frame image path
    frame_image: Path | None = None
    if req.image_path:
        frame_image = Path(req.image_path)
        if not frame_image.is_absolute():
            frame_image = PROJECT_DIR / frame_image

    if not frame_image or not frame_image.exists():
        raise HTTPException(
            status_code=400,
            detail="image_path is required and must exist for video generation",
        )

    handler = get_handler(
        "video_clip",
        replicate_token=REPLICATE_API_TOKEN,
        xai_key=XAI_API_KEY,
        http_client=http_client,
    )
    try:
        with activate_run_context(run_id=req.run_id or "", phase=req.phase):
            result = await handler.generate(VideoClipInput(
                frame_id=frame_id,
                dialogue_text=req.dialogue_text or "",
                motion_prompt=req.prompt,
                frame_image_path=frame_image,
                suggested_duration=req.duration,
                output_dir=PROJECT_DIR,
                run_id=req.run_id,
                phase=req.phase,
            ))
    finally:
        await handler.close()

    if not result.success:
        emit_event(
            PROJECT_DIR,
            event="video_generation_failed",
            level="ERROR",
            run_id=req.run_id or "",
            phase=req.phase,
            frame_id=frame_id,
            handler="video_clip",
            details={"error": result.error, "model": result.model_used},
        )
        error_detail = result.error_detail or {"error": result.error, "failure_type": "MODEL_ERROR"}
        if isinstance(error_detail, dict):
            error_detail = dict(error_detail)
            if result.error:
                error_detail.setdefault("error", result.error)
            if result.model_used:
                error_detail.setdefault("model", result.model_used)
        raise HTTPException(status_code=502, detail=error_detail)

    # Move handler output to the requested location if different
    if result.video_path and result.video_path.resolve() != output.resolve():
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(result.video_path), str(output))

    emit_event(
        PROJECT_DIR,
        event="video_generated",
        run_id=req.run_id or "",
        phase=req.phase,
        frame_id=frame_id,
        handler="video_clip",
        details={"path": str(output), "model": result.model_used, "duration": result.duration},
    )
    return {
        "success": True,
        "path": str(output),
        "prediction_id": "",
        "model": result.model_used,
        "duration": result.duration,
    }


# ---------------------------------------------------------------------------
# Layer 1: POST /internal/generate-location-grid
# ---------------------------------------------------------------------------

class GenerateLocationGridRequest(BaseModel):
    prompt: str
    location_id: str
    output_path: str
    seed: Optional[int] = None
    output_format: str = "png"
    template_type: str = "exterior"  # Preset template key (see location_grid.py)
    media_style: str = ""  # Optional style instructions for prompt
    run_id: Optional[str] = None
    phase: str = ""
    sensitive_context: bool = False


@app.post("/internal/generate-location-grid")
async def generate_location_grid(req: GenerateLocationGridRequest):
    """Location reference grid via location_grid handler (nano-banana-pro, 16:9, 2K, no fallback)."""
    output = _resolve_output(req.output_path)

    handler = get_handler(
        "location_grid",
        replicate_token=REPLICATE_API_TOKEN,
        xai_key=XAI_API_KEY,
        http_client=http_client,
    )
    try:
        with activate_run_context(run_id=req.run_id or "", phase=req.phase):
            result = await handler.generate(LocationGridInput(
                location_id=req.location_id,
                prompt=req.prompt,
                template_type=req.template_type,
                media_style=req.media_style,
                output_dir=PROJECT_DIR,
                seed=req.seed,
                output_format=req.output_format,
                run_id=req.run_id,
                phase=req.phase,
                sensitive_context=req.sensitive_context,
            ))
    finally:
        await handler.close()

    if not result.success:
        emit_event(
            PROJECT_DIR,
            event="location_generation_failed",
            level="ERROR",
            run_id=req.run_id or "",
            phase=req.phase,
            asset_id=req.location_id,
            handler="location_grid",
            details={"error": result.error, "model": result.model_used},
        )
        error_detail = result.error_detail or {"error": result.error, "failure_type": "MODEL_ERROR"}
        raise HTTPException(status_code=502, detail=error_detail)

    # Move handler output to the requested location if different
    if result.image_path and result.image_path.resolve() != output.resolve():
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(result.image_path), str(output))

    emit_event(
        PROJECT_DIR,
        event="location_generated",
        run_id=req.run_id or "",
        phase=req.phase,
        asset_id=req.location_id,
        handler="location_grid",
        details={"path": str(output), "model": result.model_used, "resolution": result.resolution},
    )
    return {
        "success": True,
        "path": str(output),
        "seed": req.seed,
        "prediction_id": "",
        "model": result.model_used,
        "resolution": result.resolution,
    }


# ---------------------------------------------------------------------------
# Layer 1: POST /internal/generate-storyboard
# ---------------------------------------------------------------------------

class GenerateStoryboardRequest(BaseModel):
    prompt: str
    grid_id: str
    layout: str = "2x2"
    output_path: str
    reference_images: list[str] = Field(default_factory=list)
    frame_ids: list[str] = Field(default_factory=list)
    seed: Optional[int] = None
    output_format: str = "png"
    run_id: Optional[str] = None
    phase: str = ""
    sensitive_context: bool = False


@app.post("/internal/generate-storyboard")
async def generate_storyboard(req: GenerateStoryboardRequest):
    """Storyboard composite via storyboard handler (nano-banana-2 → pro, cell extraction)."""
    output = _resolve_output(req.output_path)

    # Resolve reference image paths
    ref_paths: list[Path] = []
    for ref in req.reference_images:
        p = Path(ref)
        if not p.is_absolute():
            p = PROJECT_DIR / p
        if p.exists():
            ref_paths.append(p)

    handler = get_handler(
        "storyboard",
        replicate_token=REPLICATE_API_TOKEN,
        xai_key=XAI_API_KEY,
        http_client=http_client,
    )
    try:
        with activate_run_context(run_id=req.run_id or "", phase=req.phase):
            result = await handler.generate(StoryboardInput(
                grid_id=req.grid_id,
                prompt=req.prompt,
                reference_images=ref_paths,
                layout=req.layout,
                frame_ids=req.frame_ids,
                output_dir=PROJECT_DIR,
                seed=req.seed,
                output_format=req.output_format,
                run_id=req.run_id,
                phase=req.phase,
                sensitive_context=req.sensitive_context,
            ))
    finally:
        await handler.close()

    if not result.success:
        emit_event(
            PROJECT_DIR,
            event="storyboard_generation_failed",
            level="ERROR",
            run_id=req.run_id or "",
            phase=req.phase,
            asset_id=req.grid_id,
            handler="storyboard",
            details={"error": result.error, "model": result.model_used},
        )
        error_detail = result.error_detail or {"error": result.error, "failure_type": "MODEL_ERROR"}
        raise HTTPException(status_code=502, detail=error_detail)

    # Move composite to the requested location if different
    if result.composite_path and result.composite_path.resolve() != output.resolve():
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(result.composite_path), str(output))

    emit_event(
        PROJECT_DIR,
        event="storyboard_generated",
        run_id=req.run_id or "",
        phase=req.phase,
        asset_id=req.grid_id,
        handler="storyboard",
        details={
            "composite_path": str(output),
            "cell_count": len(result.cell_paths),
            "model": result.model_used,
        },
    )
    return {
        "success": True,
        "composite_path": str(output),
        "cell_paths": [str(p) for p in result.cell_paths],
        "grid_id": result.grid_id,
        "model": result.model_used,
        "resolution": result.resolution,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Batch Generation Endpoints
#  Semaphore-capped at 10 concurrent. Individual failures are graceful.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class BatchGenerateImageRequest(BaseModel):
    items: list[GenerateImageRequest]
    max_concurrent: int = Field(default=10, ge=1, le=10)


class BatchGenerateFrameRequest(BaseModel):
    items: list[GenerateFrameRequest]
    max_concurrent: int = Field(default=10, ge=1, le=10)


class BatchGenerateVideoRequest(BaseModel):
    items: list[GenerateVideoRequest]
    max_concurrent: int = Field(default=10, ge=1, le=10)


class BatchGenerateLocationGridRequest(BaseModel):
    items: list[GenerateLocationGridRequest]
    max_concurrent: int = Field(default=10, ge=1, le=10)


class BatchGenerateStoryboardRequest(BaseModel):
    items: list[GenerateStoryboardRequest]
    max_concurrent: int = Field(default=10, ge=1, le=10)


@app.post("/internal/batch-generate-image")
async def batch_generate_image(req: BatchGenerateImageRequest):
    """Batch cast image generation — up to 10 concurrent. Graceful per-item failures."""
    handler = get_handler(
        "cast_image", replicate_token=REPLICATE_API_TOKEN, http_client=http_client,
    )
    try:
        inputs = []
        for item in req.items:
            output = _resolve_output(item.output_path)
            cast_id = item.cast_id or output.stem
            inputs.append(CastImageInput(
                cast_id=cast_id,
                prompt=item.prompt,
                media_style=item.media_style or "",
                output_dir=PROJECT_DIR,
                seed=item.seed,
            ))
        batch = await handler.generate_batch(inputs, req.max_concurrent)
    finally:
        await handler.close()

    results: list[dict[str, Any]] = []
    for item, result in zip(req.items, batch.results):
        output = _resolve_output(item.output_path)
        entry: dict[str, Any] = {"success": result.success, "path": str(output)}
        if result.success:
            if result.image_path and result.image_path.resolve() != output.resolve():
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(result.image_path), str(output))
            entry["model"] = result.model_used
            entry["upscaled"] = getattr(result, "upscaled", False)
        else:
            entry["error"] = result.error
            entry["error_detail"] = result.error_detail
        results.append(entry)

    return {
        "results": results,
        "total": batch.total,
        "succeeded": batch.succeeded,
        "failed": batch.failed,
    }


@app.post("/internal/batch-generate-frame")
async def batch_generate_frame(req: BatchGenerateFrameRequest):
    """Batch frame generation — up to 10 concurrent. Graceful per-item failures."""
    handler = get_handler(
        "frame", replicate_token=REPLICATE_API_TOKEN, http_client=http_client,
    )
    try:
        def _resolve_batch_ref(ref: str) -> Path | None:
            p = Path(ref)
            if not p.is_absolute():
                p = PROJECT_DIR / p
            return p if p.exists() else None

        inputs = []
        for item in req.items:
            output = _resolve_output(item.output_path)
            frame_id = item.frame_id or output.stem

            # Separate storyboard from generic refs (mirrors single-frame logic)
            item_storyboard: Path | None = None
            remaining: list[str] = list(item.reference_images)
            if item.storyboard_image:
                item_storyboard = _resolve_batch_ref(item.storyboard_image)
            elif remaining and "storyboards/" in remaining[0].replace("\\", "/"):
                item_storyboard = _resolve_batch_ref(remaining.pop(0))

            ref_paths: list[Path] = [p for ref in remaining if (p := _resolve_batch_ref(ref))]
            inputs.append(FrameInput(
                frame_id=frame_id,
                prompt=item.prompt,
                reference_images=ref_paths,
                storyboard_image=item_storyboard,
                output_dir=PROJECT_DIR,
                seed=item.seed,
                output_format=item.output_format if item.output_format in ("jpg", "png") else "png",
                run_id=item.run_id,
                phase=item.phase,
            ))
        batch = await handler.generate_batch(inputs, req.max_concurrent)
    finally:
        await handler.close()

    results: list[dict[str, Any]] = []
    for item, result in zip(req.items, batch.results):
        output = _resolve_output(item.output_path)
        entry: dict[str, Any] = {"success": result.success, "path": str(output)}
        if result.success:
            if result.image_path and result.image_path.resolve() != output.resolve():
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(result.image_path), str(output))
            entry["model"] = result.model_used
            entry["downshifted"] = getattr(result, "downshifted", False)
        else:
            entry["error"] = result.error
            entry["error_detail"] = result.error_detail
        results.append(entry)

    return {
        "results": results,
        "total": batch.total,
        "succeeded": batch.succeeded,
        "failed": batch.failed,
    }


@app.post("/internal/batch-generate-video")
async def batch_generate_video(req: BatchGenerateVideoRequest):
    """Batch video clip generation — up to 10 concurrent. Graceful per-item failures."""
    handler = get_handler(
        "video_clip", replicate_token=REPLICATE_API_TOKEN, http_client=http_client,
    )
    try:
        inputs = []
        for item in req.items:
            output = _resolve_output(item.output_path)
            frame_id = item.frame_id or output.stem
            frame_image: Path | None = None
            if item.image_path:
                frame_image = Path(item.image_path)
                if not frame_image.is_absolute():
                    frame_image = PROJECT_DIR / frame_image
            inputs.append(VideoClipInput(
                frame_id=frame_id,
                dialogue_text=item.dialogue_text or "",
                motion_prompt=item.prompt,
                frame_image_path=frame_image,
                suggested_duration=item.duration,
                output_dir=PROJECT_DIR,
                run_id=item.run_id,
                phase=item.phase,
            ))
        batch = await handler.generate_batch(inputs, req.max_concurrent)
    finally:
        await handler.close()

    results: list[dict[str, Any]] = []
    for item, result in zip(req.items, batch.results):
        output = _resolve_output(item.output_path)
        entry: dict[str, Any] = {"success": result.success, "path": str(output)}
        if result.success:
            if result.video_path and result.video_path.resolve() != output.resolve():
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(result.video_path), str(output))
            entry["model"] = result.model_used
            entry["duration"] = getattr(result, "duration", 0)
        else:
            entry["error"] = result.error
            entry["error_detail"] = result.error_detail
        results.append(entry)

    return {
        "results": results,
        "total": batch.total,
        "succeeded": batch.succeeded,
        "failed": batch.failed,
    }


@app.post("/internal/batch-generate-location-grid")
async def batch_generate_location_grid(req: BatchGenerateLocationGridRequest):
    """Batch location grid generation — up to 10 concurrent. Graceful per-item failures."""
    handler = get_handler(
        "location_grid", replicate_token=REPLICATE_API_TOKEN, http_client=http_client,
    )
    try:
        inputs = []
        for item in req.items:
            inputs.append(LocationGridInput(
                location_id=item.location_id,
                prompt=item.prompt,
                template_type=item.template_type,
                media_style=item.media_style,
                output_dir=PROJECT_DIR,
                seed=item.seed,
                output_format=item.output_format,
                run_id=item.run_id,
                phase=item.phase,
            ))
        batch = await handler.generate_batch(inputs, req.max_concurrent)
    finally:
        await handler.close()

    results: list[dict[str, Any]] = []
    for item, result in zip(req.items, batch.results):
        output = _resolve_output(item.output_path)
        entry: dict[str, Any] = {"success": result.success, "path": str(output)}
        if result.success:
            if result.image_path and result.image_path.resolve() != output.resolve():
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(result.image_path), str(output))
            entry["model"] = result.model_used
            entry["resolution"] = getattr(result, "resolution", "")
        else:
            entry["error"] = result.error
            entry["error_detail"] = result.error_detail
        results.append(entry)

    return {
        "results": results,
        "total": batch.total,
        "succeeded": batch.succeeded,
        "failed": batch.failed,
    }


@app.post("/internal/batch-generate-storyboard")
async def batch_generate_storyboard(req: BatchGenerateStoryboardRequest):
    """Batch storyboard generation — up to 10 concurrent. Graceful per-item failures."""
    handler = get_handler(
        "storyboard", replicate_token=REPLICATE_API_TOKEN, http_client=http_client,
    )
    try:
        inputs = []
        for item in req.items:
            ref_paths: list[Path] = []
            for ref in item.reference_images:
                p = Path(ref)
                if not p.is_absolute():
                    p = PROJECT_DIR / p
                if p.exists():
                    ref_paths.append(p)
            inputs.append(StoryboardInput(
                grid_id=item.grid_id,
                prompt=item.prompt,
                reference_images=ref_paths,
                layout=item.layout,
                frame_ids=item.frame_ids,
                output_dir=PROJECT_DIR,
                seed=item.seed,
                output_format=item.output_format,
                run_id=item.run_id,
                phase=item.phase,
            ))
        batch = await handler.generate_batch(inputs, req.max_concurrent)
    finally:
        await handler.close()

    results: list[dict[str, Any]] = []
    for item, result in zip(req.items, batch.results):
        output = _resolve_output(item.output_path)
        entry: dict[str, Any] = {"success": result.success}
        if result.success:
            if result.composite_path and result.composite_path.resolve() != output.resolve():
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(result.composite_path), str(output))
            entry["composite_path"] = str(output)
            entry["cell_paths"] = [str(p) for p in getattr(result, "cell_paths", [])]
            entry["grid_id"] = getattr(result, "grid_id", "")
            entry["model"] = result.model_used
            entry["resolution"] = getattr(result, "resolution", "")
        else:
            entry["composite_path"] = str(output)
            entry["error"] = result.error
            entry["error_detail"] = result.error_detail
        results.append(entry)

    return {
        "results": results,
        "total": batch.total,
        "succeeded": batch.succeeded,
        "failed": batch.failed,
    }


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
# Layer 1: POST /internal/refine-video-prompts
# ---------------------------------------------------------------------------

class RefineVideoPromptsRequest(BaseModel):
    frame_ids: Optional[list[str]] = None  # None = all frames
    concurrency: int = 3


@app.post("/internal/refine-video-prompts")
async def api_refine_video_prompts(req: RefineVideoPromptsRequest):
    """Run Grok vision refinement on video prompts.

    Reads each video prompt JSON, sends the frame image + graph prompt
    to grok-2-vision, and writes back a refined prompt grounded in what
    the frame actually shows.
    """
    from graph.frame_prompt_refiner import refine_video_prompt, refine_all_video_prompts

    if req.frame_ids:
        # Refine specific frames
        import json as _json
        prompt_dir = PROJECT_DIR / "video" / "prompts"
        results = {"refined": 0, "skipped": 0, "failed": 0}
        for fid in req.frame_ids:
            jp = prompt_dir / f"{fid}_video.json"
            if not jp.exists():
                results["skipped"] += 1
                continue
            data = _json.loads(jp.read_text(encoding="utf-8"))
            if data.get("refined_by") == "grok-vision":
                results["skipped"] += 1
                continue
            refined = await refine_video_prompt(data, PROJECT_DIR)
            jp.write_text(
                _json.dumps(refined, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            if refined.get("refined_by") == "grok-vision":
                results["refined"] += 1
            elif "failed" in refined.get("refined_by", ""):
                results["failed"] += 1
            else:
                results["skipped"] += 1
        return results
    else:
        return await refine_all_video_prompts(
            PROJECT_DIR, concurrency=req.concurrency
        )


# ---------------------------------------------------------------------------
# Internal helpers: Replicate prediction error reporting
# ---------------------------------------------------------------------------

# Known Replicate error codes and their meanings
_REPLICATE_ERROR_CODES = {
    "E005": "NSFW/safety filter triggered — the prompt or generated output was flagged as sensitive content",
    "E004": "Model timeout — prediction took too long",
    "E003": "Service unavailable or model at capacity — retry later",
    "PA": "Prediction interrupted before execution — retry later",
}


def _extract_replicate_error_code(error_msg: str) -> str:
    """Extract Replicate's symbolic error code from an error message."""
    error_msg = error_msg or ""
    import re as _re

    code_match = _re.search(r"\((E\d+)\)", error_msg)
    if code_match:
        return code_match.group(1)

    pa_match = _re.search(r"\(code:\s*([A-Z]+)\)", error_msg)
    if pa_match:
        return pa_match.group(1)

    return "UNKNOWN"


def _classify_replicate_error(error_msg: str, logs: str) -> dict:
    """Parse Replicate error message and logs to classify the failure type."""
    error_msg = error_msg or ""
    logs = logs or ""
    error_code = _extract_replicate_error_code(error_msg)

    # Detect specific failure types
    is_safety = ("NSFW" in logs or "flagged as sensitive" in error_msg or error_code == "E005"
                 or "Content Blocked" in error_msg or "IMAGE_SAFETY" in error_msg
                 or "blockReason" in error_msg)
    is_timeout = "timeout" in error_msg.lower() or error_code == "E004"
    is_capacity = ("high demand" in error_msg.lower() or "at capacity" in error_msg.lower()
                   or error_code in {"E003", "PA"})
    is_retryable_transient = ("please retry" in error_msg.lower() or is_capacity)

    if is_safety:
        failure_type = "SAFETY_FILTER"
    elif is_timeout:
        failure_type = "TIMEOUT"
    elif is_retryable_transient:
        failure_type = "UPSTREAM_TRANSIENT"
    else:
        failure_type = "MODEL_ERROR"

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
        "is_retryable": is_safety or is_retryable_transient,
        "rephrase_hints": trigger_hints,
    }


def _is_retryable_prediction_failure(error_detail: dict, error_msg: str) -> bool:
    """Return True when Replicate explicitly indicates the prediction should be retried."""
    return bool(error_detail.get("is_retryable")) or "please retry" in (error_msg or "").lower()


def _can_use_pro_capacity_rescue(model: str, pred_input: dict[str, Any], error_detail: dict) -> bool:
    """Use Replicate Pro's fallback model path only for transient 4K failures."""
    return (
        model == "google/nano-banana-pro"
        and pred_input.get("resolution") == "4K"
        and error_detail.get("failure_type") == "UPSTREAM_TRANSIENT"
    )


def _build_pro_capacity_rescue_input(pred_input: dict[str, Any]) -> dict[str, Any]:
    """Downshift a 4K Pro request so Replicate can use its fallback capacity path."""
    rescue = dict(pred_input)
    rescue["resolution"] = "2K"
    rescue["allow_fallback_model"] = True
    rescue.setdefault("safety_filter_level", "block_only_high")
    return rescue


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
        "run_id": current_run_id() or None,
        "phase": current_phase() or None,
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
        "run_id": current_run_id() or None,
        "phase": current_phase() or None,
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
        emit_event(
            PROJECT_DIR,
            event="prediction_failure",
            level="ERROR",
            run_id=report.get("run_id") or "",
            phase=report.get("phase") or "",
            handler=model,
            details={
                "prediction_id": prediction_id,
                "failure_type": classification["failure_type"],
                "error_code": classification["error_code"],
                "failure_path": str(failure_path),
            },
        )
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
