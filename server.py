"""
ScreenWire AI — FastAPI Core Engine (MVP single-file server)
Headless pipeline backend: manifest reconciliation, file sentinel,
agent process management, and Layer 1 programmatic gateways.
"""

import asyncio
import atexit
import base64
import hashlib
import json
import math
import mimetypes
import os
import re
import shutil
import signal
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
import httpx
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field
from tenacity import (
    retry,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from llm.xai_client import DEFAULT_REASONING_MODEL, XAIClient
from llm.project_tools import build_project_tools, make_project_tool_executor
from llm.xai_client import SyncXAIClient
from telemetry import activate_run_context, current_phase, current_run_id, emit_event
from workspace_api import (
    append_ui_event,
    build_frame_context,
    build_workspace_snapshot,
    classify_ui_gate,
    clear_pipeline_invalidations,
    clear_review_entity_changes,
    create_graph_node,
    delete_graph_node,
    dirty_pipeline_phases,
    entity_upload_path,
    frame_upload_path,
    get_graph_node,
    graph_collection_name,
    load_pipeline_invalidations,
    load_review_entity_changes,
    load_json,
    load_timeline_overrides,
    load_ui_phase_report,
    load_workspace_state,
    mark_pipeline_invalidation,
    mark_project_file_change,
    pipeline_artifact_progress,
    rewind_manifest_phases,
    attach_entity_image,
    patch_graph_node,
    save_timeline_overrides,
    save_workspace_state,
    write_ui_phase_report,
)
from supabase_persistence import (
    get_supabase_persistence,
    should_persist_rel_path,
)

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
SCREENWIRE_EXECUTION_MODE = str(os.getenv("SCREENWIRE_EXECUTION_MODE") or "local").strip().lower()
THUMBNAIL_CACHE_DIR = ".cache/thumbnails"
THUMBNAIL_SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
THUMBNAIL_FORMAT_MAP = {
    "webp": ("WEBP", ".webp"),
    "jpg": ("JPEG", ".jpg"),
    "jpeg": ("JPEG", ".jpg"),
    "png": ("PNG", ".png"),
}


def log(module: str, message: str) -> None:
    print(f"[{datetime.now().isoformat()}] [{module}] {message}")


def _queue_execution_enabled() -> bool:
    return SCREENWIRE_EXECUTION_MODE in {"queue", "worker", "supabase"} and get_supabase_persistence(http_client) is not None


def _parse_asset_event(path: Path) -> dict[str, Any] | None:
    try:
        rel_path = path.relative_to(PROJECT_DIR).as_posix()
    except ValueError:
        return None

    suffix = path.suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".mp4", ".mov", ".webm"}:
        return None

    url = _project_file_url(rel_path)
    if rel_path.startswith("frames/composed/"):
        frame_id = path.stem.removesuffix("_gen")
        return {"type": "frame_generated", "data": {"frameId": frame_id, "imageUrl": url}}
    if rel_path.startswith("cast/composites/"):
        entity_id = path.stem.removesuffix("_ref")
        return {"type": "entity_image_generated", "data": {"entityId": entity_id, "imageUrl": url}}
    if rel_path.startswith("locations/primary/") or rel_path.startswith("props/generated/"):
        return {"type": "entity_image_generated", "data": {"entityId": path.stem, "imageUrl": url}}
    if rel_path.startswith("video/clips/"):
        frame_id = path.stem
        return {"type": "storyboard_generated", "data": {"frameId": frame_id, "imageUrl": url}}
    return None


def _record_project_asset_event(path: Path) -> None:
    global project_asset_revision, project_asset_event
    project_asset_revision += 1
    project_asset_event = _parse_asset_event(path)


def _notify_project_asset_event(path: Path) -> None:
    if server_loop is None:
        return
    try:
        server_loop.call_soon_threadsafe(_record_project_asset_event, path)
    except RuntimeError:
        return


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
    def _handle_path(self, path: Path) -> None:
        if path.suffix == ".tmp":
            return
        try:
            if path.stat().st_size == 0:
                return
        except OSError:
            return
        log("Sentinel", f"New file: {path}")
        _notify_project_asset_event(path)

    def on_created(self, event) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        self._handle_path(Path(event.src_path))

    def on_moved(self, event) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        self._handle_path(Path(event.dest_path))


class _ImageTagHandler(FileSystemEventHandler):
    """Auto-tags images with entity names when they appear in watched directories."""

    def __init__(self, entity_type: str, project_dir: Path) -> None:
        self.entity_type = entity_type
        self.project_dir = project_dir

    def _handle_path(self, path: Path) -> None:
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
            tag_image(path, label, self.project_dir)
        except Exception as exc:
            log("ImageTagger", f"Failed to tag {path.name}: {exc}")
        _notify_project_asset_event(path)

    def on_created(self, event) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        self._handle_path(Path(event.src_path))

    def on_moved(self, event) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        self._handle_path(Path(event.dest_path))


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
http_client: httpx.AsyncClient | None = None  # initialized in lifespan
ui_pipeline_jobs: dict[str, dict[str, Any]] = {}
server_loop: asyncio.AbstractEventLoop | None = None
project_asset_revision = 0
project_asset_event: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Retry predicate — only retry on 429 / 500 / 503
# ---------------------------------------------------------------------------

def _retryable_status(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 503)
    return False


_gateway_retry = retry(
    retry=retry_if_exception(_retryable_status),
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
    duration: float = 5
    resolution: str = "720p"
    output_path: str
    extra_params: dict[str, Any] = Field(default_factory=dict)
    frame_id: Optional[str] = None       # handler: identifies frame for output naming
    dialogue_text: Optional[str] = None  # handler: prefixed before prompt for lip-sync
    run_id: Optional[str] = None
    phase: str = ""


class ExtendVideoRequest(BaseModel):
    prompt: str = ""
    video_path: str
    duration: float = 5
    output_path: str
    extra_params: dict[str, Any] = Field(default_factory=dict)
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


class UIChatMessageRequest(BaseModel):
    content: str
    mode: str = "suggest"
    focusTarget: Optional[dict[str, Any]] = None
    focusTargets: list[dict[str, Any]] = Field(default_factory=list)


class UIFocusRequest(BaseModel):
    focus: Optional[dict[str, Any]] = None


class UIApprovalRequest(BaseModel):
    gate: str


class UIChangeRequest(BaseModel):
    gate: str
    feedback: str


class UIGraphNodePatchRequest(BaseModel):
    updates: dict[str, Any] = Field(default_factory=dict)


class UIEntityCreateRequest(BaseModel):
    type: str
    name: str
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class UIEntityUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TimelineFrameUpdateRequest(BaseModel):
    duration: Optional[float] = None
    prompt: Optional[str] = None
    dialogueId: Optional[str] = None
    trimStart: Optional[float] = None
    trimEnd: Optional[float] = None


class TimelineExpandRequest(BaseModel):
    direction: str = "after"


class TimelineDialogueUpdateRequest(BaseModel):
    text: Optional[str] = None
    character: Optional[str] = None
    startFrame: Optional[int] = None
    endFrame: Optional[int] = None
    duration: Optional[float] = None


class TimelineFrameEditRequest(BaseModel):
    prompt: str


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client, server_loop
    http_client = httpx.AsyncClient(timeout=None)
    server_loop = asyncio.get_running_loop()
    persistence = get_supabase_persistence(http_client)

    reconciler.load_manifest()
    if ENABLE_MANIFEST_QUEUE:
        reconciler.start_watcher()
        await reconciler.start_writer()
        log("Engine", "Manifest queue reconciler enabled")
    else:
        log("Engine", "Manifest queue reconciler disabled; graph materialization is authoritative")

    # Sentinel
    sentinel.start()
    if persistence is not None:
        try:
            await persistence.ensure_project(PROJECT_DIR, include_graph_snapshot=True)
            log("Engine", f"Supabase persistence enabled for project {PROJECT_DIR.name}")
        except Exception as exc:
            log("Engine", f"Supabase persistence bootstrap failed: {exc}")

    log("Engine", f"ScreenWire AI engine started — project: {PROJECT_DIR}")

    yield

    # Shutdown
    if ENABLE_MANIFEST_QUEUE:
        await reconciler.stop()
    sentinel.stop()
    await agent_mgr.kill_all()
    if persistence is not None:
        await persistence.aclose()
    await http_client.aclose()
    server_loop = None
    log("Engine", "Engine shut down cleanly")


app = FastAPI(title="ScreenWire AI Engine", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def ui_route_audit_middleware(request: Request, call_next):
    path = request.url.path
    is_ui_route = path.startswith("/api/projects/")
    started = datetime.now(timezone.utc)

    try:
        response = await call_next(request)
    except Exception as exc:
        if is_ui_route:
            duration_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000.0
            append_ui_event(
                PROJECT_DIR,
                {
                    "projectId": PROJECT_DIR.name,
                    "method": request.method,
                    "path": path,
                    "gate": classify_ui_gate(path),
                    "statusCode": 500,
                    "ok": False,
                    "durationMs": round(duration_ms, 2),
                    "error": str(exc),
                },
            )
            write_ui_phase_report(PROJECT_DIR.name, PROJECT_DIR)
        raise

    if is_ui_route:
        duration_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000.0
        append_ui_event(
            PROJECT_DIR,
            {
                "projectId": PROJECT_DIR.name,
                "method": request.method,
                "path": path,
                "gate": classify_ui_gate(path),
                "statusCode": response.status_code,
                "ok": response.status_code < 400,
                "durationMs": round(duration_ms, 2),
            },
        )
        write_ui_phase_report(PROJECT_DIR.name, PROJECT_DIR)

    return response


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


def _assert_project(project_id: str) -> Path:
    if project_id != PROJECT_DIR.name:
        raise HTTPException(
            status_code=404,
            detail=f"Project '{project_id}' is not active in this backend session",
        )
    return PROJECT_DIR


def _workspace_snapshot(project_id: str) -> dict[str, Any]:
    _assert_project(project_id)
    snapshot = build_workspace_snapshot(project_id, PROJECT_DIR)
    workers = _local_project_workers_payload()
    snapshot["workers"] = workers

    active_jobs = [
        job
        for job in ui_pipeline_jobs.values()
        if job.get("status") == "running"
        and (job.get("process") is None or job["process"].returncode is None)
    ]
    if active_jobs:
        active_job = max(active_jobs, key=lambda job: int(job.get("activePhase") or 0))
        project = dict(snapshot.get("project") or {})
        project["status"] = _pipeline_status_for_phase(int(active_job.get("activePhase") or 0))
        project["progress"] = max(
            int(project.get("progress") or 0),
            int(active_job.get("progress") or 0),
        )
        snapshot["project"] = project

    return snapshot


async def _workspace_snapshot_async(project_id: str) -> dict[str, Any]:
    snapshot = _workspace_snapshot(project_id)
    workers = await _project_workers_payload_async(project_id)
    snapshot["workers"] = workers

    active_workers = [
        worker
        for worker in workers
        if str(worker.get("status") or "") in {"running", "idle"}
    ]
    if active_workers:
        active_worker = max(active_workers, key=lambda worker: int(worker.get("targetPhase") or 0))
        project = dict(snapshot.get("project") or {})
        project["status"] = _pipeline_status_for_phase(int(active_worker.get("targetPhase") or 0))
        project["progress"] = max(int(project.get("progress") or 0), int(active_worker.get("progress") or 0))
        snapshot["project"] = project

    return snapshot


def _chat_history_path() -> Path:
    path = PROJECT_DIR / "logs" / "ui_chat_history.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_chat_history() -> list[dict[str, Any]]:
    data = load_json(_chat_history_path(), [])
    return data if isinstance(data, list) else []


def _save_chat_history(history: list[dict[str, Any]]) -> None:
    path = _chat_history_path()
    path.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")


def _chat_focus_path() -> Path:
    path = PROJECT_DIR / "logs" / "ui_chat_focus.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _save_chat_focus(focus: dict[str, Any] | None) -> None:
    _chat_focus_path().write_text(json.dumps({"focus": focus}, indent=2) + "\n", encoding="utf-8")


def _workspace_state() -> dict[str, Any]:
    return load_workspace_state(PROJECT_DIR)


def _save_workspace_state_file(state: dict[str, Any]) -> None:
    save_workspace_state(PROJECT_DIR, state)


def _timeline_overrides() -> dict[str, Any]:
    return load_timeline_overrides(PROJECT_DIR)


def _save_timeline_overrides(overrides: dict[str, Any]) -> None:
    save_timeline_overrides(PROJECT_DIR, overrides)


def _timeline_prompt_path(frame_id: str, kind: str) -> Path:
    root = PROJECT_DIR / ("video" if kind == "video" else "frames") / "prompts"
    return root / f"{frame_id}_{kind}.json"


def _normalize_timeline_duration(value: float | int | None) -> float:
    try:
        numeric = float(value if value is not None else 5)
    except Exception:
        numeric = 5.0
    return round(max(2.0, min(15.0, numeric)), 1)


def _normalize_video_request_duration(value: float | int | None) -> int:
    try:
        numeric = float(value if value is not None else 5)
    except Exception:
        numeric = 5.0
    return max(2, min(15, math.ceil(numeric)))


def _normalize_timeline_trim(value: float | int | None) -> float:
    try:
        numeric = float(value if value is not None else 0)
    except Exception:
        numeric = 0.0
    return round(max(0.0, min(15.0, numeric)), 1)


def _timeline_image_rel_from_url(image_url: str | None) -> str | None:
    if not image_url:
        return None
    image_url = str(image_url).split("?", 1)[0]
    project_prefix = "/api/projects/"
    legacy_prefix = "/api/project/file/"
    if str(image_url).startswith(project_prefix):
        parts = str(image_url).split("/", 5)
        if len(parts) >= 6 and parts[4] == "file":
            return parts[5]
    return image_url[len(legacy_prefix):] if str(image_url).startswith(legacy_prefix) else str(image_url)


_VERSIONED_ASSET_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".bmp",
    ".svg",
    ".mp4",
    ".mov",
    ".webm",
}


def _project_file_url(rel_path: str | Path | None) -> str | None:
    if not rel_path:
        return None
    rel = Path(rel_path).as_posix().lstrip("./")
    if not rel:
        return None
    project_id = PROJECT_DIR.name
    url = f"/api/projects/{project_id}/file/{rel}"
    target = (PROJECT_DIR / rel).resolve()
    if target.exists() and target.is_file() and target.suffix.lower() in _VERSIONED_ASSET_SUFFIXES:
        stat = target.stat()
        return f"{url}?v={stat.st_mtime_ns}"
    return url


async def _sync_project_asset_if_needed(project_dir: Path, rel_path: str | Path) -> None:
    normalized = Path(rel_path).as_posix().lstrip("./")
    if not normalized or not should_persist_rel_path(normalized):
        return
    persistence = get_supabase_persistence(http_client)
    if persistence is None:
        return
    target = (project_dir / normalized).resolve()
    if not target.exists() or not target.is_file():
        return
    try:
        await persistence.ensure_asset_synced(project_dir, normalized, local_path=target)
    except Exception as exc:
        log("SupabasePersistence", f"Asset sync failed for {project_dir.name}/{normalized}: {exc}")


async def _project_file_response(project_dir: Path, requested_path: str):
    normalized = Path(requested_path).as_posix().lstrip("./")
    target = (project_dir / normalized).resolve()
    if not str(target).startswith(str(project_dir.resolve())):
        raise HTTPException(status_code=403, detail="Requested path escapes project root")

    persistence = get_supabase_persistence(http_client)
    if persistence is not None and should_persist_rel_path(normalized):
        try:
            signed_url = await persistence.get_signed_url_for_rel_path(project_dir, normalized)
            return RedirectResponse(signed_url, status_code=307)
        except FileNotFoundError:
            pass
        except Exception as exc:
            log("SupabasePersistence", f"Signed URL resolution failed for {project_dir.name}/{normalized}: {exc}")

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Project file not found")

    return FileResponse(
        target,
        media_type=mimetypes.guess_type(str(target))[0] or "application/octet-stream",
        headers={
            "Cache-Control": "no-store, no-cache, max-age=0, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


def _sanitize_thumbnail_dimension(value: int | None, fallback: int) -> int:
    if value is None:
        return fallback
    return max(64, min(2048, int(value)))


def _thumbnail_media_type(fmt: str) -> str:
    if fmt == "png":
        return "image/png"
    if fmt in {"jpg", "jpeg"}:
        return "image/jpeg"
    return "image/webp"


def _thumbnail_cache_path(
    project_dir: Path,
    target: Path,
    width: int,
    height: int,
    fit: str,
    fmt: str,
) -> Path:
    rel = target.relative_to(project_dir).as_posix()
    signature = f"{rel}|{target.stat().st_mtime_ns}|{width}|{height}|{fit}|{fmt}"
    digest = hashlib.sha1(signature.encode("utf-8")).hexdigest()
    _, suffix = THUMBNAIL_FORMAT_MAP[fmt]
    return project_dir / THUMBNAIL_CACHE_DIR / digest[:2] / f"{digest}{suffix}"


def _render_thumbnail(project_dir: Path, target: Path, width: int, height: int, fit: str, fmt: str) -> Path:
    target_suffix = target.suffix.lower()
    if target_suffix not in THUMBNAIL_SUPPORTED_SUFFIXES:
        raise HTTPException(status_code=415, detail="Thumbnail proxy only supports image assets")

    cache_path = _thumbnail_cache_path(project_dir, target, width, height, fit, fmt)
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return cache_path

    try:
        source_image = Image.open(target)
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=415, detail=f"Unable to decode image asset: {exc}") from exc

    resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.LANCZOS)
    image = ImageOps.exif_transpose(source_image)
    if fit == "contain":
        thumb = ImageOps.contain(image, (width, height), method=resampling)
    elif fit == "inside":
        thumb = image.copy()
        thumb.thumbnail((width, height), resampling)
    else:
        thumb = ImageOps.fit(image, (width, height), method=resampling)

    pil_format, _ = THUMBNAIL_FORMAT_MAP[fmt]
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    save_kwargs: dict[str, Any] = {"optimize": True}
    if pil_format == "WEBP":
        save_kwargs.update({"quality": 84, "method": 6})
    elif pil_format == "JPEG":
        thumb = thumb.convert("RGB")
        save_kwargs.update({"quality": 86, "progressive": True})
    elif thumb.mode not in {"RGB", "RGBA"}:
        thumb = thumb.convert("RGBA")

    thumb.save(cache_path, pil_format, **save_kwargs)
    return cache_path


def _thumbnail_response(
    project_dir: Path,
    requested_path: str,
    *,
    width: int | None,
    height: int | None,
    fit: str,
    fmt: str,
) -> FileResponse:
    target = (project_dir / requested_path).resolve()
    if not str(target).startswith(str(project_dir.resolve())):
        raise HTTPException(status_code=403, detail="Requested path escapes project root")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Project file not found")

    normalized_fit = str(fit or "cover").strip().lower()
    if normalized_fit not in {"cover", "contain", "inside"}:
        raise HTTPException(status_code=400, detail="Unsupported thumbnail fit mode")

    normalized_fmt = str(fmt or "webp").strip().lower()
    if normalized_fmt not in THUMBNAIL_FORMAT_MAP:
        raise HTTPException(status_code=400, detail="Unsupported thumbnail format")

    thumb_width = _sanitize_thumbnail_dimension(width, 640)
    thumb_height = _sanitize_thumbnail_dimension(height, 360)
    thumb_path = _render_thumbnail(project_dir, target, thumb_width, thumb_height, normalized_fit, normalized_fmt)
    return FileResponse(
        thumb_path,
        media_type=_thumbnail_media_type(normalized_fmt),
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
        },
    )


def _set_timeline_frame_duration(frame_id: str, duration: float, overrides: dict[str, Any]) -> None:
    expanded_frames = overrides.get("expandedFrames") or []
    expanded = next((item for item in expanded_frames if item.get("id") == frame_id), None)
    if expanded is not None:
        expanded["duration"] = _normalize_timeline_duration(duration)
        overrides["expandedFrames"] = expanded_frames
        return

    video_prompt_path = _timeline_prompt_path(frame_id, "video")
    video_prompt = load_json(video_prompt_path, {})
    video_prompt["duration"] = _normalize_timeline_duration(duration)
    video_prompt_path.parent.mkdir(parents=True, exist_ok=True)
    video_prompt_path.write_text(json.dumps(video_prompt, indent=2) + "\n", encoding="utf-8")


def _set_timeline_frame_dialogue(frame_id: str, dialogue_id: str | None, overrides: dict[str, Any]) -> None:
    expanded_frames = overrides.get("expandedFrames") or []
    expanded = next((item for item in expanded_frames if item.get("id") == frame_id), None)
    if expanded is not None:
        expanded["dialogueId"] = dialogue_id
        overrides["expandedFrames"] = expanded_frames
        return

    frame_overrides = overrides.get("frameOverrides") or {}
    current = dict(frame_overrides.get(frame_id) or {})
    current["dialogueId"] = dialogue_id
    frame_overrides[frame_id] = current
    overrides["frameOverrides"] = frame_overrides


def _set_timeline_frame_trim(
    frame_id: str,
    overrides: dict[str, Any],
    *,
    trim_start: float | None = None,
    trim_end: float | None = None,
) -> None:
    expanded_frames = overrides.get("expandedFrames") or []
    expanded = next((item for item in expanded_frames if item.get("id") == frame_id), None)
    if expanded is not None:
        if trim_start is not None:
            expanded["trimStart"] = _normalize_timeline_trim(trim_start)
        if trim_end is not None:
            expanded["trimEnd"] = _normalize_timeline_trim(trim_end)
        overrides["expandedFrames"] = expanded_frames
        return

    frame_overrides = overrides.get("frameOverrides") or {}
    current = dict(frame_overrides.get(frame_id) or {})
    if trim_start is not None:
        current["trimStart"] = _normalize_timeline_trim(trim_start)
    if trim_end is not None:
        current["trimEnd"] = _normalize_timeline_trim(trim_end)
    frame_overrides[frame_id] = current
    overrides["frameOverrides"] = frame_overrides


def _set_timeline_frame_image(frame_id: str, image_rel: str, overrides: dict[str, Any]) -> None:
    expanded_frames = overrides.get("expandedFrames") or []
    expanded = next((item for item in expanded_frames if item.get("id") == frame_id), None)
    if expanded is not None:
        expanded["imageRel"] = image_rel
        overrides["expandedFrames"] = expanded_frames


def _set_timeline_frame_prompt(frame_id: str, prompt: str, overrides: dict[str, Any]) -> None:
    expanded_frames = overrides.get("expandedFrames") or []
    expanded = next((item for item in expanded_frames if item.get("id") == frame_id), None)
    if expanded is not None:
        expanded["prompt"] = prompt
        overrides["expandedFrames"] = expanded_frames
        return

    image_prompt_path = _timeline_prompt_path(frame_id, "image")
    image_prompt = load_json(image_prompt_path, {})
    image_prompt["prompt"] = prompt
    image_prompt_path.parent.mkdir(parents=True, exist_ok=True)
    image_prompt_path.write_text(json.dumps(image_prompt, indent=2) + "\n", encoding="utf-8")


def _set_timeline_dialogue_override(dialogue_id: str, field: str, value: Any, overrides: dict[str, Any]) -> None:
    dialogue_overrides = overrides.get("dialogueOverrides") or {}
    current = dict(dialogue_overrides.get(dialogue_id) or {})
    current[field] = value
    dialogue_overrides[dialogue_id] = current
    overrides["dialogueOverrides"] = dialogue_overrides


def _redistribute_dialogue_frames(
    project_id: str,
    dialogue_id: str,
    overrides: dict[str, Any],
    *,
    pinned_frame_id: str | None = None,
    pinned_duration: float | None = None,
) -> None:
    snapshot = _workspace_snapshot(project_id)
    dialogue = next((item for item in snapshot["dialogueBlocks"] if item["id"] == dialogue_id), None)
    if not dialogue:
        return

    linked_frame_ids = list(dialogue.get("linkedFrameIds") or [])
    if not linked_frame_ids:
        return

    effective_total = max(float(dialogue.get("duration") or 0), float(len(linked_frame_ids) * 2))
    effective_total = round(effective_total, 1)
    if effective_total != float(dialogue.get("duration") or 0):
        _set_timeline_dialogue_override(dialogue_id, "duration", effective_total, overrides)

    if len(linked_frame_ids) == 1:
        only_frame = linked_frame_ids[0]
        duration = _normalize_timeline_duration(pinned_duration if pinned_frame_id == only_frame else effective_total)
        _set_timeline_frame_duration(only_frame, duration, overrides)
        _set_timeline_dialogue_override(dialogue_id, "duration", duration, overrides)
        return

    assignments: dict[str, float] = {}
    if pinned_frame_id and pinned_frame_id in linked_frame_ids and pinned_duration is not None:
        max_pinned = max(2.0, effective_total - float((len(linked_frame_ids) - 1) * 2))
        assignments[pinned_frame_id] = min(_normalize_timeline_duration(pinned_duration), round(max_pinned, 1))

    remaining_ids = [frame_id for frame_id in linked_frame_ids if frame_id not in assignments]
    remaining_total = effective_total - sum(assignments.values())
    if remaining_ids:
        min_required = float(len(remaining_ids) * 2)
        if remaining_total < min_required:
            remaining_total = min_required
            effective_total = round(sum(assignments.values()) + remaining_total, 1)
            _set_timeline_dialogue_override(dialogue_id, "duration", effective_total, overrides)
        base_value = round(remaining_total / len(remaining_ids), 1)
        running_total = sum(assignments.values())
        for index, frame_id in enumerate(remaining_ids):
            if index == len(remaining_ids) - 1:
                duration = round(effective_total - running_total, 1)
            else:
                duration = base_value
                running_total += duration
            assignments[frame_id] = _normalize_timeline_duration(duration)

    for frame_id, duration in assignments.items():
        _set_timeline_frame_duration(frame_id, duration, overrides)


def _local_project_workers_payload() -> list[dict[str, Any]]:
    workers: list[dict[str, Any]] = []
    for job in ui_pipeline_jobs.values():
        workers.append(
            {
                "id": job["id"],
                "name": job["name"],
                "status": job["status"],
                "progress": job["progress"],
                "message": job["message"],
                "targetPhase": int(job.get("targetPhase") or 0),
                "cancellable": bool(
                    int(job.get("targetPhase") or 0) >= 4 and job.get("status") == "running"
                ),
            }
        )
    return workers


def _supabase_job_to_worker_payload(row: dict[str, Any]) -> dict[str, Any]:
    status = str(row.get("status") or "queued").strip().lower()
    worker_status = "idle"
    if status == "running":
        worker_status = "running"
    elif status == "complete":
        worker_status = "complete"
    elif status in {"error", "cancel_requested"}:
        worker_status = "error"

    target_phase = int(row.get("target_phase") or 0)
    message = str(row.get("message") or "").strip()
    if status == "queued" and not message:
        message = "Queued for background execution..."
    elif status == "cancel_requested":
        message = message or "Stopping background execution..."

    return {
        "id": str(row.get("job_key") or row.get("id") or ""),
        "name": _default_job_name(str(row.get("job_key") or "job"), target_phase),
        "status": worker_status,
        "progress": int(row.get("progress") or 0),
        "message": message,
        "targetPhase": target_phase,
        "cancellable": bool(status == "running" and target_phase >= 4 and not bool(row.get("cancel_requested"))),
    }


async def _project_workers_payload_async(project_id: str) -> list[dict[str, Any]]:
    if not _queue_execution_enabled():
        return _local_project_workers_payload()

    persistence = get_supabase_persistence(http_client)
    if persistence is None:
        return _local_project_workers_payload()

    try:
        rows = await persistence.list_pipeline_jobs(project_id, include_terminal=True)
    except Exception as exc:
        log("SupabasePersistence", f"Worker list failed for {project_id}: {exc}")
        return _local_project_workers_payload()
    return [_supabase_job_to_worker_payload(row) for row in rows]


async def _sync_job_state_to_supabase(job: dict[str, Any]) -> None:
    persistence = get_supabase_persistence(http_client)
    if persistence is None:
        return
    try:
        await persistence.ensure_project(PROJECT_DIR)
        await persistence.update_pipeline_job(
            project_id=PROJECT_DIR.name,
            job_key=str(job.get("id") or ""),
            status=str(job.get("status") or "queued"),
            progress=int(job.get("progress") or 0),
            message=str(job.get("message") or ""),
            active_phase=int(job.get("activePhase") or 0) or None,
            target_phase=int(job.get("targetPhase") or 0) or None,
            cancel_requested=bool(job.get("cancelRequested")),
            payload={
                "phaseNumbers": list(job.get("phaseNumbers") or []),
                "cancelRequested": bool(job.get("cancelRequested")),
            },
            worker_name=str(job.get("workerName") or ""),
        )
    except Exception as exc:
        log("SupabasePersistence", f"Job sync failed for {job.get('id')}: {exc}")


def _schedule_job_sync(job: dict[str, Any]) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_sync_job_state_to_supabase(job))


def _pipeline_status_for_phase(phase_number: int) -> str:
    if phase_number <= 3:
        return "generating_assets"
    if phase_number == 4:
        return "generating_frames"
    return "generating_video"


def _default_job_name(job_name: str, target_phase: int) -> str:
    if job_name == "preproduction_build" or target_phase == 3:
        return "Preproduction Build"
    if job_name == "frame_generation" or target_phase == 4:
        return "Frame Generation"
    if job_name == "video_generation" or target_phase == 5:
        return "Video Generation"
    return job_name.replace("_", " ").title()


def _default_job_running_message(job_name: str, target_phase: int, active_phase: int) -> str:
    if job_name == "preproduction_build" or target_phase == 3:
        if active_phase <= 0:
            return "Initializing project build..."
        if active_phase == 1:
            return "Writing outline and creative output..."
        if active_phase == 2:
            return "Constructing graph, enrichment, and shot tags..."
        return "Generating entity references..."
    if job_name == "frame_generation" or target_phase == 4:
        return "Generating approved frames..."
    if job_name == "video_generation" or target_phase == 5:
        return "Generating approved video clips..."
    return f"Running pipeline phase {active_phase}"


def _default_job_complete_message(job_name: str, target_phase: int) -> str:
    if job_name == "preproduction_build" or target_phase == 3:
        return "Preproduction build completed"
    if job_name == "frame_generation" or target_phase == 4:
        return "Frame generation completed"
    if job_name == "video_generation" or target_phase == 5:
        return "Video generation completed"
    if target_phase >= 0:
        return f"Phase {target_phase} completed"
    return "Pipeline phases completed"


def _phase_complete(manifest: dict[str, Any], phase_number: int) -> bool:
    phase = (manifest.get("phases") or {}).get(f"phase_{phase_number}", {})
    return phase.get("status") == "complete"


def _phases_complete_through(manifest: dict[str, Any], target_phase: int) -> bool:
    return all(_phase_complete(manifest, phase_number) for phase_number in range(target_phase + 1))


def _next_pipeline_target(
    manifest: dict[str, Any],
    approvals: dict[str, Any],
    artifact_progress: dict[str, Any] | None = None,
    invalidations: dict[str, Any] | None = None,
) -> tuple[str, int] | None:
    if not _phases_complete_through(manifest, 3):
        return ("preproduction_build", 3)

    dirty_phases = {
        int(str(key).split("_", 1)[1])
        for key in (invalidations or {})
        if re.fullmatch(r"phase_\d+", str(key))
    }
    if 3 in dirty_phases:
        return ("preproduction_build", 3)

    expected_frames = int((artifact_progress or {}).get("expectedFrameCount") or 0)
    composed_frames = int((artifact_progress or {}).get("composedFrameCount") or 0)
    clip_count = int((artifact_progress or {}).get("clipCount") or 0)
    frames_incomplete = expected_frames > 0 and composed_frames < expected_frames
    clips_incomplete = expected_frames > 0 and clip_count < expected_frames

    if approvals.get("referencesApprovedAt") and (
        4 in dirty_phases or not _phases_complete_through(manifest, 4) or frames_incomplete
    ):
        return ("frame_generation", 4)
    if approvals.get("timelineApprovedAt") and frames_incomplete:
        return ("frame_generation", 4)
    if approvals.get("timelineApprovedAt") and (
        5 in dirty_phases or not _phases_complete_through(manifest, 5) or clips_incomplete
    ):
        return ("video_generation", 5)
    return None


def _build_pipeline_job_command(target_phase: int) -> list[str]:
    cmd = [
        sys.executable,
        str(APP_DIR / "run_pipeline.py"),
        "--project",
        PROJECT_DIR.name,
    ]
    # Post-approval catch-up should resume from the approved gate itself rather
    # than rewinding earlier "complete" phases based on reuse warnings.
    if target_phase >= 4:
        cmd.extend(["--phase", str(target_phase)])
    else:
        cmd.extend(["--resume", "--through-phase", str(target_phase)])
    cmd.append("--live")
    return cmd


def _dirty_preproduction_start_phase(target_phase: int) -> int | None:
    if target_phase > 3:
        return None
    dirty = [phase for phase in dirty_pipeline_phases(PROJECT_DIR) if 1 <= phase <= target_phase]
    return min(dirty) if dirty else None


def _job_checkpoint_progress(target_phase: int) -> tuple[int, int]:
    manifest = load_json(PROJECT_DIR / "project_manifest.json", {})
    dirty_phases = set(dirty_pipeline_phases(PROJECT_DIR))
    active_phase = target_phase
    for phase_number in range(target_phase + 1):
        if phase_number in dirty_phases or not _phase_complete(manifest, phase_number):
            active_phase = phase_number
            break

    total_phases = max(target_phase + 1, 1)
    completed = sum(
        1
        for phase_number in range(target_phase + 1)
        if phase_number not in dirty_phases and _phase_complete(manifest, phase_number)
    )
    progress = min(95, max(5, round((completed / total_phases) * 100)))
    return active_phase, progress


def _video_generation_preflight_error() -> str | None:
    try:
        from graph.api import build_shot_packet
        from graph.store import GraphStore

        graph = GraphStore(PROJECT_DIR).load()
    except Exception as exc:
        return f"Video generation preflight failed while loading graph state: {exc}"

    frame_ids = list(getattr(graph, "frame_order", None) or list(getattr(graph, "frames", {}).keys()))
    for frame_id in frame_ids:
        try:
            packet = build_shot_packet(graph, frame_id)
        except Exception as exc:
            return f"Video generation preflight failed for {frame_id}: {exc}"

        shot_intent = getattr(packet, "shot_intent", None)
        missing: list[str] = []
        if not str(getattr(shot_intent, "shot", "") or "").strip():
            missing.append("shot")
        if not str(getattr(shot_intent, "angle", "") or "").strip():
            missing.append("angle")
        if not str(getattr(shot_intent, "movement", "") or "").strip():
            missing.append("movement")
        if missing:
            return f"{frame_id}: incomplete shot packet for video prompt assembly; missing {', '.join(missing)}"
    return None


async def _clear_invalid_video_generation_state(
    reason: str,
    *,
    source: str,
    preserve_approval: bool = False,
) -> None:
    message = str(reason or "").strip() or "Video generation is blocked by invalid timeline data."
    if preserve_approval:
        mark_pipeline_invalidation(
            PROJECT_DIR,
            5,
            "video_generation_preflight_failed",
            source=source,
            subject_type="timeline",
            subject_id="",
            clear_approvals=(),
        )
    else:
        _mark_timeline_video_dirty("video_generation_preflight_failed", source=source)

    job = ui_pipeline_jobs.get("video_generation")
    if job is not None:
        proc = job.get("process")
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        job["process"] = None
        job["status"] = "error"
        job["progress"] = 100
        job["message"] = f"Video Generation blocked: {message}"
    log("UIJob", f"Cleared invalid video generation approval: {message}")


def _job_stop_phase_and_approvals(job: dict[str, Any]) -> tuple[int, tuple[str, ...]] | None:
    target_phase = int(job.get("targetPhase") or 0)
    if target_phase >= 5:
        return 5, ("timelineApprovedAt", "videoApprovedAt")
    if target_phase == 4:
        return 4, ("referencesApprovedAt", "timelineApprovedAt", "videoApprovedAt")
    return None


async def _terminate_job_process(job: dict[str, Any]) -> None:
    proc = job.get("process")
    if proc is None or proc.returncode is not None:
        return
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
    finally:
        job["process"] = None


async def _cancel_ui_pipeline_job(job: dict[str, Any], *, reason: str = "Stopped by user") -> None:
    job["cancelRequested"] = True
    await _terminate_job_process(job)

    task = job.get("task")
    if task is not None and task is not asyncio.current_task() and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log("UIJob", f"Cancellation cleanup for {job.get('id')} raised {type(exc).__name__}: {exc}")

    stop_state = _job_stop_phase_and_approvals(job)
    if stop_state is not None:
        start_phase, clear_approvals = stop_state
        mark_pipeline_invalidation(
            PROJECT_DIR,
            start_phase,
            "stopped_by_user",
            source=str(job.get("id") or "ui_stop"),
            subject_type="workflow",
            subject_id=str(job.get("id") or ""),
            clear_approvals=clear_approvals,
        )

    job["status"] = "error"
    job["message"] = reason
    _schedule_job_sync(job)
    ui_pipeline_jobs.pop(str(job.get("id") or ""), None)


def _running_cancellable_jobs() -> list[dict[str, Any]]:
    return [
        job
        for job in ui_pipeline_jobs.values()
        if job.get("status") == "running" and _job_stop_phase_and_approvals(job) is not None
    ]


async def _active_queue_jobs(project_id: str) -> list[dict[str, Any]]:
    persistence = get_supabase_persistence(http_client)
    if persistence is None:
        return []
    try:
        return await persistence.list_pipeline_jobs(project_id, include_terminal=False)
    except Exception as exc:
        log("SupabasePersistence", f"Active queue job lookup failed for {project_id}: {exc}")
        return []


def _queue_job_stop_phase_and_approvals(job: dict[str, Any]) -> tuple[int, tuple[str, ...]] | None:
    target_phase = int(job.get("target_phase") or 0)
    if target_phase >= 5:
        return 5, ("timelineApprovedAt", "videoApprovedAt")
    if target_phase == 4:
        return 4, ("referencesApprovedAt", "timelineApprovedAt", "videoApprovedAt")
    return None


async def _cancel_queue_job(job: dict[str, Any], *, reason: str = "Stopped by user") -> None:
    persistence = get_supabase_persistence(http_client)
    if persistence is None:
        raise RuntimeError("Supabase persistence is required to cancel queued jobs.")

    stop_state = _queue_job_stop_phase_and_approvals(job)
    if stop_state is not None:
        start_phase, clear_approvals = stop_state
        mark_pipeline_invalidation(
            PROJECT_DIR,
            start_phase,
            "stopped_by_user",
            source=str(job.get("job_key") or "ui_stop"),
            subject_type="workflow",
            subject_id=str(job.get("job_key") or ""),
            clear_approvals=clear_approvals,
        )

    await persistence.update_pipeline_job(
        project_id=PROJECT_DIR.name,
        job_key=str(job.get("job_key") or ""),
        status="cancel_requested",
        progress=int(job.get("progress") or 0),
        message=reason,
        active_phase=int(job.get("active_phase") or 0) or None,
        target_phase=int(job.get("target_phase") or 0) or None,
        cancel_requested=True,
        payload=dict(job.get("payload") or {}),
        result=dict(job.get("result") or {}),
        worker_name=str(job.get("worker_name") or job.get("claimed_by") or ""),
    )


async def _repair_video_preflight_blockers(job: dict[str, Any] | None = None) -> None:
    from graph.store import GraphStore

    store = GraphStore(PROJECT_DIR)
    if not store.exists():
        raise RuntimeError("Video generation preflight repair failed: graph is missing.")

    graph = store.load()
    frame_ids = list(getattr(graph, "frame_order", []) or list(getattr(graph, "frames", {}).keys()))
    missing_direction = [
        frame_id
        for frame_id in frame_ids
        if (
            (frame := graph.frames.get(frame_id)) is not None
            and (
                getattr(frame, "composition", None) is None
                or not str(getattr(frame.composition, "shot", "") or "").strip()
                or not str(getattr(frame.composition, "angle", "") or "").strip()
                or not str(getattr(frame.composition, "movement", "") or "").strip()
            )
        )
    ]
    missing_tags = [
        frame_id
        for frame_id in frame_ids
        if not str(getattr(getattr(graph.frames.get(frame_id), "cinematic_tag", None), "tag", "") or "").strip()
    ]

    if job is not None:
        job["progress"] = max(int(job.get("progress") or 0), 12)
        job["message"] = (
            f"Repairing video direction on {len(missing_direction) or len(frame_ids)} frame(s)..."
        )

    await _run_project_script(
        "-m",
        "graph.frame_enricher",
        "--project-dir",
        str(PROJECT_DIR),
        label="video_preflight_frame_enricher",
        job=job,
    )

    if job is not None:
        job["progress"] = max(int(job.get("progress") or 0), 42)
        job["message"] = "Refreshing cinematic tags and motion guidance..."

    if missing_tags:
        await _run_project_script(
            "-m",
            "graph.grok_tagger",
            "--project-dir",
            str(PROJECT_DIR),
            label="video_preflight_grok_tagger",
            job=job,
        )

    await _run_project_script(
        str(APP_DIR / "skills" / "graph_validate_video_direction"),
        "--project-dir",
        str(PROJECT_DIR),
        "--fix",
        label="video_preflight_graph_validate_video_direction",
        job=job,
    )

    if job is not None:
        job["progress"] = max(int(job.get("progress") or 0), 68)
        job["message"] = "Rebuilding prompts and materialized timeline output..."

    await _run_project_script(
        str(APP_DIR / "skills" / "graph_assemble_prompts"),
        "--project-dir",
        str(PROJECT_DIR),
        label="video_preflight_graph_assemble_prompts",
        job=job,
    )
    await _run_project_script(
        str(APP_DIR / "skills" / "graph_validate_dialogue"),
        "--project-dir",
        str(PROJECT_DIR),
        label="video_preflight_graph_validate_dialogue",
        job=job,
    )
    await _run_project_script(
        str(APP_DIR / "graph" / "prompt_pair_validator.py"),
        "--project-dir",
        str(PROJECT_DIR),
        label="video_preflight_prompt_pair_validator",
        job=job,
    )
    await _run_project_script(
        str(APP_DIR / "skills" / "graph_materialize"),
        "--project-dir",
        str(PROJECT_DIR),
        label="video_preflight_graph_materialize",
        job=job,
    )

    final_error = _video_generation_preflight_error()
    if final_error:
        raise RuntimeError(final_error)


async def _spawn_video_preflight_repair_job() -> dict[str, Any]:
    job_name = "video_preflight_repair"
    existing = ui_pipeline_jobs.get(job_name)
    if existing and existing.get("status") == "running":
        return existing

    job = {
        "id": job_name,
        "name": "Video Prep Repair",
        "status": "running",
        "progress": 8,
        "message": "Repairing timeline direction before video generation...",
        "process": None,
        "task": None,
        "cancelRequested": False,
        "phaseNumbers": [5],
        "activePhase": 5,
        "targetPhase": 5,
        "startedAt": datetime.now(timezone.utc).isoformat(),
    }
    ui_pipeline_jobs[job_name] = job
    _schedule_job_sync(job)

    async def _watch() -> None:
        try:
            await _repair_video_preflight_blockers(job)
            if job.get("cancelRequested"):
                return
            job["status"] = "complete"
            job["progress"] = 100
            job["activePhase"] = 5
            job["message"] = "Video prep repaired. Resuming clip generation..."
            _schedule_job_sync(job)
            await _ensure_pipeline_catchup(PROJECT_DIR.name)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            await _clear_invalid_video_generation_state(
                str(exc),
                source="video_preflight_repair",
                preserve_approval=True,
            )
            job["status"] = "error"
            job["progress"] = 100
            job["message"] = f"Video Prep Repair failed: {exc}"
            _schedule_job_sync(job)

    job["task"] = asyncio.create_task(_watch())
    return job


async def _ensure_pipeline_catchup(project_id: str) -> None:
    _assert_project(project_id)
    manifest = load_json(PROJECT_DIR / "project_manifest.json", {})
    workspace_state = _workspace_state()
    approvals = dict((workspace_state.get("approvals") or {}))
    invalidations = load_pipeline_invalidations(PROJECT_DIR)
    artifact_progress = pipeline_artifact_progress(PROJECT_DIR, manifest)
    dirty_phases = {
        int(str(key).split("_", 1)[1])
        for key in invalidations
        if re.fullmatch(r"phase_\d+", str(key))
    }
    expected_frames = int(artifact_progress.get("expectedFrameCount") or 0)
    composed_frames = int(artifact_progress.get("composedFrameCount") or 0)
    frames_incomplete = expected_frames > 0 and composed_frames < expected_frames
    if approvals.get("timelineApprovedAt") and not frames_incomplete and 4 not in dirty_phases:
        preflight_error = _video_generation_preflight_error()
        if preflight_error:
            await _spawn_video_preflight_repair_job()
            return

    if _queue_execution_enabled():
        active_jobs = await _active_queue_jobs(project_id)
    else:
        active_jobs = [
            job
            for job in ui_pipeline_jobs.values()
            if job.get("status") == "running"
            and (job.get("process") is None or job["process"].returncode is None)
        ]
    if active_jobs:
        return

    next_target = _next_pipeline_target(manifest, approvals, artifact_progress, invalidations)
    if next_target is None:
        return

    job_name, target_phase = next_target
    spawn_kwargs: dict[str, Any] = {}
    if job_name == "frame_generation":
        spawn_kwargs = {
            "prelaunch": _finalize_review_and_resume_phase4,
            "prelaunch_message": "Applying review updates before frame generation...",
        }
    await _spawn_pipeline_phase_job(job_name, target_phase, **spawn_kwargs)


def _mark_timeline_video_dirty(reason: str, *, source: str, subject_id: str | None = None) -> None:
    mark_pipeline_invalidation(
        PROJECT_DIR,
        5,
        reason,
        source=source,
        subject_type="timeline",
        subject_id=subject_id,
        clear_approvals=("timelineApprovedAt", "videoApprovedAt"),
    )


def _mark_frame_regeneration_dirty(reason: str, *, source: str, subject_id: str | None = None) -> None:
    mark_pipeline_invalidation(
        PROJECT_DIR,
        4,
        reason,
        source=source,
        subject_type="frame",
        subject_id=subject_id,
        clear_approvals=("timelineApprovedAt", "videoApprovedAt"),
    )


def _project_entity_by_id(project_id: str, entity_id: str) -> dict[str, Any]:
    entity = next(
        (item for item in _workspace_snapshot(project_id)["entities"] if item["id"] == entity_id),
        None,
    )
    if entity is None:
        raise HTTPException(status_code=404, detail=f"Entity '{entity_id}' not found")
    return entity


async def _run_project_script(*args: str, label: str, job: dict[str, Any] | None = None) -> None:
    cmd = [sys.executable, *args]
    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH", "")
    repo_root = str(APP_DIR)
    env["PYTHONPATH"] = repo_root if not existing_pythonpath else f"{repo_root}{os.pathsep}{existing_pythonpath}"
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(APP_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    if job is not None:
        job["process"] = proc
    stdout_data, stderr_data = await proc.communicate()
    if job is not None and job.get("process") is proc:
        job["process"] = None
    if stdout_data:
        log(label, stdout_data.decode(errors="ignore"))
    if stderr_data:
        log(label, stderr_data.decode(errors="ignore"))
    if proc.returncode != 0:
        raise RuntimeError(f"{label} failed with exit {proc.returncode}")


def _review_alignment_schema(node_type: str) -> dict[str, Any]:
    if node_type == "cast":
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "physical_description": {"type": "string"},
                "wardrobe_description": {"type": "string"},
                "personality": {"type": "string"},
            },
            "required": ["physical_description", "wardrobe_description", "personality"],
        }
    if node_type == "location":
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "description": {"type": "string"},
                "atmosphere": {"type": "string"},
            },
            "required": ["description", "atmosphere"],
        }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "description": {"type": "string"},
            "narrative_significance": {"type": "string"},
        },
        "required": ["description", "narrative_significance"],
    }


def _review_alignment_instruction(node_type: str, node: Any) -> str:
    base_summary = json.dumps(node.model_dump(mode="json"), indent=2, ensure_ascii=False)
    if node_type == "cast":
        return (
            "Review this cast node against the uploaded reference image. Return concise corrections "
            "that make the identity and wardrobe description match the reference image while preserving "
            "the story role. Do not invent weapons, locations, or scene actions.\n\n"
            f"Current node:\n{base_summary}"
        )
    if node_type == "location":
        return (
            "Review this location node against the uploaded reference image. Return concise corrections "
            "to the location description and atmosphere so downstream prompts match the actual place.\n\n"
            f"Current node:\n{base_summary}"
        )
    return (
        "Review this prop node against the uploaded reference image. Return concise corrections "
        "to the prop description and narrative significance so downstream prompts match the actual object.\n\n"
        f"Current node:\n{base_summary}"
    )


def _apply_review_alignment_patch(node_type: str, node: Any, patch: dict[str, Any]) -> None:
    if node_type == "cast":
        node.identity.physical_description = str(patch.get("physical_description") or node.identity.physical_description or "")
        node.identity.wardrobe_description = str(patch.get("wardrobe_description") or node.identity.wardrobe_description or "")
        node.personality = str(patch.get("personality") or node.personality or "")
        return
    if node_type == "location":
        node.description = str(patch.get("description") or node.description or "")
        node.atmosphere = str(patch.get("atmosphere") or node.atmosphere or "")
        return
    node.description = str(patch.get("description") or node.description or "")
    node.narrative_significance = str(patch.get("narrative_significance") or node.narrative_significance or "")


def _review_change_impacted_frames(graph: Any, changes: list[dict[str, Any]]) -> list[str]:
    frame_ids: set[str] = set()
    for change in changes:
        node_type = str(change.get("nodeType") or "").strip().lower()
        node_id = str(change.get("nodeId") or "").strip()
        if not node_type or not node_id:
            continue
        if node_type == "cast":
            for state in graph.cast_frame_states.values():
                if state.cast_id == node_id:
                    frame_ids.add(state.frame_id)
        elif node_type == "location":
            for frame in graph.frames.values():
                if frame.location_id == node_id:
                    frame_ids.add(frame.frame_id)
        elif node_type == "prop":
            for state in graph.prop_frame_states.values():
                if state.prop_id == node_id:
                    frame_ids.add(state.frame_id)
    return sorted(frame_ids)


async def _finalize_review_and_resume_phase4(job: dict[str, Any] | None = None) -> None:
    changes = load_review_entity_changes(PROJECT_DIR)
    invalidations = load_pipeline_invalidations(PROJECT_DIR)
    phase4_reason = str((invalidations.get("phase_4") or {}).get("reason") or "").strip()
    needs_prompt_refresh = phase4_reason in {
        "entity_graph_updated",
        "graph_downstream_updated",
        "entity_created",
        "entity_deleted",
        "entity_image_attached",
        "reference_asset_updated",
        "graph_artifact_updated",
    }
    if not changes and not needs_prompt_refresh:
        return

    from graph.frame_enricher import apply_frame_enrichment, re_enrich_frames
    from graph.reference_collector import ReferenceImageCollector
    from graph.runtime_state import save_graph_projection
    from graph.store import GraphStore

    store = GraphStore(PROJECT_DIR)
    graph = store.load()
    xai_key = os.getenv("XAI_API_KEY", "")

    if job is not None and changes:
        job["progress"] = max(int(job.get("progress") or 0), 12)
        job["message"] = "Applying reviewed entity updates..."

    if changes and xai_key:
        client = XAIClient(api_key=xai_key)
        for change in changes:
            image_rel = str(change.get("imagePath") or "").strip()
            node_type = str(change.get("nodeType") or "").strip().lower()
            node_id = str(change.get("nodeId") or "").strip()
            if not image_rel or not node_type or not node_id:
                continue
            registry_name = graph_collection_name(node_type)
            registry = getattr(graph, registry_name, None)
            node = registry.get(node_id) if registry is not None else None
            image_path = PROJECT_DIR / image_rel
            if node is None or not image_path.exists():
                continue
            patch = await client.generate_json_with_image(
                image_path=image_path,
                prompt=_review_alignment_instruction(node_type, node),
                schema=_review_alignment_schema(node_type),
                system_prompt="You align ScreenWire graph entity descriptions to uploaded reference images. Return only compact factual corrections.",
                model="grok-4-1-fast-reasoning",
                task_hint="entity_review_alignment",
                temperature=0.0,
                max_tokens=1200,
            )
            _apply_review_alignment_patch(node_type, node, patch)

    if job is not None:
        job["progress"] = max(int(job.get("progress") or 0), 20)
        job["message"] = "Refreshing cast bible and prompt context..."

    ReferenceImageCollector(graph, PROJECT_DIR).sync_cast_bible(
        store=store,
        run_id=current_run_id(),
        sequence_id=getattr(graph.project, "project_id", "") or PROJECT_DIR.name,
    )

    impacted_frames = _review_change_impacted_frames(graph, changes)
    if impacted_frames and xai_key:
        if job is not None:
            job["progress"] = max(int(job.get("progress") or 0), 28)
            job["message"] = "Re-enriching affected timeline nodes..."
        correction_issues = [
            {
                "frame_id": frame_id,
                "what": (
                    "User review updated entity details or uploaded new reference images. "
                    "Refresh this frame so blocking, appearance, props, and environment match "
                    "the latest approved graph state."
                ),
            }
            for frame_id in impacted_frames
        ]
        for result in await re_enrich_frames(graph, correction_issues, api_key=xai_key, max_concurrent=10):
            if "error" in result:
                log("ReviewFinalize", f"Re-enrichment warning for {result.get('frame_id')}: {result['error']}")
                continue
            apply_frame_enrichment(graph, result)

    save_graph_projection(graph, PROJECT_DIR, store=store)

    if job is not None:
        job["progress"] = max(int(job.get("progress") or 0), 36)
        job["message"] = "Rebuilding prompts and materialized review data..."

    await _run_project_script(str(APP_DIR / "skills" / "graph_assemble_prompts"), "--project-dir", str(PROJECT_DIR), label="review_graph_assemble_prompts", job=job)
    await _run_project_script(str(APP_DIR / "skills" / "graph_validate_dialogue"), "--project-dir", str(PROJECT_DIR), label="review_graph_validate_dialogue", job=job)
    await _run_project_script(str(APP_DIR / "graph" / "prompt_pair_validator.py"), "--project-dir", str(PROJECT_DIR), label="review_prompt_pair_validator", job=job)
    await _run_project_script(str(APP_DIR / "skills" / "graph_materialize"), "--project-dir", str(PROJECT_DIR), label="review_graph_materialize", job=job)
    await _run_project_script(str(APP_DIR / "skills" / "graph_validate_video_direction"), "--project-dir", str(PROJECT_DIR), "--fix", label="review_graph_validate_video_direction", job=job)
    if changes:
        clear_review_entity_changes(PROJECT_DIR)


def _focus_summary(focus_target: dict[str, Any] | None, focus_targets: list[dict[str, Any]] | None = None) -> str:
    focus_targets = [item for item in (focus_targets or []) if isinstance(item, dict) and item.get("id")]
    if focus_targets:
        ordered = [f"{item.get('type')}::{item.get('id')} ({item.get('name')})" for item in focus_targets]
        return " Current focus selection: " + "; ".join(ordered) + ". Prioritize these items together."
    if focus_target:
        return (
            f" Current focus target: {focus_target.get('type')}::{focus_target.get('id')} "
            f"({focus_target.get('name')}). Prioritize that context."
        )
    return ""


def _approval_key(gate: str) -> str:
    normalized = str(gate or "").strip().lower()
    mapping = {
        "skeleton": "skeletonApprovedAt",
        "references": "referencesApprovedAt",
        "reference": "referencesApprovedAt",
        "timeline": "timelineApprovedAt",
        "video": "videoApprovedAt",
    }
    if normalized not in mapping:
        raise HTTPException(status_code=400, detail=f"Unknown approval gate: {gate}")
    return mapping[normalized]


def _run_morpheus_chat(
    content: str,
    focus_target: dict[str, Any] | None = None,
    focus_targets: list[dict[str, Any]] | None = None,
    mode: str = "suggest",
) -> str:
    if not XAI_API_KEY:
        return (
            "Morpheus is not configured yet because XAI_API_KEY is missing on the local backend. "
            "The workspace is loaded, but chat reasoning is unavailable."
        )

    mode_normalized = str(mode or "suggest").strip().lower()
    system_prompt = (
        "You are Morpheus, the local ScreenWire creative assistant. "
        "You help the user understand, edit, and regenerate the currently selected project. "
        "Use the provided tools to inspect the project files, Greenlight reports, graph, prompts, and assets. "
        "Prefer structured graph edits and targeted media actions over loose freeform rewrites. "
        "Do not edit source code outside the project. Be direct, practical, and concise."
    )
    if mode_normalized == "apply":
        system_prompt += (
            " The user wants you to apply changes directly when it is safe to do so. "
            "Prefer update_graph_node, query_graph_database, and targeted image/video actions. "
            "Only write project files when a graph edit is not sufficient, then summarize exactly what changed."
        )
    elif mode_normalized == "regenerate":
        system_prompt += (
            " The user wants targeted regeneration help. Prefer updating relevant prompt/output artifacts "
            "for the focused project items. Use Nano Banana for image generation/editing and Grok video tools for clip generation/extension when requested."
        )
    else:
        system_prompt += (
            " Default to suggestion mode unless the user explicitly asks you to apply edits."
        )
    system_prompt += (
        " Greenlight is the QA and diagnostics lane. Use Greenlight reports for health findings, "
        "but keep your own role focused on assisting the user's creative and operational intent."
    )
    system_prompt += _focus_summary(focus_target, focus_targets)

    tool_executor = make_project_tool_executor(
        project_root=PROJECT_DIR,
        repo_root=APP_DIR,
        skills_dir=APP_DIR / "skills",
    )
    client = SyncXAIClient(api_key=XAI_API_KEY)
    return client.generate_text_with_tools(
        prompt=content,
        system_prompt=system_prompt,
        tools=build_project_tools(),
        tool_executor=tool_executor,
        task_hint="morpheus-ui-chat",
        cache_key=f"{PROJECT_DIR.name}-ui-chat-{mode_normalized}",
        max_tool_turns=16,
    )


async def _spawn_pipeline_phase_job(
    job_name: str,
    phase_numbers: int | list[int],
    *,
    display_name: str | None = None,
    running_message: str | None = None,
    completion_message: str | None = None,
    prelaunch: Any = None,
    prelaunch_message: str | None = None,
) -> dict[str, Any]:
    phases = [int(phase_numbers)] if isinstance(phase_numbers, int) else [int(phase) for phase in phase_numbers]
    target_phase = max(phases)
    started_at = datetime.now(timezone.utc)

    if _queue_execution_enabled():
        persistence = get_supabase_persistence(http_client)
        if persistence is None:
            raise RuntimeError("Supabase persistence is required for queue execution mode.")

        existing_rows = await _active_queue_jobs(PROJECT_DIR.name)
        existing = next(
            (row for row in existing_rows if str(row.get("job_key") or "") == job_name),
            None,
        )
        if existing is not None:
            return {
                "id": str(existing.get("job_key") or job_name),
                "name": display_name or _default_job_name(job_name, target_phase),
                "status": str(existing.get("status") or "queued"),
                "progress": int(existing.get("progress") or 0),
                "message": str(existing.get("message") or ""),
                "phaseNumbers": phases,
                "activePhase": int(existing.get("active_phase") or 0),
                "targetPhase": int(existing.get("target_phase") or target_phase),
                "startedAt": str(existing.get("started_at") or existing.get("created_at") or started_at.isoformat()),
            }

        dirty_preproduction_phase = _dirty_preproduction_start_phase(target_phase)
        if dirty_preproduction_phase is not None:
            rewind_manifest_phases(
                PROJECT_DIR,
                dirty_preproduction_phase,
                "preproduction_resume_requested",
                source=job_name,
            )

        active_phase, progress = _job_checkpoint_progress(target_phase)
        job = {
            "id": job_name,
            "name": display_name or _default_job_name(job_name, target_phase),
            "status": "queued",
            "progress": progress,
            "message": running_message or _default_job_running_message(job_name, target_phase, active_phase),
            "phaseNumbers": phases,
            "activePhase": active_phase,
            "targetPhase": target_phase,
            "startedAt": started_at.isoformat(),
        }

        if prelaunch is not None:
            job["progress"] = max(int(job.get("progress") or 0), 10)
            job["message"] = prelaunch_message or f"Preparing {job['name'].lower()}..."
            await prelaunch(job)

        queue_message = f"Queued: {running_message or _default_job_running_message(job_name, target_phase, int(job.get('activePhase') or active_phase))}"
        await persistence.update_pipeline_job(
            project_id=PROJECT_DIR.name,
            job_key=job_name,
            status="queued",
            progress=int(job.get("progress") or 0),
            message=queue_message,
            active_phase=int(job.get("activePhase") or active_phase),
            target_phase=target_phase,
            cancel_requested=False,
            payload={
                "phaseNumbers": phases,
                "cancelRequested": False,
            },
            worker_name="",
        )
        job["message"] = queue_message
        return job

    existing = ui_pipeline_jobs.get(job_name)
    if existing and existing.get("status") == "running" and (
        existing.get("process") is None or existing["process"].returncode is None
    ):
        return existing

    dirty_preproduction_phase = _dirty_preproduction_start_phase(target_phase)
    if dirty_preproduction_phase is not None:
        rewind_manifest_phases(
            PROJECT_DIR,
            dirty_preproduction_phase,
            "preproduction_resume_requested",
            source=job_name,
        )

    active_phase, progress = _job_checkpoint_progress(target_phase)

    job = {
        "id": job_name,
        "name": display_name or _default_job_name(job_name, target_phase),
        "status": "running",
        "progress": progress,
        "message": running_message or _default_job_running_message(job_name, target_phase, active_phase),
        "process": None,
        "task": None,
        "cancelRequested": False,
        "phaseNumbers": phases,
        "activePhase": active_phase,
        "targetPhase": target_phase,
        "startedAt": started_at.isoformat(),
    }
    ui_pipeline_jobs[job_name] = job
    _schedule_job_sync(job)

    async def _watch() -> None:
        try:
            if prelaunch is not None:
                job["progress"] = max(int(job.get("progress") or 0), 10)
                job["message"] = prelaunch_message or f"Preparing {job['name'].lower()}..."
                await prelaunch(job)

            cmd = _build_pipeline_job_command(target_phase)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(APP_DIR),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=dict(os.environ),
            )
            job["process"] = proc

            communicate_task = asyncio.create_task(proc.communicate())
            while not communicate_task.done():
                active_phase_now, progress_now = _job_checkpoint_progress(target_phase)
                job["activePhase"] = active_phase_now
                job["progress"] = progress_now
                job["message"] = running_message or _default_job_running_message(job_name, target_phase, active_phase_now)
                await asyncio.sleep(0.5)

            stdout_data, stderr_data = await communicate_task
            if stdout_data:
                log("UIJob", stdout_data.decode(errors="ignore"))
            if stderr_data:
                log("UIJob", stderr_data.decode(errors="ignore"))

            if job.get("cancelRequested"):
                return
            if proc.returncode != 0:
                job["status"] = "error"
                job["progress"] = 100
                job["message"] = f"{job['name']} failed before phase {target_phase} completed"
                _schedule_job_sync(job)
                return

            job["process"] = None
            if target_phase <= 3:
                clear_pipeline_invalidations(PROJECT_DIR, 1, 2, 3, not_after=started_at)
            else:
                clear_pipeline_invalidations(PROJECT_DIR, target_phase, not_after=started_at)
            job["status"] = "complete"
            job["progress"] = 100
            job["activePhase"] = target_phase
            job["message"] = completion_message or _default_job_complete_message(job_name, target_phase)
            _schedule_job_sync(job)
            await _ensure_pipeline_catchup(PROJECT_DIR.name)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            job["process"] = None
            job["status"] = "error"
            job["progress"] = 100
            job["message"] = f"{job['name']} failed: {exc}"
            log("UIJob", f"{job['name']} failed: {type(exc).__name__}: {exc}")
            _schedule_job_sync(job)

    job["task"] = asyncio.create_task(_watch())
    return job


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


@app.get("/api/project/file/{requested_path:path}")
async def get_project_file(requested_path: str):
    return await _project_file_response(PROJECT_DIR, requested_path)


@app.get("/api/project/thumbnail/{requested_path:path}")
async def get_project_thumbnail(
    requested_path: str,
    w: int | None = Query(default=None),
    h: int | None = Query(default=None),
    fit: str = Query(default="cover"),
    format: str = Query(default="webp"),
):
    normalized = Path(requested_path).as_posix().lstrip("./")
    target = (PROJECT_DIR / normalized).resolve()
    if (
        not target.exists()
        and get_supabase_persistence(http_client) is not None
        and should_persist_rel_path(normalized)
    ):
        try:
            target = await get_supabase_persistence(http_client).mirror_remote_asset_to_cache(PROJECT_DIR, normalized)  # type: ignore[union-attr]
        except FileNotFoundError:
            pass
    return _thumbnail_response(
        PROJECT_DIR if target == (PROJECT_DIR / normalized).resolve() else PROJECT_DIR / ".cache" / "supabase_mirror",
        normalized if target == (PROJECT_DIR / normalized).resolve() else normalized,
        width=w,
        height=h,
        fit=fit,
        fmt=format,
    )


@app.get("/api/projects/{project_id}/file/{requested_path:path}")
async def get_project_scoped_file(project_id: str, requested_path: str):
    project_dir = _assert_project(project_id)
    return await _project_file_response(project_dir, requested_path)


@app.get("/api/projects/{project_id}/thumbnail/{requested_path:path}")
async def get_project_scoped_thumbnail(
    project_id: str,
    requested_path: str,
    w: int | None = Query(default=None),
    h: int | None = Query(default=None),
    fit: str = Query(default="cover"),
    format: str = Query(default="webp"),
):
    project_dir = _assert_project(project_id)
    normalized = Path(requested_path).as_posix().lstrip("./")
    target = (project_dir / normalized).resolve()
    if (
        not target.exists()
        and get_supabase_persistence(http_client) is not None
        and should_persist_rel_path(normalized)
    ):
        try:
            target = await get_supabase_persistence(http_client).mirror_remote_asset_to_cache(project_dir, normalized)  # type: ignore[union-attr]
        except FileNotFoundError:
            pass
    return _thumbnail_response(
        project_dir if target == (project_dir / normalized).resolve() else project_dir / ".cache" / "supabase_mirror",
        normalized if target == (project_dir / normalized).resolve() else normalized,
        width=w,
        height=h,
        fit=fit,
        fmt=format,
    )


@app.get("/api/projects/{project_id}/workspace")
async def get_workspace(project_id: str):
    await _ensure_pipeline_catchup(project_id)
    return await _workspace_snapshot_async(project_id)


@app.get("/api/projects/{project_id}/diagnostics")
async def get_workspace_diagnostics(project_id: str):
    _assert_project(project_id)
    path = write_ui_phase_report(project_id, PROJECT_DIR)
    return load_ui_phase_report(PROJECT_DIR) | {"path": _project_file_url(path.relative_to(PROJECT_DIR))}


@app.get("/api/projects/{project_id}/greenlight")
async def get_greenlight_report(project_id: str):
    _assert_project(project_id)
    path = write_ui_phase_report(project_id, PROJECT_DIR)
    return load_ui_phase_report(PROJECT_DIR) | {
        "path": _project_file_url(path.relative_to(PROJECT_DIR)),
        "agent": "greenlight",
    }


@app.get("/api/projects/{project_id}/concept")
async def get_project_concept(project_id: str):
    return _workspace_snapshot(project_id)["creativeConcept"]


@app.post("/api/projects/{project_id}/concept")
async def set_project_concept(project_id: str, payload: dict[str, Any]):
    _assert_project(project_id)
    onboarding_path = PROJECT_DIR / "source_files" / "onboarding_config.json"
    onboarding = load_json(onboarding_path, {})
    onboarding["extraDetails"] = payload.get("synopsis") or payload.get("sourceText") or onboarding.get("extraDetails", "")
    if payload.get("genre"):
        onboarding["genre"] = [item.strip() for item in str(payload["genre"]).split(",") if item.strip()]
    elif payload.get("genres"):
        genres = payload.get("genres") or []
        if isinstance(genres, str):
            onboarding["genre"] = [item.strip() for item in genres.split(",") if item.strip()]
        else:
            onboarding["genre"] = [str(item).strip() for item in genres if str(item).strip()]
    if payload.get("tone"):
        onboarding["mood"] = [item.strip() for item in str(payload["tone"]).split(",") if item.strip()]
    if payload.get("mediaStyle"):
        onboarding["mediaStyle"] = str(payload["mediaStyle"]).strip()
    if payload.get("frameCount") is not None:
        frame_count = payload.get("frameCount")
        onboarding["frameBudget"] = frame_count if frame_count not in ("", None) else onboarding.get("frameBudget", "auto")
    if payload.get("creativityLevel"):
        onboarding["creativeFreedom"] = str(payload["creativityLevel"]).strip()
    onboarding_path.write_text(json.dumps(onboarding, indent=2) + "\n", encoding="utf-8")
    mark_project_file_change(PROJECT_DIR, onboarding_path.relative_to(PROJECT_DIR), source="ui_concept_update")
    await _sync_project_asset_if_needed(PROJECT_DIR, onboarding_path.relative_to(PROJECT_DIR))

    pitch_path = PROJECT_DIR / "source_files" / "pitch.md"
    source_text = payload.get("sourceText") or payload.get("synopsis") or ""
    if source_text:
        pitch_path.write_text(str(source_text).strip() + "\n", encoding="utf-8")
        mark_project_file_change(PROJECT_DIR, pitch_path.relative_to(PROJECT_DIR), source="ui_concept_update")
        await _sync_project_asset_if_needed(PROJECT_DIR, pitch_path.relative_to(PROJECT_DIR))

    await _ensure_pipeline_catchup(project_id)
    return _workspace_snapshot(project_id)["creativeConcept"]


@app.post("/api/projects/{project_id}/concept/upload")
async def upload_project_concept_file(project_id: str, file: UploadFile = File(...)):
    _assert_project(project_id)
    source_dir = PROJECT_DIR / "source_files"
    source_dir.mkdir(parents=True, exist_ok=True)
    target = source_dir / (file.filename or "upload.bin")
    content = await file.read()
    target.write_bytes(content)
    await _sync_project_asset_if_needed(PROJECT_DIR, target.relative_to(PROJECT_DIR))

    extracted_text = ""
    if target.suffix.lower() in {".md", ".txt", ".json"}:
        extracted_text = target.read_text(encoding="utf-8", errors="ignore")

    return {"text": extracted_text, "path": str(target.relative_to(PROJECT_DIR))}


@app.get("/api/projects/{project_id}/skeleton")
async def get_project_skeleton(project_id: str):
    return _workspace_snapshot(project_id)["skeletonPlan"]


@app.post("/api/projects/{project_id}/skeleton/generate")
async def generate_project_skeleton(project_id: str):
    _assert_project(project_id)
    job = await _spawn_pipeline_phase_job(
        "preproduction_build",
        3,
        display_name="Preproduction Build",
    )
    return {
        "jobId": job["id"],
        "status": job["status"],
        "message": job["message"],
    }


@app.post("/api/projects/{project_id}/skeleton/approve")
async def approve_project_skeleton(project_id: str):
    _assert_project(project_id)
    state = _workspace_state()
    approvals = dict(state.get("approvals") or {})
    approvals["skeletonApprovedAt"] = datetime.now(timezone.utc).isoformat()
    state["approvals"] = approvals
    _save_workspace_state_file(state)
    await _spawn_pipeline_phase_job(
        "preproduction_build",
        3,
        display_name="Preproduction Build",
    )
    return await _workspace_snapshot_async(project_id)


@app.post("/api/projects/{project_id}/approve")
async def approve_project_gate(project_id: str, req: UIApprovalRequest):
    _assert_project(project_id)
    gate = str(req.gate or "").strip().lower()
    state = _workspace_state()
    approvals = dict(state.get("approvals") or {})
    approvals[_approval_key(gate)] = datetime.now(timezone.utc).isoformat()
    state["approvals"] = approvals
    _save_workspace_state_file(state)

    if gate == "timeline":
        preflight_error = _video_generation_preflight_error()
        if preflight_error:
            await _spawn_video_preflight_repair_job()
            return await _workspace_snapshot_async(project_id)

    if gate == "skeleton":
        await _spawn_pipeline_phase_job(
            "preproduction_build",
            3,
            display_name="Preproduction Build",
        )
    elif gate in {"reference", "references"}:
        await _spawn_pipeline_phase_job(
            "frame_generation",
            4,
            display_name="Frame Generation",
            prelaunch=_finalize_review_and_resume_phase4,
            prelaunch_message="Applying review updates before frame generation...",
        )
    elif gate == "timeline":
        await _spawn_pipeline_phase_job(
            "video_generation",
            5,
            display_name="Video Generation",
        )

    return await _workspace_snapshot_async(project_id)


@app.post("/api/projects/{project_id}/request-changes")
async def request_project_changes(project_id: str, req: UIChangeRequest):
    _assert_project(project_id)
    state = _workspace_state()
    requests = list(state.get("changeRequests") or [])
    requests.append(
        {
            "gate": str(req.gate).strip().lower(),
            "feedback": str(req.feedback).strip(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
    state["changeRequests"] = requests
    approvals = dict(state.get("approvals") or {})
    approvals.pop(_approval_key(req.gate), None)
    state["approvals"] = approvals
    _save_workspace_state_file(state)
    return {"ok": True, "changeRequests": requests}


@app.post("/api/projects/{project_id}/skeleton/edit-request")
async def request_project_skeleton_edit(project_id: str, payload: dict[str, Any]):
    _assert_project(project_id)
    feedback = str(payload.get("feedback") or "").strip()
    requests_path = PROJECT_DIR / "logs" / "ui_skeleton_feedback.json"
    existing = load_json(requests_path, [])
    if not isinstance(existing, list):
        existing = []
    existing.append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "feedback": feedback,
        }
    )
    requests_path.parent.mkdir(parents=True, exist_ok=True)
    requests_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    return _workspace_snapshot(project_id)["skeletonPlan"]


@app.get("/api/projects/{project_id}/entities")
async def get_project_entities(project_id: str):
    return _workspace_snapshot(project_id)["entities"]


@app.get("/api/projects/{project_id}/entities/{entity_id}")
async def get_project_entity(project_id: str, entity_id: str):
    _assert_project(project_id)
    return _project_entity_by_id(project_id, entity_id)


@app.post("/api/projects/{project_id}/entities")
async def create_project_entity(project_id: str, req: UIEntityCreateRequest):
    _assert_project(project_id)
    try:
        created = create_graph_node(
            PROJECT_DIR,
            req.type,
            {
                "name": req.name,
                "description": req.description,
                "metadata": req.metadata,
            },
            modified_by="ui_create",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    node_type = str(req.type or "").strip().lower().rstrip("s")
    entity_id = str(
        created.get("cast_id")
        or created.get("location_id")
        or created.get("prop_id")
        or ""
    )
    await _ensure_pipeline_catchup(project_id)
    return _project_entity_by_id(project_id, entity_id)


@app.put("/api/projects/{project_id}/entities/{entity_id}")
async def update_project_entity(project_id: str, entity_id: str, req: UIEntityUpdateRequest):
    _assert_project(project_id)
    entity = _project_entity_by_id(project_id, entity_id)
    node_type = entity["type"]
    metadata = req.metadata if isinstance(req.metadata, dict) else {}
    updates: dict[str, Any] = {}
    if req.name is not None:
        if node_type == "cast":
            updates["name"] = req.name
            updates["display_name"] = req.name
            updates["source_name"] = req.name
        else:
            updates["name"] = req.name
    if req.description is not None:
        if node_type == "cast":
            updates["personality"] = req.description
        elif node_type == "location":
            updates["description"] = req.description
        else:
            updates["description"] = req.description

    if metadata:
        if node_type == "cast":
            identity_updates = {}
            if "physical_description" in metadata:
                identity_updates["physical_description"] = metadata.get("physical_description")
            if "wardrobe_description" in metadata:
                identity_updates["wardrobe_description"] = metadata.get("wardrobe_description")
            if identity_updates:
                updates["identity"] = identity_updates
            if "story_summary" in metadata:
                updates["story_summary"] = metadata.get("story_summary")
        elif node_type == "location":
            if "atmosphere" in metadata:
                updates["atmosphere"] = metadata.get("atmosphere")
            if "location_type" in metadata:
                updates["location_type"] = metadata.get("location_type")
            if "story_summary" in metadata:
                updates["story_summary"] = metadata.get("story_summary")
        elif node_type == "prop":
            if "narrative_significance" in metadata:
                updates["narrative_significance"] = metadata.get("narrative_significance")
            if "story_summary" in metadata:
                updates["story_summary"] = metadata.get("story_summary")

    try:
        patch_graph_node(PROJECT_DIR, node_type, entity_id, updates, modified_by="ui_entity_update")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"{node_type}:{entity_id} not found") from exc
    await _ensure_pipeline_catchup(project_id)
    return _project_entity_by_id(project_id, entity_id)


@app.delete("/api/projects/{project_id}/entities/{entity_id}")
async def delete_project_entity(project_id: str, entity_id: str):
    _assert_project(project_id)
    entity = _project_entity_by_id(project_id, entity_id)
    try:
        delete_graph_node(PROJECT_DIR, entity["type"], entity_id, modified_by="ui_delete")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"{entity['type']}:{entity_id} not found") from exc
    await _ensure_pipeline_catchup(project_id)
    return {"ok": True}


@app.get("/api/projects/{project_id}/graph/{node_type}/{node_id}")
async def get_project_graph_node(project_id: str, node_type: str, node_id: str):
    _assert_project(project_id)
    try:
        graph_collection_name(node_type)
        node = get_graph_node(PROJECT_DIR, node_type, node_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if node is None:
        raise HTTPException(status_code=404, detail=f"{node_type}:{node_id} not found")
    return node


@app.get("/api/projects/{project_id}/graph/frame/{frame_id}/context")
async def get_project_frame_context(project_id: str, frame_id: str):
    _assert_project(project_id)
    context = build_frame_context(PROJECT_DIR, frame_id)
    if context is None:
        raise HTTPException(status_code=404, detail=f"frame:{frame_id} not found")
    return context


@app.patch("/api/projects/{project_id}/graph/{node_type}/{node_id}")
async def patch_project_graph_node(project_id: str, node_type: str, node_id: str, req: UIGraphNodePatchRequest):
    _assert_project(project_id)
    try:
        patch_graph_node(PROJECT_DIR, node_type, node_id, req.updates, modified_by="ui")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"{node_type}:{node_id} not found") from exc
    await _ensure_pipeline_catchup(project_id)
    return await _workspace_snapshot_async(project_id)


@app.post("/api/projects/{project_id}/entities/{entity_id}/upload")
async def upload_entity_image(project_id: str, entity_id: str, image: UploadFile = File(...)):
    _assert_project(project_id)
    entity = _project_entity_by_id(project_id, entity_id)

    target = entity_upload_path(PROJECT_DIR, entity_id, entity["type"], image.filename or ".png")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(await image.read())
    await _sync_project_asset_if_needed(PROJECT_DIR, target.relative_to(PROJECT_DIR))
    attach_entity_image(PROJECT_DIR, entity["type"], entity_id, target, modified_by="ui_upload")
    await _ensure_pipeline_catchup(project_id)
    return {"imageUrl": _project_file_url(target.relative_to(PROJECT_DIR))}


@app.get("/api/projects/{project_id}/storyboard")
async def get_project_storyboard(project_id: str):
    return _workspace_snapshot(project_id)["storyboardFrames"]


@app.post("/api/projects/{project_id}/storyboard/{frame_id}/upload")
async def upload_storyboard_frame(project_id: str, frame_id: str, image: UploadFile = File(...)):
    _assert_project(project_id)
    target = frame_upload_path(PROJECT_DIR, frame_id, image.filename or ".png")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(await image.read())
    await _sync_project_asset_if_needed(PROJECT_DIR, target.relative_to(PROJECT_DIR))
    _mark_frame_regeneration_dirty("storyboard_reference_uploaded", source="ui_storyboard_upload", subject_id=frame_id)
    await _ensure_pipeline_catchup(project_id)
    return {"imageUrl": _project_file_url(target.relative_to(PROJECT_DIR))}


@app.get("/api/projects/{project_id}/timeline")
async def get_project_timeline(project_id: str):
    return _workspace_snapshot(project_id)["timelineFrames"]


@app.get("/api/projects/{project_id}/timeline/dialogue")
async def get_project_timeline_dialogue(project_id: str):
    return _workspace_snapshot(project_id)["dialogueBlocks"]


@app.put("/api/projects/{project_id}/timeline/{frame_id}")
async def update_project_timeline_frame(project_id: str, frame_id: str, req: TimelineFrameUpdateRequest):
    _assert_project(project_id)
    overrides = _timeline_overrides()
    snapshot_before = _workspace_snapshot(project_id)
    current_frame = next((item for item in snapshot_before["timelineFrames"] if item["id"] == frame_id), None)
    if not current_frame:
        raise HTTPException(status_code=404, detail=f"Frame '{frame_id}' not found")

    previous_dialogue_id = current_frame.get("dialogueId")
    if req.prompt is not None:
        _set_timeline_frame_prompt(frame_id, req.prompt, overrides)

    if req.dialogueId is not None:
        new_dialogue_id = req.dialogueId or None
        if previous_dialogue_id and previous_dialogue_id != new_dialogue_id:
            previous_dialogue = next((item for item in snapshot_before["dialogueBlocks"] if item["id"] == previous_dialogue_id), None)
            if previous_dialogue and len(previous_dialogue.get("linkedFrameIds") or []) <= 1:
                raise HTTPException(status_code=400, detail="A dialogue block must keep at least one linked frame")
        _set_timeline_frame_dialogue(frame_id, new_dialogue_id, overrides)
        if previous_dialogue_id and previous_dialogue_id != new_dialogue_id:
            _redistribute_dialogue_frames(project_id, previous_dialogue_id, overrides)
        if new_dialogue_id:
            _redistribute_dialogue_frames(project_id, new_dialogue_id, overrides)

    if req.duration is not None:
        if previous_dialogue_id and req.dialogueId is None:
          _redistribute_dialogue_frames(
              project_id,
              previous_dialogue_id,
              overrides,
              pinned_frame_id=frame_id,
              pinned_duration=req.duration,
          )
        elif req.dialogueId:
            new_dialogue_id = req.dialogueId or None
            if new_dialogue_id:
                _redistribute_dialogue_frames(
                    project_id,
                    new_dialogue_id,
                    overrides,
                    pinned_frame_id=frame_id,
                    pinned_duration=req.duration,
                )
            else:
                _set_timeline_frame_duration(frame_id, req.duration, overrides)
        else:
            _set_timeline_frame_duration(frame_id, req.duration, overrides)

    if req.trimStart is not None or req.trimEnd is not None:
        _set_timeline_frame_trim(
            frame_id,
            overrides,
            trim_start=req.trimStart,
            trim_end=req.trimEnd,
        )

    _save_timeline_overrides(overrides)
    await _ensure_pipeline_catchup(project_id)

    timeline = _workspace_snapshot(project_id)["timelineFrames"]
    frame = next((item for item in timeline if item["id"] == frame_id), None)
    if not frame:
        raise HTTPException(status_code=404, detail=f"Frame '{frame_id}' not found")
    return frame


@app.post("/api/projects/{project_id}/timeline/{frame_id}/regenerate")
async def regenerate_project_timeline_frame(project_id: str, frame_id: str):
    _assert_project(project_id)
    overrides = _timeline_overrides()
    expanded = next((item for item in overrides.get("expandedFrames") or [] if item.get("id") == frame_id), None)
    source_frame_id = expanded.get("sourceFrameId") if expanded else frame_id

    image_prompt_path = _timeline_prompt_path(source_frame_id, "image")
    prompt_data = load_json(image_prompt_path, {})
    if not prompt_data:
        raise HTTPException(status_code=404, detail=f"Prompt data for frame '{frame_id}' not found")

    prompt_text = expanded.get("prompt") if expanded else prompt_data.get("prompt")
    output_rel = f"frames/composed/{frame_id}_gen.png"
    request = GenerateFrameRequest(
        prompt=prompt_text or prompt_data.get("prompt") or "",
        size=prompt_data.get("size") or "landscape_16_9",
        output_path=output_rel,
        output_format="png",
        reference_images=list(prompt_data.get("reference_images") or prompt_data.get("ref_images") or []),
        storyboard_image=prompt_data.get("storyboard_image"),
        frame_id=frame_id,
        phase="ui_regenerate_frame",
    )
    result = await generate_frame(request)

    if expanded is not None:
        expanded["imageRel"] = output_rel
        _save_timeline_overrides(overrides)

    _mark_timeline_video_dirty("timeline_frame_regenerated", source="ui_timeline_regenerate", subject_id=frame_id)
    await _ensure_pipeline_catchup(project_id)

    timeline = _workspace_snapshot(project_id)["timelineFrames"]
    frame = next((item for item in timeline if item["id"] == frame_id), None)
    return frame or result


@app.delete("/api/projects/{project_id}/timeline/{frame_id}")
async def remove_project_timeline_frame(project_id: str, frame_id: str):
    _assert_project(project_id)
    overrides = _timeline_overrides()
    snapshot = _workspace_snapshot(project_id)
    current_frame = next((item for item in snapshot["timelineFrames"] if item["id"] == frame_id), None)
    if not current_frame:
        raise HTTPException(status_code=404, detail=f"Frame '{frame_id}' not found")
    dialogue_id = current_frame.get("dialogueId")
    if dialogue_id:
        dialogue = next((item for item in snapshot["dialogueBlocks"] if item["id"] == dialogue_id), None)
        if dialogue and len(dialogue.get("linkedFrameIds") or []) <= 1:
            raise HTTPException(status_code=400, detail="A dialogue block must keep at least one linked frame")

    expanded_frames = overrides.get("expandedFrames") or []
    existing_len = len(expanded_frames)
    overrides["expandedFrames"] = [item for item in expanded_frames if item.get("id") != frame_id]
    if len(overrides["expandedFrames"]) == existing_len:
        hidden = set(overrides.get("hiddenFrameIds") or [])
        hidden.add(frame_id)
        overrides["hiddenFrameIds"] = sorted(hidden)
    if dialogue_id:
        _redistribute_dialogue_frames(project_id, dialogue_id, overrides)
    _save_timeline_overrides(overrides)
    await _ensure_pipeline_catchup(project_id)
    return {"ok": True}


@app.post("/api/projects/{project_id}/timeline/{frame_id}/expand")
async def expand_project_timeline_frame(project_id: str, frame_id: str, req: TimelineExpandRequest):
    _assert_project(project_id)
    direction = "before" if str(req.direction).lower() == "before" else "after"
    timeline = _workspace_snapshot(project_id)["timelineFrames"]
    source_frame = next((item for item in timeline if item["id"] == frame_id), None)
    if not source_frame:
        raise HTTPException(status_code=404, detail=f"Frame '{frame_id}' not found")

    overrides = _timeline_overrides()
    expanded_frames = overrides.get("expandedFrames") or []
    suffix = len(expanded_frames) + 1
    new_id = f"{frame_id}_{direction}_{suffix:02d}"
    image_rel = _timeline_image_rel_from_url(source_frame.get("imageUrl"))

    expanded_frames.append(
        {
            "id": new_id,
            "sourceFrameId": source_frame.get("sourceFrameId") or frame_id,
            "storyboardId": source_frame.get("storyboardId"),
            "direction": direction,
            "prompt": source_frame.get("prompt") or "",
            "duration": source_frame.get("duration") or 5,
            "dialogueId": source_frame.get("dialogueId"),
            "imageRel": image_rel,
        }
    )
    overrides["expandedFrames"] = expanded_frames
    _save_timeline_overrides(overrides)
    await _ensure_pipeline_catchup(project_id)

    refreshed = _workspace_snapshot(project_id)["timelineFrames"]
    return [item for item in refreshed if item["id"] == new_id]


@app.put("/api/projects/{project_id}/timeline/dialogue/{dialogue_id}")
async def update_project_timeline_dialogue(project_id: str, dialogue_id: str, req: TimelineDialogueUpdateRequest):
    _assert_project(project_id)
    overrides = _timeline_overrides()
    if req.text is not None:
        _set_timeline_dialogue_override(dialogue_id, "text", req.text, overrides)
    if req.character is not None:
        _set_timeline_dialogue_override(dialogue_id, "character", req.character, overrides)
    if req.startFrame is not None:
        _set_timeline_dialogue_override(dialogue_id, "startFrame", req.startFrame, overrides)
    if req.endFrame is not None:
        _set_timeline_dialogue_override(dialogue_id, "endFrame", req.endFrame, overrides)
    if req.duration is not None:
        _set_timeline_dialogue_override(dialogue_id, "duration", max(0.5, float(req.duration)), overrides)
        _redistribute_dialogue_frames(project_id, dialogue_id, overrides)
    _save_timeline_overrides(overrides)
    await _ensure_pipeline_catchup(project_id)
    dialogue = _workspace_snapshot(project_id)["dialogueBlocks"]
    block = next((item for item in dialogue if item["id"] == dialogue_id), None)
    if not block:
        raise HTTPException(status_code=404, detail=f"Dialogue '{dialogue_id}' not found")
    return block


@app.post("/api/projects/{project_id}/timeline/{frame_id}/edit")
async def edit_project_timeline_frame(project_id: str, frame_id: str, req: TimelineFrameEditRequest):
    _assert_project(project_id)
    if not req.prompt.strip():
        raise HTTPException(status_code=400, detail="Edit prompt is required")

    timeline = _workspace_snapshot(project_id)["timelineFrames"]
    frame = next((item for item in timeline if item["id"] == frame_id), None)
    if not frame:
        raise HTTPException(status_code=404, detail=f"Frame '{frame_id}' not found")
    image_rel = _timeline_image_rel_from_url(frame.get("imageUrl"))
    if not image_rel:
        raise HTTPException(status_code=400, detail="Frame has no image to edit")

    source_frame_id = frame.get("sourceFrameId") or frame_id
    image_prompt = load_json(_timeline_prompt_path(source_frame_id, "image"), {})
    base_prompt = str(image_prompt.get("prompt") or frame.get("prompt") or "").strip()
    edit_prompt = req.prompt.strip()
    if base_prompt:
        edit_prompt = (
            f"{edit_prompt}\n\n"
            f"Preserve continuity with the existing frame. Original frame intent:\n{base_prompt[:2000]}"
        )

    output_rel = f"frames/composed/{frame_id}_gen.png"
    request = EditImageRequest(
        input_path=image_rel,
        prompt=edit_prompt,
        size=image_prompt.get("size") or "landscape_16_9",
        output_path=output_rel,
        output_format="png",
        phase="ui_edit_frame",
    )
    result = await edit_image(request)

    overrides = _timeline_overrides()
    _set_timeline_frame_image(frame_id, output_rel, overrides)
    _save_timeline_overrides(overrides)
    await _ensure_pipeline_catchup(project_id)

    refreshed = _workspace_snapshot(project_id)["timelineFrames"]
    updated = next((item for item in refreshed if item["id"] == frame_id), None)
    return updated or result


@app.post("/api/projects/{project_id}/timeline/{frame_id}/upload")
async def upload_timeline_frame(project_id: str, frame_id: str, image: UploadFile = File(...)):
    _assert_project(project_id)
    target = frame_upload_path(PROJECT_DIR, frame_id, image.filename or ".png")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(await image.read())
    await _sync_project_asset_if_needed(PROJECT_DIR, target.relative_to(PROJECT_DIR))
    overrides = _timeline_overrides()
    for item in overrides.get("expandedFrames") or []:
        if item.get("id") == frame_id:
            item["imageRel"] = target.relative_to(PROJECT_DIR).as_posix()
            _save_timeline_overrides(overrides)
            break
    _mark_timeline_video_dirty("timeline_frame_uploaded", source="ui_timeline_upload", subject_id=frame_id)
    await _ensure_pipeline_catchup(project_id)
    return {"imageUrl": _project_file_url(target.relative_to(PROJECT_DIR))}


@app.get("/api/projects/{project_id}/workers")
async def get_project_workers(project_id: str):
    _assert_project(project_id)
    await _ensure_pipeline_catchup(project_id)
    return await _project_workers_payload_async(project_id)


@app.post("/api/projects/{project_id}/workers/cancel")
async def cancel_project_workers(project_id: str):
    _assert_project(project_id)
    cancelled_ids: list[str] = []
    if _queue_execution_enabled():
        for job in await _active_queue_jobs(project_id):
            if _queue_job_stop_phase_and_approvals(job) is None:
                continue
            await _cancel_queue_job(job)
            cancelled_ids.append(str(job.get("job_key") or ""))
        return {"cancelled": cancelled_ids}

    for job in list(_running_cancellable_jobs()):
        await _cancel_ui_pipeline_job(job)
        cancelled_ids.append(str(job.get("id") or ""))
    return {"cancelled": cancelled_ids}


@app.post("/api/workers/{worker_id}/cancel")
async def cancel_worker(worker_id: str):
    if _queue_execution_enabled():
        persistence = get_supabase_persistence(http_client)
        if persistence is None:
            raise HTTPException(status_code=503, detail="Queue persistence is unavailable")
        job = await persistence.get_pipeline_job(PROJECT_DIR.name, worker_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Worker '{worker_id}' not found")
        status = str(job.get("status") or "")
        if status not in {"queued", "running"} or _queue_job_stop_phase_and_approvals(job) is None:
            raise HTTPException(status_code=409, detail=f"Worker '{worker_id}' cannot be stopped")
        await _cancel_queue_job(job)
        return {"cancelled": worker_id}

    job = ui_pipeline_jobs.get(worker_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Worker '{worker_id}' not found")
    if job.get("status") != "running" or _job_stop_phase_and_approvals(job) is None:
        raise HTTPException(status_code=409, detail=f"Worker '{worker_id}' cannot be stopped")
    await _cancel_ui_pipeline_job(job)
    return {"cancelled": worker_id}


@app.get("/api/projects/{project_id}/chat")
async def get_project_chat(project_id: str):
    _assert_project(project_id)
    return _load_chat_history()


@app.post("/api/projects/{project_id}/chat")
async def send_project_chat(project_id: str, req: UIChatMessageRequest):
    _assert_project(project_id)
    history = _load_chat_history()
    user_message = {
        "id": f"user-{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        "role": "user",
        "content": req.content,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": req.mode,
        "focusTarget": req.focusTarget,
    }
    history.append(user_message)

    response_text = _run_morpheus_chat(req.content, req.focusTarget, req.focusTargets, req.mode)
    agent_message = {
        "id": f"agent-{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        "role": "agent",
        "content": response_text,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": req.mode,
    }
    history.append(agent_message)
    _save_chat_history(history)
    return {"response": agent_message}


@app.post("/api/projects/{project_id}/chat/focus")
async def set_project_chat_focus(project_id: str, req: UIFocusRequest):
    _assert_project(project_id)
    _save_chat_focus(req.focus)
    return {"ok": True}


@app.delete("/api/projects/{project_id}/chat")
async def clear_project_chat(project_id: str):
    _assert_project(project_id)
    _save_chat_history([])
    _save_chat_focus(None)
    return {"ok": True}


@app.websocket("/ws/projects/{project_id}")
async def project_events_socket(websocket: WebSocket, project_id: str):
    _assert_project(project_id)
    await websocket.accept()
    try:
        await websocket.send_json({"type": "connected", "data": {"projectId": project_id}})
        snapshot = await _workspace_snapshot_async(project_id)
        worker_snapshot = await _project_workers_payload_async(project_id)
        await websocket.send_json({"type": "workspace_update", "data": snapshot})
        await websocket.send_json({"type": "worker_snapshot", "data": worker_snapshot})
        await websocket.send_json({"type": "project_update", "data": snapshot["project"]})
        last_asset_revision = project_asset_revision
        last_full_push = time.monotonic()
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
            except asyncio.TimeoutError:
                if project_asset_revision != last_asset_revision:
                    last_asset_revision = project_asset_revision
                    snapshot = await _workspace_snapshot_async(project_id)
                    worker_snapshot = await _project_workers_payload_async(project_id)
                    await websocket.send_json({"type": "workspace_update", "data": snapshot})
                    await websocket.send_json({"type": "worker_snapshot", "data": worker_snapshot})
                    await websocket.send_json({"type": "project_update", "data": snapshot["project"]})
                    if project_asset_event is not None:
                        await websocket.send_json(project_asset_event)
                    last_full_push = time.monotonic()
                    continue
                if time.monotonic() - last_full_push < 5.0:
                    continue
                snapshot = await _workspace_snapshot_async(project_id)
                worker_snapshot = await _project_workers_payload_async(project_id)
                await websocket.send_json({"type": "workspace_update", "data": snapshot})
                await websocket.send_json({"type": "worker_snapshot", "data": worker_snapshot})
                await websocket.send_json({"type": "project_update", "data": snapshot["project"]})
                for worker in worker_snapshot:
                    await websocket.send_json({"type": "worker_update", "data": worker})
                last_full_push = time.monotonic()
    except WebSocketDisconnect:
        return


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

    duration = _normalize_video_request_duration(req.duration)

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
                suggested_duration=duration,
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
        "duration": result.duration or duration,
    }


# ---------------------------------------------------------------------------
# Layer 1: POST /internal/extend-video
# ---------------------------------------------------------------------------

@app.post("/internal/extend-video")
async def extend_video(req: ExtendVideoRequest):
    """Extend a generated Grok video clip via Replicate."""
    output = _resolve_output(req.output_path)
    input_video = Path(req.video_path)
    if not input_video.is_absolute():
        input_video = PROJECT_DIR / input_video
    if not input_video.exists():
        raise HTTPException(
            status_code=400,
            detail="video_path is required and must exist for video extension",
        )

    duration = _normalize_video_request_duration(req.duration)

    try:
        video_uri = await _upload_to_replicate(input_video)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to upload video for extension: {exc}") from exc

    pred_input: dict[str, Any] = {
        "video": video_uri,
        "duration": duration,
    }
    if req.prompt.strip():
        pred_input["prompt"] = req.prompt.strip()
    if isinstance(req.extra_params, dict):
        pred_input.update(req.extra_params)

    headers = {
        "Authorization": f"Bearer {REPLICATE_API_TOKEN}",
        "Content-Type": "application/json",
        "Prefer": "wait",
    }

    with activate_run_context(run_id=req.run_id or "", phase=req.phase):
        try:
            pred_data = await _replicate_predict(
                "xai/grok-imagine-video-extension",
                pred_input,
                headers,
            )
            prediction_id = pred_data.get("id")
            if pred_data.get("status") not in ("succeeded", "failed", "canceled"):
                pred_data = await _poll_replicate_prediction(prediction_id, headers)
        except httpx.HTTPError as exc:
            detail = exc.response.text if getattr(exc, "response", None) is not None else str(exc)
            raise HTTPException(status_code=502, detail=detail) from exc

    if pred_data.get("status") != "succeeded":
        detail = pred_data.get("error") or pred_data
        raise HTTPException(status_code=502, detail=detail)

    out = pred_data.get("output")
    if isinstance(out, list):
        output_url = next((item for item in out if isinstance(item, str)), "")
    else:
        output_url = out if isinstance(out, str) else ""
    if not output_url:
        raise HTTPException(status_code=502, detail="Video extension returned no output URL")

    output.parent.mkdir(parents=True, exist_ok=True)
    await _download_file(output_url, output)

    emit_event(
        PROJECT_DIR,
        event="video_extended",
        run_id=req.run_id or "",
        phase=req.phase,
        handler="video_extension",
        details={
            "path": str(output),
            "model": "xai/grok-imagine-video-extension",
            "duration": duration,
            "source": str(input_video),
        },
    )
    return {
        "success": True,
        "path": str(output),
        "prediction_id": pred_data.get("id", ""),
        "model": "xai/grok-imagine-video-extension",
        "duration": duration,
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
            duration = _normalize_video_request_duration(item.duration)
            inputs.append(VideoClipInput(
                frame_id=frame_id,
                dialogue_text=item.dialogue_text or "",
                motion_prompt=item.prompt,
                frame_image_path=frame_image,
                suggested_duration=duration,
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
