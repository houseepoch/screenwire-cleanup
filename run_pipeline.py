#!/usr/bin/env python3
"""ScreenWire AI — Headless Pipeline Runner (MVP Test Harness)

Drives the full ScreenWire AI pipeline from Phase 0 -> Phase 6 without
human input. Starts the FastAPI server, polls /health until ready, spawns
local Grok-backed agent runners for each phase sequentially, then runs Phase 6 export
programmatically via ffmpeg.

Usage:
    python3 run_pipeline.py [--dry-run] [--phase N]
"""

import argparse
import atexit
import hashlib
import json
import math
import os
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

from graph.feature_flags import ENABLE_STORYBOARD_GUIDANCE
from llm.xai_client import (
    DEFAULT_REASONING_MODEL,
    DEFAULT_STAGE1_REASONING_MODEL,
    SyncXAIClient,
    build_prompt_cache_key,
)
from screenwire_contracts import (
    creative_freedom_contract,
    default_dialogue_workflow,
    derive_frame_range_from_budget,
    derive_output_size_from_frame_budget,
    derive_output_size_label_from_frame_budget,
    minimum_scene_count_for_frame_budget,
    normalize_frame_budget,
)
from telemetry import PHASE_ENV, RUN_ID_ENV, emit_event, generate_run_id, with_run_context
from video_prompt_projection import build_video_request_projection, generate_video_prompt_projection

# ---------------------------------------------------------------------------
# Process tracking & graceful shutdown
# ---------------------------------------------------------------------------

_active_procs: list[subprocess.Popen] = []
_shutting_down = False


def _register_proc(proc: subprocess.Popen) -> None:
    """Track a child process for cleanup on interrupt."""
    _active_procs.append(proc)


def _unregister_proc(proc: subprocess.Popen) -> None:
    """Remove a finished process from tracking."""
    try:
        _active_procs.remove(proc)
    except ValueError:
        pass


def _shutdown_handler(signum, frame):
    """Handle Ctrl+C / SIGINT: kill all child processes, stop server, exit."""
    global _shutting_down
    if _shutting_down:
        # Second interrupt — force exit
        print(f"\n{RED}Force exit.{RESET}", flush=True)
        os._exit(1)

    _shutting_down = True
    sig_name = signal.Signals(signum).name if signum else "INTERRUPT"
    print(f"\n\n{BOLD}{YELLOW}╔═══════════════════════════════════════════╗{RESET}")
    print(f"{BOLD}{YELLOW}║  {sig_name} received — shutting down...     ║{RESET}")
    print(f"{BOLD}{YELLOW}╚═══════════════════════════════════════════╝{RESET}\n")

    # Kill all tracked child processes
    active = list(_active_procs)
    if active:
        print(f"{YELLOW}Killing {len(active)} active process(es)...{RESET}", flush=True)
        for proc in active:
            try:
                proc.terminate()
                print(f"  TERM → PID {proc.pid}", flush=True)
            except OSError:
                pass
        # Give them a moment, then force kill
        time.sleep(2)
        for proc in active:
            try:
                if proc.poll() is None:
                    proc.kill()
                    print(f"  KILL → PID {proc.pid}", flush=True)
            except OSError:
                pass

    # Stop the server
    stop_server()

    print(f"\n{GREEN}Cleanup complete. Pipeline interrupted.{RESET}", flush=True)
    sys.exit(130)


signal.signal(signal.SIGINT, _shutdown_handler)
signal.signal(signal.SIGTERM, _shutdown_handler)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

APP_DIR = Path(__file__).resolve().parent
PROJECTS_DIR = APP_DIR / "projects"
PROJECT_DIR: Path | None = None  # Set in main() via --project
PROMPTS_DIR = APP_DIR / "agent_prompts"
SKILLS_DIR = APP_DIR / "skills"
SERVER_URL = "http://localhost:8000"
SERVER_SCRIPT = APP_DIR / "server.py"

PHASE_TIMEOUT = None       # No timeout — agents run until complete
SERVER_START_TIMEOUT = 30  # seconds to wait for server to come up
SERVER_POLL_INTERVAL = 1   # seconds between /health polls

DEFAULT_MODEL = DEFAULT_REASONING_MODEL
AUDIT_PHASE2 = False  # Set to True via --audit flag to spawn graph auditor agent after Step 2c
FRAME_GEN_CONCURRENCY = 10
VIDEO_GEN_CONCURRENCY = max(1, int(os.getenv("SCREENWIRE_VIDEO_CONCURRENCY", "2")))
VIDEO_REFINE_CONCURRENCY = max(1, int(os.getenv("SCREENWIRE_VIDEO_REFINE_CONCURRENCY", "5")))
VIDEO_GEN_RETRIES = max(1, int(os.getenv("SCREENWIRE_VIDEO_RETRIES", "3")))

AGENT_RUNNER_CMD = [sys.executable, "-m", "llm.agent_runner"]

LOGS_DIR: Path | None = None          # Set in main() after --project
PIPELINE_LOGS_DIR: Path | None = None  # Set in main() after --project
PIPELINE_RUN_ID = ""
LIVE_MODE = False
LIVE_ENV = "SCREENWIRE_LIVE"


def _with_repo_pythonpath(env: dict[str, str]) -> dict[str, str]:
    repo_root = str(APP_DIR)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = repo_root if not existing else f"{repo_root}{os.pathsep}{existing}"
    return env

# Phase names for reporting
PHASE_NAMES = {
    0: "Project Scaffold",
    1: "Narrative Contracts + Parallel Prose",
    2: "Graph Construction",
    3: "Asset Generation + Storyboards + Quality Gate",
    4: "Frame Composition",
    5: "Video Generation",
    6: "Final Export",
}

# ---------------------------------------------------------------------------
# Colours & Logging
# ---------------------------------------------------------------------------

RESET  = "\033[0m"
BOLD   = "\033[1m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
CYAN   = "\033[36m"
DIM    = "\033[2m"


def log(msg: str, color: str = RESET) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"{DIM}[{ts}]{RESET} {color}{msg}{RESET}", flush=True)


def log_header(msg: str) -> None:
    bar = "=" * 60
    print(f"\n{BOLD}{CYAN}{bar}{RESET}")
    print(f"{BOLD}{CYAN}  {msg}{RESET}")
    print(f"{BOLD}{CYAN}{bar}{RESET}", flush=True)


def log_ok(msg: str) -> None:
    log(f"OK: {msg}", GREEN)


def log_warn(msg: str) -> None:
    log(f"WARN: {msg}", YELLOW)


def log_err(msg: str) -> None:
    log(f"FAIL: {msg}", RED)


def fail(msg: str) -> None:
    log_err(msg)
    sys.exit(1)


def _phase_label(phase_num: int) -> str:
    return f"phase_{phase_num}"


def live_log(msg: str, color: str = CYAN) -> None:
    """Emit a console progress line only when live mode is enabled."""
    if LIVE_MODE:
        log(msg, color)


def _format_eta(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _progress_eta_suffix(*, started_at: float, completed: int, total: int) -> str:
    """Return a short ETA suffix for live progress lines."""
    if completed <= 0 or completed >= total:
        return ""
    elapsed = max(time.time() - started_at, 0.001)
    rate = completed / elapsed
    if rate <= 0:
        return ""
    eta_seconds = (total - completed) / rate
    return f", ETA {_format_eta(eta_seconds)}"


# ---------------------------------------------------------------------------
# Streaming subprocess helper
# ---------------------------------------------------------------------------

def _stream_subprocess(
    cmd,
    cwd=None,
    env=None,
    timeout=None,
    label="process",
    echo_stdout: bool = True,
    echo_stderr: bool = True,
):
    """Run a subprocess, streaming stdout/stderr in real-time while capturing them.

    Uses os.read() on non-blocking file descriptors instead of readline()
    so that output appears immediately even when the child process doesn't
    emit newlines (e.g. agent runner buffering).

    Returns a subprocess.CompletedProcess with stdout/stderr populated.
    """
    import fcntl as _fcntl
    import select as _sel

    if _shutting_down:
        raise KeyboardInterrupt("Pipeline shutting down")

    proc = subprocess.Popen(
        cmd, cwd=str(cwd) if cwd else None, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    _register_proc(proc)

    # Set pipes to non-blocking so os.read won't hang
    for pipe in (proc.stdout, proc.stderr):
        fd = pipe.fileno()
        flags = _fcntl.fcntl(fd, _fcntl.F_GETFL)
        _fcntl.fcntl(fd, _fcntl.F_SETFL, flags | os.O_NONBLOCK)

    stdout_parts: list[bytes] = []
    stderr_parts: list[bytes] = []
    start = time.time()

    try:
        while True:
            if _shutting_down:
                proc.kill()
                proc.wait()
                raise KeyboardInterrupt("Pipeline shutting down")

            if timeout and (time.time() - start) > timeout:
                proc.kill()
                proc.wait()
                raise subprocess.TimeoutExpired(cmd, timeout)

            readable, _, _ = _sel.select(
                [proc.stdout, proc.stderr], [], [], 0.5,
            )
            for stream in readable:
                try:
                    chunk = os.read(stream.fileno(), 8192)
                except OSError:
                    chunk = b""
                if not chunk:
                    continue
                text = chunk.decode("utf-8", errors="replace")
                if stream is proc.stdout:
                    stdout_parts.append(chunk)
                    if echo_stdout:
                        for line in text.splitlines(keepends=True):
                            print(f"{DIM}[{label}]{RESET} {line}", end="", flush=True)
                else:
                    stderr_parts.append(chunk)
                    if echo_stderr:
                        for line in text.splitlines(keepends=True):
                            print(f"{DIM}[{label}]{RESET} {RED}{line}{RESET}", end="", flush=True)

            if proc.poll() is not None:
                # Drain remaining data from pipes
                for stream, parts, color in (
                    (proc.stdout, stdout_parts, ""),
                    (proc.stderr, stderr_parts, RED),
                ):
                    while True:
                        try:
                            chunk = os.read(stream.fileno(), 8192)
                        except OSError:
                            break
                        if not chunk:
                            break
                        parts.append(chunk)
                        text = chunk.decode("utf-8", errors="replace")
                        should_echo = (stream is proc.stdout and echo_stdout) or (
                            stream is proc.stderr and echo_stderr
                        )
                        if should_echo:
                            for line in text.splitlines(keepends=True):
                                print(f"{DIM}[{label}]{RESET} {color}{line}"
                                      f"{RESET if color else ''}", end="", flush=True)
                break
    except subprocess.TimeoutExpired:
        _unregister_proc(proc)
        raise
    except KeyboardInterrupt:
        _unregister_proc(proc)
        raise
    except Exception:
        proc.kill()
        proc.wait()
        _unregister_proc(proc)
        raise

    _unregister_proc(proc)
    return subprocess.CompletedProcess(
        cmd, returncode=proc.returncode,
        stdout=b"".join(stdout_parts).decode("utf-8", errors="replace"),
        stderr=b"".join(stderr_parts).decode("utf-8", errors="replace"),
    )


# ---------------------------------------------------------------------------
# Timing helpers
# ---------------------------------------------------------------------------

class Timer:
    def __init__(self) -> None:
        self.start = time.time()

    def elapsed(self) -> float:
        return time.time() - self.start

    def elapsed_str(self) -> str:
        s = self.elapsed()
        if s < 60:
            return f"{s:.1f}s"
        m = int(s // 60)
        return f"{m}m {s % 60:.0f}s"


# ---------------------------------------------------------------------------
# Server management
# ---------------------------------------------------------------------------

_server_proc: subprocess.Popen | None = None
_server_log_fh = None  # file handle kept open for server lifetime


def start_server(dry_run: bool) -> None:
    """Start the FastAPI server as a background subprocess unless already up."""
    global _server_proc, _server_log_fh

    if _server_already_running():
        log_ok("Server already running on port 8000 -- skipping start")
        return

    if dry_run:
        log(f"[DRY-RUN] Would start server: python3 {SERVER_SCRIPT}", YELLOW)
        return

    log(f"Starting FastAPI server: python3 {SERVER_SCRIPT}")

    # Redirect server output to a log file instead of piping (avoids buffer deadlock)
    server_log_path = LOGS_DIR / "server.log"
    server_log_path.parent.mkdir(parents=True, exist_ok=True)
    _server_log_fh = open(server_log_path, "w")

    env = with_run_context(
        {**os.environ, "PROJECT_DIR": str(PROJECT_DIR)},
        run_id=PIPELINE_RUN_ID,
        phase=os.environ.get(PHASE_ENV, "pipeline_boot"),
    )
    _server_proc = subprocess.Popen(
        [sys.executable, str(SERVER_SCRIPT)],
        cwd=str(APP_DIR),
        env=env,

        stdout=_server_log_fh,
        stderr=_server_log_fh,
    )
    _register_proc(_server_proc)
    log(f"Server PID: {_server_proc.pid}  (log: {server_log_path})")


def _server_already_running() -> bool:
    try:
        import urllib.request
        urllib.request.urlopen(f"{SERVER_URL}/health", timeout=2)
        return True
    except Exception:
        return False


def wait_for_server(dry_run: bool) -> None:
    """Poll /health until the server responds or times out."""
    if dry_run:
        log("[DRY-RUN] Would poll /health until ready", YELLOW)
        return

    import urllib.request
    log(f"Waiting for server at {SERVER_URL}/health ...")
    deadline = time.time() + SERVER_START_TIMEOUT
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{SERVER_URL}/health", timeout=2)
            log_ok("Server is ready")
            return
        except Exception:
            time.sleep(SERVER_POLL_INTERVAL)

    # Dump the server log tail for diagnosis
    server_log_path = LOGS_DIR / "server.log"
    if server_log_path.exists():
        tail = server_log_path.read_text()[-2000:]
        if tail:
            print(f"\n--- server.log tail ---\n{tail}\n--- END ---\n", flush=True)

    fail(f"Server did not come up within {SERVER_START_TIMEOUT}s")


def stop_server() -> None:
    """Terminate the server subprocess if we started it."""
    global _server_log_fh
    if _server_proc:
        log("Stopping server ...")
        _server_proc.terminate()
        try:
            _server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _server_proc.kill()
        _unregister_proc(_server_proc)
    if _server_log_fh:
        _server_log_fh.close()
        _server_log_fh = None


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

MANIFEST_PATH: Path | None = None  # Set in main() after --project


def read_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text())


def write_manifest(manifest: dict) -> None:
    tmp_path = MANIFEST_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(manifest, indent=2))
    os.replace(tmp_path, MANIFEST_PATH)


def advance_phase(completed: int, next_phase: int, dry_run: bool) -> dict:
    """Mark phase N complete and phase N+1 ready in the manifest.

    Merges into existing phase data rather than overwriting.
    Guarded by dry_run -- no manifest changes in dry-run mode.
    """
    if dry_run:
        log(f"[DRY-RUN] Would advance manifest: phase_{completed} complete -> phase_{next_phase} ready", YELLOW)
        return {}

    manifest = read_manifest()

    # Merge into existing phase data (preserve any fields the agent wrote)
    phase_data = manifest.get("phases", {}).get(f"phase_{completed}", {})
    phase_data["status"] = "complete"
    phase_data["completedAt"] = datetime.now(timezone.utc).isoformat()
    manifest.setdefault("phases", {})[f"phase_{completed}"] = phase_data

    if next_phase <= 6:
        next_data = manifest.get("phases", {}).get(f"phase_{next_phase}", {})
        next_data["status"] = "ready"
        manifest["phases"][f"phase_{next_phase}"] = next_data

    manifest["status"] = f"phase_{completed}_complete"
    manifest["version"] = manifest.get("version", 1) + 1
    write_manifest(manifest)
    log_ok(f"Manifest updated: phase_{completed} complete -> phase_{next_phase} ready")
    return manifest


def mark_project_complete(export_path: str,
                          export_duration: float, export_size_bytes: int,
                          export_codec: str, export_resolution: str) -> None:
    """Re-reads manifest fresh, then marks project complete."""
    manifest = read_manifest()

    phase_data = manifest.get("phases", {}).get("phase_6", {})
    phase_data["status"] = "complete"
    phase_data["completedAt"] = datetime.now(timezone.utc).isoformat()
    manifest.setdefault("phases", {})["phase_6"] = phase_data

    manifest["project"] = {
        "status": "complete",
        "completedAt": datetime.now(timezone.utc).isoformat(),
        "exportPath": export_path,
        "exportDuration": round(export_duration, 2),
        "exportFileSize": f"{export_size_bytes // (1024 * 1024)}MB",
        "exportCodec": export_codec,
        "exportResolution": export_resolution,
    }
    manifest["status"] = "complete"
    manifest["version"] = manifest.get("version", 1) + 1
    write_manifest(manifest)


# ---------------------------------------------------------------------------
# Phase report saving
# ---------------------------------------------------------------------------

def save_phase_report(phase_num: int, timer: Timer, agent_id: str,
                      result: subprocess.CompletedProcess | None,
                      created_files: list[Path] | None = None) -> None:
    """Save a JSON report for the phase to logs/pipeline/phase_N_report.json."""
    PIPELINE_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = PIPELINE_LOGS_DIR / f"phase_{phase_num}_report.json"

    file_info = []
    if created_files:
        for p in created_files:
            if p.exists():
                file_info.append({
                    "path": str(p),
                    "size_bytes": p.stat().st_size,
                })

    stdout_text = (result.stdout or "") if result else ""
    stderr_text = (result.stderr or "") if result else ""
    stdout_lines = stdout_text.splitlines()
    stderr_lines = stderr_text.splitlines()

    report = {
        "phase": phase_num,
        "name": PHASE_NAMES.get(phase_num, "unknown"),
        "agent_id": agent_id,
        "duration_seconds": round(timer.elapsed(), 2),
        "duration_human": timer.elapsed_str(),
        "exit_code": result.returncode if result else None,
        "files_created": file_info,
        "stdout_first_20": stdout_lines[:20],
        "stdout_last_20": stdout_lines[-20:] if len(stdout_lines) > 20 else [],
        "stderr_first_20": stderr_lines[:20],
        "stderr_last_20": stderr_lines[-20:] if len(stderr_lines) > 20 else [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    report_path.write_text(json.dumps(report, indent=2))
    log(f"Phase report saved: {report_path}")


def collect_files_in(directory: Path) -> list[Path]:
    """Recursively collect all files in a directory."""
    if not directory.exists():
        return []
    return sorted(p for p in directory.rglob("*") if p.is_file())


def print_file_summary(label: str, files: list[Path]) -> None:
    """Print a summary of files with sizes."""
    if not files:
        return
    log(f"Files in {label}:")
    for p in files:
        size = p.stat().st_size
        size_str = f"{size:,}B" if size < 1024 else f"{size // 1024:,}KB"
        print(f"    {p.name}  ({size_str})", flush=True)
    print(f"    --- {len(files)} file(s) total ---", flush=True)


# ---------------------------------------------------------------------------
# Prompt caching: include expansion + shared conventions deployment
# ---------------------------------------------------------------------------

_INCLUDE_RE = re.compile(r'\{\{include:(.+?)\}\}')


def _expand_includes(text: str, base_dir: Path) -> str:
    """Replace {{include:path}} markers with file contents."""
    def _replacer(match: re.Match) -> str:
        inc_path = base_dir / match.group(1)
        if inc_path.exists():
            return inc_path.read_text()
        log_warn(f"Include file not found: {inc_path}")
        return match.group(0)
    return _INCLUDE_RE.sub(_replacer, text)


def _deploy_shared_conventions(project_dir: Path) -> None:
    """Copy shared_conventions.md → project_dir/CLAUDE.md if stale or missing."""
    source = APP_DIR / "shared_conventions.md"
    target = project_dir / "CLAUDE.md"
    if not source.exists():
        return
    if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
        return  # already up to date
    import shutil
    shutil.copy2(source, target)
    log(f"Deployed CLAUDE.md → {target.relative_to(project_dir)}")


def _deploy_project_reporting_assets(project_dir: Path) -> None:
    """Ensure each project has the concatenation script and report dirs."""
    template_script = PROJECTS_DIR / "_template" / "scripts" / "concatenate_project_snapshot.py"
    target_script = project_dir / "scripts" / "concatenate_project_snapshot.py"
    target_script.parent.mkdir(parents=True, exist_ok=True)
    (project_dir / "reports" / "archive").mkdir(parents=True, exist_ok=True)

    if not template_script.exists():
        log_warn(f"Project report template missing: {template_script}")
        return

    source_text = template_script.read_text(encoding="utf-8")
    current_text = target_script.read_text(encoding="utf-8") if target_script.exists() else None
    if current_text != source_text:
        target_script.write_text(source_text, encoding="utf-8")
        target_script.chmod(0o755)
        log(f"Deployed project report script → {target_script.relative_to(project_dir)}")


def _run_project_report(project_dir: Path) -> None:
    """Generate the per-project concatenated report after prompts are assembled."""
    _deploy_project_reporting_assets(project_dir)
    report_script = project_dir / "scripts" / "concatenate_project_snapshot.py"
    report_result = _stream_subprocess(
        [sys.executable, str(report_script), "--project-dir", str(project_dir)],
        cwd=project_dir,
        label="project_report",
    )
    if report_result.returncode != 0:
        raise RuntimeError("Project report generation failed after prompt assembly.")
    generate_video_prompt_projection(project_dir)
    log_ok("Project report generated")


def _read_json_file(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _find_existing_rel_path(project_dir: Path, candidates: list[str]) -> str | None:
    for rel in candidates:
        if not rel:
            continue
        if (project_dir / rel).exists():
            return rel
    return None


def _project_cover_entity_candidates(project_dir: Path) -> list[dict[str, Any]]:
    graph = _read_json_file(project_dir / "graph" / "narrative_graph.json", {})
    if not isinstance(graph, dict):
        return []

    frame_sets: dict[tuple[str, str], set[str]] = {}

    for state in (graph.get("cast_frame_states") or {}).values():
        if not isinstance(state, dict):
            continue
        cast_id = str(state.get("cast_id") or "").strip()
        frame_id = str(state.get("frame_id") or "").strip()
        if cast_id and frame_id:
            frame_sets.setdefault(("cast", cast_id), set()).add(frame_id)

    for state in (graph.get("prop_frame_states") or {}).values():
        if not isinstance(state, dict):
            continue
        prop_id = str(state.get("prop_id") or "").strip()
        frame_id = str(state.get("frame_id") or "").strip()
        if prop_id and frame_id:
            frame_sets.setdefault(("prop", prop_id), set()).add(frame_id)

    for frame_id, frame in (graph.get("frames") or {}).items():
        if not isinstance(frame, dict):
            continue
        location_id = str(frame.get("location_id") or "").strip()
        if location_id:
            frame_sets.setdefault(("location", location_id), set()).add(str(frame_id))

    counts: list[dict[str, Any]] = []
    for (entity_type, entity_id), frames in frame_sets.items():
        if entity_type == "cast":
            registry = graph.get("cast") or {}
            node = registry.get(entity_id) or {}
            identity = node.get("identity") or {}
            name = node.get("display_name") or node.get("name") or entity_id
            description = (
                node.get("description")
                or identity.get("physical_description")
                or identity.get("wardrobe_description")
                or node.get("personality")
                or ""
            )
            image_rel = _find_existing_rel_path(
                project_dir,
                [
                    f"cast/composites/{entity_id}_ref.png",
                    f"cast/composites/{entity_id}_ref.jpg",
                    f"cast/composites/{entity_id}.png",
                    f"cast/composites/{entity_id}.jpg",
                ],
            )
        elif entity_type == "location":
            registry = graph.get("locations") or {}
            node = registry.get(entity_id) or {}
            name = node.get("name") or entity_id
            description = node.get("description") or node.get("atmosphere") or ""
            image_rel = _find_existing_rel_path(
                project_dir,
                [
                    f"locations/primary/{entity_id}.png",
                    f"locations/primary/{entity_id}.jpg",
                ],
            )
        else:
            registry = graph.get("props") or {}
            node = registry.get(entity_id) or {}
            name = node.get("name") or entity_id
            description = node.get("description") or node.get("narrative_significance") or ""
            image_rel = _find_existing_rel_path(
                project_dir,
                [
                    f"props/generated/{entity_id}.png",
                    f"props/generated/{entity_id}.jpg",
                ],
            )

        counts.append(
            {
                "entityType": entity_type,
                "entityId": entity_id,
                "name": str(name),
                "description": str(description).strip(),
                "frameCount": len(frames),
                "imagePath": image_rel,
            }
        )

    counts.sort(
        key=lambda item: (
            -int(item.get("frameCount") or 0),
            str(item.get("entityType") or ""),
            str(item.get("name") or ""),
        )
    )
    return counts


def _build_project_cover_summary(project_dir: Path) -> dict[str, Any]:
    onboarding = _read_onboarding_config(project_dir)
    manifest = _read_json_file(project_dir / "project_manifest.json", {})
    skeleton_md = (project_dir / "creative_output" / "outline_skeleton.md").read_text(encoding="utf-8") if (project_dir / "creative_output" / "outline_skeleton.md").exists() else ""
    creative_md = (project_dir / "creative_output" / "creative_output.md").read_text(encoding="utf-8") if (project_dir / "creative_output" / "creative_output.md").exists() else ""

    top_entities = _project_cover_entity_candidates(project_dir)[:3]
    project_name = manifest.get("projectName") or onboarding.get("projectName") or project_dir.name
    media_style = onboarding.get("mediaStyle") or "live_clear"
    media_style_prefix = onboarding.get("mediaStylePrefix") or ""

    outline_excerpt = skeleton_md[:5000].strip()
    creative_excerpt = creative_md.strip()
    if len(creative_excerpt) > 4200:
        creative_excerpt = creative_excerpt[:2600].strip() + "\n...\n" + creative_excerpt[-1400:].strip()

    top_entity_lines = []
    for index, item in enumerate(top_entities, start=1):
        desc = f" — {item['description']}" if item.get("description") else ""
        top_entity_lines.append(
            f"{index}. {item['name']} ({item['entityType']}, {item['frameCount']} frame(s)){desc}"
        )

    fallback_summary = (
        f"{project_name} centers on {', '.join(item['name'] for item in top_entities[:2])}"
        if top_entities
        else f"{project_name} unfolds as a cinematic narrative with escalating character and environment conflict."
    )

    summary_prompt = "\n".join(
        [
            "Write a concise theatrical poster summary for a film project.",
            "Output exactly two parts:",
            "1. A 2-sentence summary in plain prose.",
            "2. On a new line: TAGLINE: <6-12 words>",
            "Do not use bullets. Do not mention software, pipelines, or production tooling.",
            "",
            f"Title: {project_name}",
            f"Media style: {media_style}",
            "Top 3 most used entities:",
            *(top_entity_lines or ["1. No entity counts available"]),
            "",
            "Outline excerpt:",
            outline_excerpt or "(missing)",
            "",
            "Creative output excerpt:",
            creative_excerpt or "(missing)",
        ]
    )

    summary_text = fallback_summary
    tagline = ""
    try:
        client = SyncXAIClient()
        raw = client.generate_text(
            prompt=summary_prompt,
            model=DEFAULT_REASONING_MODEL,
            task_hint="project_cover_summary",
            temperature=0.4,
            max_tokens=220,
            cache_key=build_prompt_cache_key("project-cover-summary", project_dir.name, project_name),
        )
        parsed_lines = [line.strip() for line in raw.splitlines() if line.strip()]
        prose_lines = [line for line in parsed_lines if not line.upper().startswith("TAGLINE:")]
        summary_text = " ".join(prose_lines).strip() or fallback_summary
        for line in parsed_lines:
            if line.upper().startswith("TAGLINE:"):
                tagline = line.split(":", 1)[1].strip()
                break
    except Exception as exc:
        log_warn(f"Project cover summary generation failed: {exc}")

    return {
        "projectName": project_name,
        "mediaStyle": media_style,
        "mediaStylePrefix": media_style_prefix,
        "summary": summary_text,
        "tagline": tagline,
        "topEntities": top_entities,
    }


def _write_project_cover_summary(project_dir: Path, payload: dict[str, Any]) -> Path:
    reports_dir = project_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    summary_path = reports_dir / "project_cover_summary.md"
    meta_path = reports_dir / "project_cover_meta.json"

    lines = [
        "# Project Cover Summary",
        "",
        f"- Project: `{payload.get('projectName', project_dir.name)}`",
        f"- Media Style: `{payload.get('mediaStyle', 'live_clear')}`",
        "",
        "## Grok Summary",
        "",
        payload.get("summary", "").strip() or "(missing)",
        "",
    ]
    if payload.get("tagline"):
        lines.extend(["## Tagline", "", payload["tagline"], ""])
    lines.append("## Top 3 Most Used Entities")
    lines.append("")
    top_entities = payload.get("topEntities") or []
    if top_entities:
        for item in top_entities:
            desc = f" — {item['description']}" if item.get("description") else ""
            lines.append(
                f"- `{item['name']}` (`{item['entityType']}`, `{item['frameCount']}` frame(s)){desc}"
            )
    else:
        lines.append("- No entity counts available")
    lines.append("")

    summary_path.write_text("\n".join(lines), encoding="utf-8")
    meta_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return summary_path


def _generate_project_cover_art(project_dir: Path, *, dry_run: bool) -> None:
    if dry_run:
        log("[DRY-RUN] Would generate project cover artwork poster", YELLOW)
        return

    payload = _build_project_cover_summary(project_dir)
    summary_path = _write_project_cover_summary(project_dir, payload)
    reports_dir = project_dir / "reports"
    cover_path = reports_dir / "project_cover.png"

    top_entities = payload.get("topEntities") or []
    ref_images = [str(project_dir / item["imagePath"]) for item in top_entities if item.get("imagePath")]
    ref_images = ref_images[:5]

    focus_names = ", ".join(item["name"] for item in top_entities[:3]) or "the core cast and world"
    summary = payload.get("summary", "").strip()
    tagline = payload.get("tagline", "").strip()
    media_prefix = payload.get("mediaStylePrefix", "")
    prompt_parts = [
        media_prefix.strip(),
        f'Movie cover artwork poster for "{payload.get("projectName", project_dir.name)}".',
        summary,
        f"Feature {focus_names} in a single premium theatrical one-sheet composition.",
        "Emotionally charged cinematic key art, iconic focal hierarchy, dramatic lighting, polished poster finish.",
        "No text, no title treatment, no credits, no logo, no watermark, no split panels, no collage grid.",
    ]
    if tagline:
        prompt_parts.insert(3, f'Poster feeling: "{tagline}".')
    cover_prompt = " ".join(part.strip() for part in prompt_parts if part and part.strip())

    args = [
        sys.executable,
        str(SKILLS_DIR / "sw_fresh_generation"),
        "--prompt",
        cover_prompt,
        "--size",
        "portrait_2_3",
        "--out",
        str(cover_path),
        "--run-id",
        os.environ.get(RUN_ID_ENV, PIPELINE_RUN_ID),
        "--phase",
        os.environ.get(PHASE_ENV, ""),
    ]
    if ref_images:
        args.extend(["--ref-images", ",".join(ref_images)])

    result = _stream_subprocess(
        args,
        cwd=project_dir,
        timeout=240,
        label="project_cover",
    )
    if result.returncode != 0:
        log_warn("Project cover artwork generation failed")
        return

    meta_path = reports_dir / "project_cover_meta.json"
    payload["imagePath"] = "reports/project_cover.png"
    payload["summaryPath"] = str(summary_path.relative_to(project_dir).as_posix())
    payload["prompt"] = cover_prompt
    payload["model"] = "google/nano-banana-2 -> google/nano-banana-pro"
    payload["generatedAt"] = datetime.now(timezone.utc).isoformat()
    meta_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    log_ok("Project cover artwork generated")


# ---------------------------------------------------------------------------
# Context seed builder (Morpheus swarm shared prefix)
# ---------------------------------------------------------------------------

def build_context_seed(project_dir: Path) -> str:
    """Pre-read all Phase 1 outputs into a single markdown document.

    This document is PREPENDED to every Morpheus swarm agent's system prompt
    as a shared cacheable prefix so repeated xAI requests can reuse the same
    stable prompt prefix across swarm workers.

    Returns a markdown string (~15-50KB depending on project size).
    """
    sections: list[str] = []
    sections.append("# ═══ CONTEXT SEED — Shared Source Material ═══\n")
    sections.append(
        "**Do NOT re-read these files from disk.** They are embedded below. "
        "Use them directly from this context seed.\n"
    )

    # 1. Manifest metadata
    manifest_path = project_dir / "project_manifest.json"
    if manifest_path.exists():
        sections.append("## Project Manifest\n```json")
        sections.append(manifest_path.read_text(encoding="utf-8").strip())
        sections.append("```\n")

    # 2. Onboarding config
    config_path = project_dir / "source_files" / "onboarding_config.json"
    if config_path.exists():
        config_text = config_path.read_text(encoding="utf-8").strip()
        sections.append("## Onboarding Config\n```json")
        sections.append(config_text)
        sections.append("```\n")

        # 2a. Extract creative freedom and dialogue workflow explicitly so all agents see it
        try:
            import json as _json
            config_data = _json.loads(config_text)
            creative_freedom = config_data.get("creativeFreedom", "balanced")
            freedom = creative_freedom_contract(creative_freedom)
            sections.append("## Creative Freedom (Extracted)\n")
            sections.append(f"- **Tier**: {creative_freedom}")
            sections.append(f"- **Philosophy**: {freedom['philosophy']}")
            sections.append(
                f"- **Permission**: {config_data.get('creativeFreedomPermission', freedom['permission'])}"
            )
            sections.append(
                f"- **Failure Modes**: {config_data.get('creativeFreedomFailureModes', freedom['failure_modes'])}"
            )
            sections.append(
                f"- **Dialogue Policy**: {config_data.get('dialoguePolicy', freedom['dialogue_policy'])}"
            )
            workflow = config_data.get("dialogueWorkflow", default_dialogue_workflow())
            if isinstance(workflow, dict):
                sections.append("## Dialogue Workflow (Extracted)\n")
                sections.append(f"- **Enabled**: {workflow.get('enabled', True)}")
                sections.append(f"- **Version**: {workflow.get('version', 'unknown')}")
                agents = workflow.get("agents") or []
                for agent in agents:
                    if not isinstance(agent, dict):
                        continue
                    sections.append(
                        f"- **Agent** `{agent.get('name', 'unknown')}` on `{agent.get('runsOn', 'unknown')}`"
                    )
            sections.append("\n")
        except Exception:
            pass  # Config is already embedded as raw JSON above

    # 3. Outline skeleton
    skeleton_path = project_dir / "creative_output" / "outline_skeleton.md"
    if skeleton_path.exists():
        sections.append("## Outline Skeleton\n")
        sections.append(skeleton_path.read_text(encoding="utf-8").strip())
        sections.append("\n")

    # 4. Full creative prose
    prose_path = project_dir / "creative_output" / "creative_output.md"
    if prose_path.exists():
        sections.append("## Creative Output (Full Prose)\n")
        sections.append(prose_path.read_text(encoding="utf-8").strip())
        sections.append("\n")

    # 5. Optional: director's project brief (legacy)
    brief_path = project_dir / "logs" / "director" / "project_brief.md"
    if brief_path.exists():
        sections.append("## Director's Project Brief (Legacy)\n")
        sections.append(brief_path.read_text(encoding="utf-8").strip())
        sections.append("\n")

    sections.append("# ═══ END CONTEXT SEED ═══\n")
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Morpheus swarm validation helpers
# ---------------------------------------------------------------------------

def _validate_graph_has_entities(project_dir: Path) -> bool:
    """Check that the graph contains cast entities (Agent 1 completed)."""
    try:
        result = _stream_subprocess(
            [sys.executable, str(SKILLS_DIR / "graph_query"),
             "--type", "cast", "--stats", "--project-dir", str(project_dir)],
            cwd=project_dir, label="validate_entities")
        # graph_query --stats prints counts; check for non-zero cast
        if result.returncode != 0:
            return False
        output = result.stdout or ""
        # Look for "cast: N" or similar pattern in output
        if "cast" in output.lower() and "0 cast" not in output.lower():
            return True
        # Fallback: just check graph file has data
        graph_path = project_dir / "graph" / "narrative_graph.json"
        if graph_path.exists() and graph_path.stat().st_size > 500:
            return True
        return False
    except Exception as e:
        log_warn(f"Entity validation failed: {e}")
        return False


def _validate_graph_has_frames(project_dir: Path) -> bool:
    """Check that the graph contains frames (Agent 2 completed)."""
    try:
        result = _stream_subprocess(
            [sys.executable, str(SKILLS_DIR / "graph_query"),
             "--type", "frame", "--stats", "--project-dir", str(project_dir)],
            cwd=project_dir, label="validate_frames")
        if result.returncode != 0:
            return False
        output = result.stdout or ""
        if "frame" in output.lower() and "0 frame" not in output.lower():
            return True
        graph_path = project_dir / "graph" / "narrative_graph.json"
        if graph_path.exists() and graph_path.stat().st_size > 2000:
            return True
        return False
    except Exception as e:
        log_warn(f"Frame validation failed: {e}")
        return False


def _run_phase_2_postprocessing(project_dir: Path) -> None:
    """Assemble, materialize, and hard-gate video-direction validity."""
    log("Running deterministic prompt assembly + materialization...")

    assemble_result = _stream_subprocess(
        [sys.executable, str(SKILLS_DIR / "graph_assemble_prompts"),
         "--project-dir", str(project_dir)],
        cwd=project_dir, label="graph_assemble_prompts")
    if assemble_result.returncode != 0:
        raise RuntimeError("Phase 2 prompt assembly failed. Fix graph shot-packet data before proceeding.")

    _run_project_report(project_dir)

    dialogue_result = _stream_subprocess(
        [sys.executable, str(SKILLS_DIR / "graph_validate_dialogue"),
         "--project-dir", str(project_dir)],
        cwd=project_dir, label="graph_validate_dialogue")
    if dialogue_result.returncode != 0:
        raise RuntimeError(
            "Phase 2 dialogue validation failed. Fix dialogue recovery, frame assignment, "
            "or creative-freedom tier compliance before proceeding."
        )

    prompt_pair_result = _stream_subprocess(
        [sys.executable, str(Path(__file__).resolve().parent / "graph" / "prompt_pair_validator.py"),
         "--project-dir", str(project_dir)],
        cwd=project_dir, label="prompt_pair_validator")
    if prompt_pair_result.returncode != 0:
        raise RuntimeError(
            "Phase 2 prompt consistency validation failed. Fix contradictory subject counts, "
            "dialogue metadata, or prompt continuity before proceeding."
        )

    _reconcile_scene_cast_presence(project_dir)

    materialize_result = _stream_subprocess(
        [sys.executable, str(SKILLS_DIR / "graph_materialize"),
         "--project-dir", str(project_dir)],
        cwd=project_dir, label="graph_materialize")
    if materialize_result.returncode != 0:
        raise RuntimeError("Phase 2 materialization failed. Fix graph serialization issues before proceeding.")

    validate_result = _stream_subprocess(
        [sys.executable, str(SKILLS_DIR / "graph_validate_video_direction"),
         "--project-dir", str(project_dir), "--fix"],
        cwd=project_dir, label="graph_validate_video_direction")
    if validate_result.returncode != 0:
        raise RuntimeError(
            "Phase 2 video-direction validation failed. Fix missing composition/directing payloads "
            "or split overlong dialogue before proceeding to Phase 3."
        )

    log_ok("Deterministic post-processing complete")


def _reconcile_scene_cast_presence(project_dir: Path) -> None:
    """Fold actual visible-cast usage back into scene.cast_present after enrichment.

    Stage 1 scene cast rosters can be sparse or omit late-discovered visible participants
    (phone voices, collective beats, etc.). After prompt assembly we have the deterministic
    shot packet view of who is visibly active per frame, so use that to reconcile the scene-
    level cast roster before materialization and quality gating.
    """
    from graph.api import build_shot_packet
    from graph.store import GraphStore

    store = GraphStore(str(project_dir))
    graph = store.load()

    scene_cast_usage: dict[str, set[str]] = {
        scene_id: set(scene.cast_present or [])
        for scene_id, scene in graph.scenes.items()
    }
    for frame_id in graph.frame_order:
        frame = graph.frames.get(frame_id)
        if frame is None or frame.scene_id not in graph.scenes:
            continue
        try:
            visible_cast_ids = build_shot_packet(graph, frame_id).visible_cast_ids or []
        except Exception:
            visible_cast_ids = []
        scene_cast_usage.setdefault(frame.scene_id, set()).update(
            cast_id for cast_id in visible_cast_ids if cast_id in graph.cast
        )

    changed = False
    for scene_id, cast_ids in scene_cast_usage.items():
        scene = graph.scenes.get(scene_id)
        if scene is None:
            continue
        resolved = sorted(cast_ids)
        if resolved != list(scene.cast_present or []):
            scene.cast_present = resolved
            changed = True

    if changed:
        store.save(graph)


# ---------------------------------------------------------------------------
# Agent spawning
# ---------------------------------------------------------------------------

def run_agent(
    agent_id: str,
    prompt_file: str,
    project_dir: Path | None = None,
    model: str = DEFAULT_MODEL,
    dry_run: bool = False,
    prompt_prefix: str = "",
    context_seed: str = "",
    timeout: int | None = None,
    stream_output: bool = True,
) -> subprocess.CompletedProcess:
    """Spawn a local Grok-backed agent runner and wait for it to finish.

    Args:
        prompt_prefix: Optional text APPENDED to the system prompt (after the
                       agent's own prompt) so that the base prompt stays
                       cacheable across parallel workers.
        context_seed:  Optional text PREPENDED to the system prompt (before the
                       agent's own prompt) as a shared cacheable prefix.  All
                       swarm agents sharing the same seed benefit from API-level
                       prompt caching on the shared base prompt.
    """
    if project_dir is None:
        project_dir = PROJECT_DIR

    prompt_path = Path(prompt_file)
    if not prompt_path.exists():
        fail(f"Prompt file not found: {prompt_file}")

    system_prompt = prompt_path.read_text()

    # Expand {{include:path}} markers — reference files resolved relative to prompt dir
    system_prompt = _expand_includes(system_prompt, prompt_path.parent)

    # context_seed is PREPENDED to the system prompt. This is the shared
    # cacheable prefix for the Morpheus swarm so repeated xAI requests can
    # reuse the same stable prompt prefix.
    if context_seed:
        system_prompt = context_seed + "\n\n---\n\n" + system_prompt

    cacheable_system_prompt = system_prompt

    # prompt_prefix is APPENDED to the system prompt (not prepended).
    # This keeps the large shared base prompt as the cacheable prefix so
    # parallel workers sharing the same base prompt can reuse it efficiently.
    # The short per-worker override at the end doesn't break prefix caching and
    # retains system-prompt authority (stronger than user message overrides).
    if prompt_prefix:
        system_prompt = system_prompt + "\n\n---\n\n" + prompt_prefix

    env = _with_repo_pythonpath(
        {**os.environ, "PROJECT_DIR": str(project_dir), "SKILLS_DIR": str(SKILLS_DIR)}
    )
    env.pop("CLAUDECODE", None)

    # Build the user message (trigger prompt).
    # When a prompt_prefix override is present, direct the agent to follow it
    # rather than "all steps" (which could trigger the full CC pipeline).
    if prompt_prefix:
        trigger_msg = (
            "Execute the CRITICAL OVERRIDE at the end of your system prompt. "
            "Follow ONLY those instructions. Do not stop or wait for input."
        )
    else:
        trigger_msg = (
            "Execute your instructions now. Work autonomously through all steps "
            "in your system prompt. Do not stop or wait for input."
        )

    env["XAI_PROMPT_CACHE_KEY"] = _agent_prompt_cache_key(
        agent_id=agent_id,
        project_dir=project_dir,
        model=model,
        cacheable_system_prompt=cacheable_system_prompt,
        trigger_msg=trigger_msg,
    )

    # Write full system prompt to a temp file (Windows cmd line limit is ~32K chars)
    import tempfile as _tempfile
    prompt_tmpfile = _tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", prefix=f"prompt_{agent_id}_",
        dir=str(project_dir), delete=False, encoding="utf-8",
    )
    prompt_tmpfile.write(system_prompt)
    prompt_tmpfile.close()
    prompt_tmp_path = prompt_tmpfile.name

    cmd = [
        *AGENT_RUNNER_CMD,
        "--print",
        "-p", trigger_msg,
        "--system-prompt-file", prompt_tmp_path,
        "--dangerously-skip-permissions",
        "--output-format", "text",
        "--model", model,
        "--task-hint", agent_id,
    ]

    if dry_run:
        log(f"[DRY-RUN] Would spawn agent: {agent_id}  (model={model})", YELLOW)
        log(f"[DRY-RUN]   prompt: {prompt_file}", YELLOW)
        log(f"[DRY-RUN]   cwd:    {project_dir}", YELLOW)
        os.unlink(prompt_tmp_path)
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    effective_timeout = timeout or PHASE_TIMEOUT
    timeout_str = f"{effective_timeout}s" if effective_timeout else "unlimited"
    log(f"Spawning agent '{agent_id}' ...  (timeout={timeout_str})")
    try:
        result = _stream_subprocess(
            cmd,
            cwd=project_dir,
            env=env,
            timeout=effective_timeout,
            label=agent_id,
            echo_stdout=stream_output,
            echo_stderr=stream_output,
        )
    finally:
        # Clean up temp prompt file
        try:
            os.unlink(prompt_tmp_path)
        except OSError:
            pass
    return result


def _agent_cache_family(agent_id: str) -> str:
    lowered = (agent_id or "").lower()
    if lowered.startswith("prose_worker_scene_"):
        return "phase1-creative"
    if lowered in {"creative_coordinator", "director"}:
        return "phase1-creative"
    if lowered.startswith("frame_enricher_worker_"):
        return "phase2-frame-enricher"
    return lowered or "agent"


def _agent_prompt_cache_key(
    *,
    agent_id: str,
    project_dir: Path,
    model: str,
    cacheable_system_prompt: str,
    trigger_msg: str,
) -> str:
    return build_prompt_cache_key(
        "agent-runner",
        project_dir.resolve().name,
        _agent_cache_family(agent_id),
        model,
        cacheable_system_prompt,
        trigger_msg,
    )


def check_agent_result(agent_id: str, result: subprocess.CompletedProcess,
                       timer: Timer) -> None:
    """Print timing and warn on non-zero exit (does NOT stop the pipeline)."""
    log(f"Agent '{agent_id}' finished in {timer.elapsed_str()}  "
        f"(exit={result.returncode})")
    if result.returncode != 0:
        log_err(f"Agent '{agent_id}' failed (exit={result.returncode})")
        if result.stderr:
            print(f"\n--- STDERR ---\n{result.stderr[-4000:]}\n--- END ---\n",
                  flush=True)
        # Try to dump the last 50 lines of events.jsonl if available
        events_path = PROJECT_DIR / "logs" / agent_id / "events.jsonl"
        if events_path.exists():
            lines = events_path.read_text().splitlines()
            tail = "\n".join(lines[-50:])
            print(f"\n--- events.jsonl (last 50) ---\n{tail}\n--- END ---\n",
                  flush=True)
        log_warn(f"Agent '{agent_id}' failed — continuing pipeline (no timeout/failure halt).")


# ---------------------------------------------------------------------------
# Manifest queue flush (sync reconciler — no server dependency)
# ---------------------------------------------------------------------------

def flush_manifest_queue() -> int:
    """Process all pending manifest queue files directly into project_manifest.json.

    This is a sync, in-process version of what the server's ManifestReconciler does.
    Ensures queue files are applied to the manifest before quality gates check it.
    Returns the number of updates applied.
    """
    queue_dir = PROJECT_DIR / "dispatch" / "manifest_queue"
    dead_dir = queue_dir / "dead_letters"
    dead_dir.mkdir(parents=True, exist_ok=True)

    queue_files = sorted(queue_dir.glob("*.json"))
    if not queue_files:
        return 0

    manifest = json.loads(MANIFEST_PATH.read_text())
    total_updates = 0

    for qf in queue_files:
        try:
            text = qf.read_text()
            text = re.sub(r'^```\w*\n|\n```$', '', text.strip())
            data = json.loads(text)
        except (json.JSONDecodeError, Exception) as exc:
            log_warn(f"Bad queue file → dead_letters: {qf.name} ({exc})")
            qf.rename(dead_dir / qf.name)
            continue

        updates = data.get("updates", [])
        if not updates:
            # Not in updates format — move to dead letters
            qf.rename(dead_dir / qf.name)
            continue

        for update in updates:
            target = update.get("target")
            set_dict = update.get("set", {})

            if target == "frame":
                _merge_by_key(manifest, "frames", "frameId", update.get("frameId"), set_dict)
            elif target == "cast":
                _merge_by_key(manifest, "cast", "castId", update.get("castId"), set_dict)
            elif target == "location":
                _merge_by_key(manifest, "locations", "locationId", update.get("locationId"), set_dict)
            elif target == "prop":
                _merge_by_key(manifest, "props", "propId", update.get("propId"), set_dict)
            elif target == "project":
                manifest.update(set_dict)
            total_updates += 1

        qf.unlink(missing_ok=True)

    if total_updates > 0:
        manifest["version"] = manifest.get("version", 0) + 1
        tmp_path = MANIFEST_PATH.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(manifest, indent=2))
        os.replace(tmp_path, MANIFEST_PATH)
        log_ok(f"Flushed manifest queue: {total_updates} updates from {len(queue_files)} files")

    return total_updates


def _merge_by_key(manifest: dict, collection: str, key_field: str,
                  key_value: str, set_dict: dict) -> None:
    """Merge an update into a manifest collection by key."""
    items = manifest.setdefault(collection, [])
    for item in items:
        if item.get(key_field) == key_value:
            item.update(set_dict)
            return
    new_item = {key_field: key_value}
    new_item.update(set_dict)
    items.append(new_item)


# ---------------------------------------------------------------------------
# Verification helpers
# ---------------------------------------------------------------------------

def verify_files(phase: str, paths: list[Path]) -> None:
    """Check that all expected output files exist; warn if any are missing."""
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        log_warn(f"Phase {phase} verification — missing files (continuing):")
        for m in missing:
            print(f"  {m}", flush=True)
    else:
        log_ok(f"Phase {phase} verification passed ({len(paths)} file(s) confirmed)")


MAX_QUALITY_RETRIES = 1  # Re-run a phase at most once if quality gate fails


def _read_onboarding_config(base: Path) -> dict:
    """Best-effort read of the Phase 0 onboarding contract."""
    config_path = base / "source_files" / "onboarding_config.json"
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _frame_needs_frame_enrichment(frame) -> bool:
    """Return True when a frame is missing core frame-enricher-authored fields."""
    if not getattr(frame, "action_summary", ""):
        return True
    if not getattr(frame, "video_optimized_prompt_block", ""):
        return True
    if not getattr(frame, "visual_flow_element", None):
        return True

    composition = getattr(frame, "composition", None)
    if composition is None or not composition.shot or not composition.angle or not composition.movement:
        return True

    directing = getattr(frame, "directing", None)
    if directing is None or not directing.dramatic_purpose or not directing.beat_turn or not directing.pov_owner:
        return True

    environment = getattr(frame, "environment", None)
    lighting = getattr(environment, "lighting", None) if environment else None
    if lighting is None or lighting.direction is None or lighting.quality is None:
        return True

    return False


def _pending_frame_enricher_inputs(graph, inputs: list[dict]) -> list[dict]:
    """Filter frame-enricher inputs down to frames that still need enrichment."""
    pending: list[dict] = []
    for input_dict in inputs:
        frame_id = str(input_dict.get("frame_id", "")).strip()
        frame = graph.frames.get(frame_id)
        if frame is None:
            continue
        if _frame_needs_frame_enrichment(frame):
            pending.append(input_dict)
    return pending


def _compact_identifier_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _posture_from_pose_name(pose_name: str):
    if not pose_name:
        return None
    prefix = pose_name.split("_", 1)[0].lower()
    mapping = {
        "standing": "standing",
        "sitting": "sitting",
        "crouching": "crouching",
        "kneeling": "kneeling",
        "lying": "lying",
        "walking": "walking",
        "running": "running",
        "leaning": "leaning",
        "hunched": "hunched",
    }
    return mapping.get(prefix)


def _rehydrate_phase_2_from_manifest(project_dir: Path, graph) -> int:
    """Backfill parser-only graphs from the last successful manifest/materialization pass."""
    manifest_path = project_dir / "project_manifest.json"
    if not manifest_path.exists():
        return 0

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return 0

    manifest_frames = {
        str(frame.get("frameId", "")).strip(): frame
        for frame in manifest.get("frames", [])
        if str(frame.get("frameId", "")).strip()
    }
    if not manifest_frames:
        return 0

    from graph.api import get_frame_cast_state_models
    from graph.schema import CastFrameState, CastFrameRole, CinematicTag, EmotionalArc, LightingDirection, LightingQuality, Posture

    restored = 0

    for frame_id, frame in graph.frames.items():
        manifest_frame = manifest_frames.get(frame_id)
        if not manifest_frame:
            continue

        restored_frame = False

        action_summary = manifest_frame.get("actionSummary")
        if action_summary:
            frame.action_summary = action_summary
            restored_frame = True

        prompt_block = manifest_frame.get("videoOptimizedPromptBlock")
        if prompt_block:
            frame.video_optimized_prompt_block = prompt_block
            restored_frame = True

        emotional_arc = manifest_frame.get("emotionalArc")
        if emotional_arc:
            try:
                frame.emotional_arc = EmotionalArc(emotional_arc)
            except ValueError:
                frame.emotional_arc = emotional_arc
            restored_frame = True

        visual_flow_element = manifest_frame.get("visualFlowElement")
        if visual_flow_element:
            frame.visual_flow_element = visual_flow_element
            restored_frame = True

        cinematic_tag = manifest_frame.get("cinematicTag")
        if cinematic_tag:
            if isinstance(cinematic_tag, dict):
                tag = str(cinematic_tag.get("tag") or "").strip()
                modifier = str(cinematic_tag.get("modifier") or "").strip()
                full_tag = str(cinematic_tag.get("full_tag") or cinematic_tag.get("fullTag") or "").strip()
                if not full_tag:
                    full_tag = " ".join(part for part in (tag, modifier) if part).strip()
                family = str(cinematic_tag.get("family") or "").strip()
                if not family and tag:
                    family = tag.split(".", 1)[0][:1]
                frame.cinematic_tag = CinematicTag(
                    tag=tag,
                    modifier=modifier,
                    full_tag=full_tag or tag,
                    definition=str(cinematic_tag.get("definition") or "").strip(),
                    family=family,
                    editorial_function=str(cinematic_tag.get("editorial_function") or cinematic_tag.get("editorialFunction") or "").strip(),
                    ai_prompt_language=str(cinematic_tag.get("ai_prompt_language") or cinematic_tag.get("aiPromptLanguage") or "").strip(),
                    lens_guidance=str(cinematic_tag.get("lens_guidance") or cinematic_tag.get("lensGuidance") or "").strip(),
                    dof_guidance=str(cinematic_tag.get("dof_guidance") or cinematic_tag.get("dofGuidance") or "").strip(),
                )
            else:
                tag = str(cinematic_tag).strip()
                frame.cinematic_tag = CinematicTag(
                    tag=tag,
                    full_tag=tag,
                    family=tag.split(".", 1)[0][:1],
                )
            restored_frame = True

        composition = manifest_frame.get("composition") or {}
        for field in ("shot", "angle", "placement", "grouping", "blocking", "movement", "focus", "transition", "rule"):
            value = composition.get(field)
            if value:
                setattr(frame.composition, field, value)
                restored_frame = True

        background = manifest_frame.get("background") or {}
        for field in ("visible_description", "camera_facing", "background_action", "background_sound", "background_music"):
            value = background.get(field)
            if value:
                setattr(frame.background, field, value)
                restored_frame = True
        depth_layers = background.get("depth_layers")
        if isinstance(depth_layers, list) and depth_layers:
            frame.background.depth_layers = depth_layers
            restored_frame = True

        directing = manifest_frame.get("directing") or {}
        for field in (
            "dramatic_purpose", "beat_turn", "pov_owner", "viewer_knowledge_delta",
            "power_dynamic", "tension_source", "camera_motivation",
            "movement_motivation", "movement_path", "reaction_target", "background_life",
        ):
            value = directing.get(field)
            if value:
                setattr(frame.directing, field, value)
                restored_frame = True

        # Manifest does not currently persist environment. Restore minimal lighting so
        # parser-only reruns can safely skip fresh frame-enricher calls when prompt artifacts exist.
        if restored_frame:
            if frame.environment.lighting.direction is None:
                frame.environment.lighting.direction = LightingDirection.AMBIENT
            if frame.environment.lighting.quality is None:
                frame.environment.lighting.quality = LightingQuality.SOFT

        visible_cast_ids = list(manifest_frame.get("castIds") or [])
        frame_states = {
            state.cast_id: state
            for state in get_frame_cast_state_models(graph, frame_id)
        }
        visible_state_ids = [
            state.cast_id
            for state in frame_states.values()
            if getattr(getattr(state, "frame_role", None), "value", getattr(state, "frame_role", None)) != "referenced"
        ]

        snapshot = manifest_frame.get("castBibleSnapshot") or {}
        for character in snapshot.get("characters") or []:
            raw_cast_id = str(character.get("character_id", "")).strip()
            if not raw_cast_id:
                continue

            resolved_cast_id = raw_cast_id
            if resolved_cast_id not in frame_states:
                target_token = _compact_identifier_token(resolved_cast_id.removeprefix("cast_"))
                for candidate in frame_states:
                    if _compact_identifier_token(candidate.removeprefix("cast_")) == target_token:
                        resolved_cast_id = candidate
                        break
                else:
                    if len(visible_state_ids) == 1:
                        resolved_cast_id = visible_state_ids[0]

            state = frame_states.get(resolved_cast_id)
            if state is None:
                default_role = CastFrameRole.SUBJECT if len(visible_cast_ids) <= 1 else CastFrameRole.BACKGROUND
                if resolved_cast_id not in graph.cast:
                    continue
                state = CastFrameState(
                    cast_id=resolved_cast_id,
                    frame_id=frame_id,
                    frame_role=default_role,
                )
                graph.cast_frame_states[f"{resolved_cast_id}@{frame_id}"] = state
                frame_states[resolved_cast_id] = state

            if getattr(getattr(state, "frame_role", None), "value", getattr(state, "frame_role", None)) == "referenced":
                continue

            if visible_cast_ids:
                state.frame_role = (
                    CastFrameRole.SUBJECT
                    if len(visible_cast_ids) == 1 and resolved_cast_id in visible_cast_ids
                    else CastFrameRole.BACKGROUND
                )

            pose = (character.get("pose") or {})
            pose_name = str(pose.get("pose", "")).strip()
            posture_name = _posture_from_pose_name(pose_name)
            if posture_name:
                try:
                    state.posture = Posture(posture_name)
                except ValueError:
                    pass

            modifiers = pose.get("modifiers") or []
            for modifier in modifiers:
                if ":" not in modifier:
                    continue
                key, value = modifier.split(":", 1)
                key = key.strip()
                value = value.strip()
                if not value:
                    continue
                if key == "action":
                    state.action = value
                elif key == "screen_position":
                    state.screen_position = value
                elif key == "facing_direction":
                    state.facing_direction = value
                elif key == "looking_at":
                    state.looking_at = value
                elif key == "emotion":
                    state.emotion = value
                elif key == "state_tag":
                    state.active_state_tag = value
                elif key == "clothing_state":
                    state.clothing_state = value
                elif key == "eye_direction":
                    state.eye_direction = value

            restored_frame = True

        if restored_frame:
            restored += 1

    return restored


def _parse_frame_enricher_worker_output(raw_text: str, frame_id: str) -> dict:
    """Parse a CLI frame-enricher worker response into the enrichment contract."""
    text = (raw_text or "").strip()
    if not text:
        return {"frame_id": frame_id, "error": "empty_output"}

    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(
            line for line in lines
            if not line.strip().startswith("```")
        ).strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        return {"frame_id": frame_id, "error": f"json_parse_error: {exc}"}

    if not isinstance(result, dict):
        return {"frame_id": frame_id, "error": "non_object_json"}
    result["frame_id"] = frame_id
    return result


def _run_frame_enricher_cli_worker(input_dict: dict, *, dry_run: bool = False) -> dict:
    """Run one frame enrichment worker through the shared local agent runner."""
    from graph.frame_enricher import FRAME_ENRICHER_MODEL, FRAME_ENRICHER_SYSTEM_PROMPT

    frame_id = str(input_dict.get("frame_id", "unknown"))
    worker_id = f"frame_enricher_worker_{frame_id}"
    worker_timer = Timer()
    live_log(f"  [FrameEnricher] starting {frame_id}")

    prompt_file = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".md",
        prefix=f"{worker_id}_",
        dir=str(PROJECT_DIR),
        delete=False,
        encoding="utf-8",
    )
    try:
        prompt_file.write(FRAME_ENRICHER_SYSTEM_PROMPT)
        prompt_file.close()

        prompt_prefix = (
            "Process ONLY the frame payload below. Return ONLY valid JSON matching "
            "the required enrichment schema. No markdown, no explanation.\n\n"
            "FRAME INPUT:\n```json\n"
            + json.dumps(input_dict, indent=2, ensure_ascii=False)
            + "\n```"
        )
        result = run_agent(
            worker_id,
            prompt_file.name,
            project_dir=PROJECT_DIR,
            model=FRAME_ENRICHER_MODEL,
            dry_run=dry_run,
            prompt_prefix=prompt_prefix,
            timeout=None,
            stream_output=False,
        )
        check_agent_result(worker_id, result, worker_timer)
        if result.returncode != 0:
            return {"frame_id": frame_id, "error": f"agent_exit_{result.returncode}"}
        return _parse_frame_enricher_worker_output(result.stdout or "", frame_id)
    finally:
        try:
            os.unlink(prompt_file.name)
        except OSError:
            pass


def _run_frame_enricher_cli_batch(inputs: list[dict], *, dry_run: bool = False, max_concurrent: int = 20) -> list[dict]:
    """Run frame-enricher workers through the local agent runner, preserving input order."""
    if not inputs:
        return []

    results: list[dict | None] = [None] * len(inputs)
    started_at = time.time()
    success_count = 0
    failure_count = 0
    total = len(inputs)
    with ThreadPoolExecutor(max_workers=min(max_concurrent, len(inputs))) as executor:
        futures = {
            executor.submit(_run_frame_enricher_cli_worker, input_dict, dry_run=dry_run): idx
            for idx, input_dict in enumerate(inputs)
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                results[idx] = future.result()
            except Exception as exc:
                frame_id = str(inputs[idx].get("frame_id", "unknown"))
                results[idx] = {"frame_id": frame_id, "error": f"worker_exception: {exc}"}
            result = results[idx] or {}
            frame_id = str(result.get("frame_id", inputs[idx].get("frame_id", "unknown")))
            if "error" in result:
                failure_count += 1
                status = f"failed ({result['error']})"
                color = YELLOW
            else:
                success_count += 1
                status = "complete"
                color = CYAN
            completed = success_count + failure_count
            eta_suffix = _progress_eta_suffix(
                started_at=started_at,
                completed=completed,
                total=total,
            )
            live_log(
                f"  [FrameEnricher {completed}/{total}] {frame_id} {status} "
                f"({success_count} ok, {failure_count} failed{eta_suffix})",
                color=color,
            )

    return [result if result is not None else {"frame_id": str(inputs[idx].get("frame_id", "unknown")), "error": "missing_result"} for idx, result in enumerate(results)]

def _project_output_size(base: Path, manifest: dict | None = None) -> str:
    """Resolve the project's declared output size from onboarding/manifest."""
    config = _read_onboarding_config(base)
    frame_budget = (
        config.get("frameBudget")
        or config.get("frame_budget")
        or (manifest or {}).get("frameBudget")
        or (manifest or {}).get("frame_budget")
    )
    if frame_budget not in (None, "", []):
        return derive_output_size_from_frame_budget(frame_budget)
    raw = (
        config.get("outputSize")
        or config.get("output_size")
        or (manifest or {}).get("outputSize")
        or (manifest or {}).get("output_size")
        or "short"
    )
    return str(raw).strip().lower()


def _project_frame_budget(base: Path, manifest: dict | None = None) -> int | None:
    config = _read_onboarding_config(base)
    raw = (
        config.get("frameBudget")
        or config.get("frame_budget")
        or (manifest or {}).get("frameBudget")
        or (manifest or {}).get("frame_budget")
    )
    if raw in (None, "", []):
        return None
    try:
        return normalize_frame_budget(raw)
    except ValueError:
        return None


def _count_protagonists(manifest: dict, base: Path) -> int:
    """Count protagonist declarations from manifest first, then cast profiles."""
    cast_entries = manifest.get("cast") or []
    manifest_count = sum(
        1
        for entry in cast_entries
        if isinstance(entry, dict) and str(entry.get("role", "")).strip().lower() == "protagonist"
    )
    if manifest_count:
        return manifest_count

    cast_dir = base / "cast"
    protagonist_count = 0
    for cast_file in cast_dir.glob("*.json"):
        try:
            cast_data = json.loads(cast_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if str(cast_data.get("role", "")).strip().lower() == "protagonist":
            protagonist_count += 1
    return protagonist_count


def _resolve_regen_image_size(prompt_data: dict, *, prompt_file: Path) -> str:
    """Resolve the authored image size, preferring the current schema key."""
    size = str(prompt_data.get("size", "")).strip()
    legacy_size = str(prompt_data.get("image_size", "")).strip()

    if size and legacy_size and size != legacy_size:
        raise ValueError(
            f"{prompt_file.name} defines conflicting size keys: size={size!r}, image_size={legacy_size!r}"
        )
    if size:
        return size
    if legacy_size:
        log_warn(f"  {prompt_file.name}: using legacy prompt key 'image_size' ({legacy_size})")
        return legacy_size
    raise ValueError(f"{prompt_file.name} is missing both 'size' and legacy 'image_size'")


def _refine_status_kind(refined_by: str) -> str:
    """Normalize refiner status strings to success/skipped/failed buckets."""
    value = (refined_by or "").strip().lower()
    if value == "grok-vision":
        return "refined"
    if value.startswith("skipped:"):
        return "skipped"
    if value.startswith("failed:"):
        return "failed"
    return "unknown"


_CAST_LOCATION_SUFFIXES = {
    "arizona", "california", "topanga", "sedona", "canyon", "ranch", "room", "hall",
}


def _edit_distance_le_one(a: str, b: str) -> bool:
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = j = edits = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if len(a) == len(b):
            i += 1
            j += 1
        else:
            j += 1
    if i < len(a) or j < len(b):
        edits += 1
    return edits <= 1


def _suspicious_cast_name_reasons(name: str) -> list[str]:
    lowered = (name or "").strip().lower()
    if not lowered:
        return ["empty"]
    reasons: list[str] = []
    tokens = [token for token in re.split(r"\s+", lowered) if token]
    if any(char.isdigit() for char in lowered):
        reasons.append("contains digits")
    if len(tokens) >= 2 and tokens[-1] in _CAST_LOCATION_SUFFIXES:
        reasons.append("ends with location-like token")
    return reasons


def quality_gate_phase_1(base: Path) -> list[str]:
    """Validate Phase 1 outputs. Returns list of failure reasons (empty = pass)."""
    issues = []
    output_size = _project_output_size(base)
    min_scene_drafts = 1 if output_size == "short" else 2
    if min_scene_drafts != 2:
        log(f"Phase 1 gate adjusted for output size {output_size}: min_scene_drafts={min_scene_drafts}")

    co = base / "creative_output" / "creative_output.md"
    if not co.exists():
        issues.append("creative_output.md missing")
    elif co.stat().st_size < 5000:
        issues.append(f"creative_output.md too small ({co.stat().st_size} bytes) — likely incomplete")

    skeleton = base / "creative_output" / "outline_skeleton.md"
    if not skeleton.exists():
        issues.append("outline_skeleton.md missing")

    # Check scene drafts exist (written to creative_output/scenes/)
    scenes_dir = base / "creative_output" / "scenes"
    if scenes_dir.exists():
        drafts = list(scenes_dir.glob("*.md"))
        if len(drafts) < min_scene_drafts:
            issues.append(
                f"Only {len(drafts)} scene draft(s) in creative_output/scenes/ — "
                f"expected at least {min_scene_drafts} for output size {output_size}"
            )
    else:
        issues.append("creative_output/scenes/ directory missing — no scene drafts found")
    return issues


def quality_gate_phase_2(base: Path) -> list[str]:
    """Validate Phase 2 outputs. Returns list of failure reasons."""
    issues = []

    # Manifest frame count
    manifest = json.loads((base / "project_manifest.json").read_text())
    output_size = _project_output_size(base, manifest)
    frames = manifest.get("frames", [])
    manifest_frames_by_id = {
        frame.get("frameId"): frame for frame in frames if frame.get("frameId")
    }
    if len(frames) < 10:
        issues.append(f"Only {len(frames)} frames in manifest — expected 10+ for this story size")

    # Check frame entity tags
    missing_tags = 0
    for f in frames:
        if not f.get("castIds") and not f.get("locationId"):
            missing_tags += 1
    if missing_tags > 0 and missing_tags > len(frames) * 0.2:
        issues.append(f"{missing_tags}/{len(frames)} frames missing castIds/locationId tags")

    # Dialogue
    dialogue_path = base / "dialogue.json"
    if not dialogue_path.exists():
        issues.append("dialogue.json missing")
    else:
        try:
            dlg = json.loads(dialogue_path.read_text())
            lines = dlg if isinstance(dlg, list) else dlg.get("lines", dlg.get("dialogue", []))
            if len(lines) < 3:
                issues.append(f"Only {len(lines)} dialogue line(s) — expected more")
        except (json.JSONDecodeError, Exception) as e:
            issues.append(f"dialogue.json parse error: {e}")

    # Cast/location/prop profiles
    cast_jsons = list((base / "cast").glob("*.json"))
    protagonist_count = _count_protagonists(manifest, base)
    min_cast_profiles = 1 if output_size == "short" and protagonist_count == 1 else 2
    if min_cast_profiles != 2:
        log(
            "Phase 2 gate adjusted for short single-protagonist project: "
            f"min_cast_profiles={min_cast_profiles}"
        )
    if len(cast_jsons) < min_cast_profiles:
        issues.append(
            f"Only {len(cast_jsons)} cast profile(s) — expected at least {min_cast_profiles} "
            f"for output size {output_size}"
        )

    loc_jsons = list((base / "locations").glob("*.json"))
    if len(loc_jsons) < 1:
        issues.append(f"No location profiles found")

    # Graph-level cast integrity
    graph_path = base / "graph" / "narrative_graph.json"
    if not graph_path.exists():
        issues.append("graph/narrative_graph.json missing")
        return issues

    try:
        from graph.store import GraphStore
        from graph.api import build_shot_packet, get_frame_cast_state_models

        def _dialogue_requires_visible_primary_speaker(frame, dialogue_node, visible_cast_ids) -> bool:
            cast_id = (getattr(dialogue_node, "cast_id", "") or "").lower()
            speaker = (getattr(dialogue_node, "speaker", "") or "").lower()
            source_text = (getattr(frame, "source_text", "") or "").lower()
            if any(
                token in f"{cast_id} {speaker} {source_text}"
                for token in ("voice over", "voiceover", "voice_over", "on phone", "phone")
            ):
                return False

            first_line = source_text.splitlines()[0].strip() if source_text else ""
            if "(" in first_line and ")" in first_line and visible_cast_ids:
                alias = first_line.split("(", 1)[1].split(")", 1)[0].strip().lower()
                for visible_cast_id in visible_cast_ids:
                    cast_node = graph.cast.get(visible_cast_id)
                    if cast_node is None:
                        continue
                    for raw in (
                        getattr(cast_node, "display_name", None),
                        getattr(cast_node, "name", None),
                        getattr(cast_node, "source_name", None),
                    ):
                        normalized = re.sub(r"\s+", " ", (raw or "").strip()).lower()
                        if normalized and alias == normalized:
                            return False
            return True

        store = GraphStore(str(base))
        graph = store.load()

        scene_cast_conflicts: list[str] = []
        manifest_cast_conflicts: list[str] = []
        missing_manifest_frames: list[str] = []
        dialogue_presence_conflicts: list[str] = []

        for frame_id in graph.frame_order:
            frame = graph.frames.get(frame_id)
            if not frame:
                continue

            raw_visible_cast_ids = sorted({
                cs.cast_id
                for cs in get_frame_cast_state_models(graph, frame_id)
                if getattr(getattr(cs, "frame_role", None), "value", getattr(cs, "frame_role", None)) != "referenced"
            })
            try:
                visible_cast_ids = sorted(set(build_shot_packet(graph, frame_id).visible_cast_ids or raw_visible_cast_ids))
            except Exception:
                visible_cast_ids = raw_visible_cast_ids

            scene = graph.scenes.get(frame.scene_id)
            scene_cast_ids = set(getattr(scene, "cast_present", []) or [])
            extra_cast = [cast_id for cast_id in visible_cast_ids if cast_id not in scene_cast_ids]
            if extra_cast:
                scene_cast_conflicts.append(
                    f"{frame_id} ({frame.scene_id}) has visible cast not in scene.cast_present: {', '.join(extra_cast)}"
                )

            manifest_frame = manifest_frames_by_id.get(frame_id)
            if not manifest_frame:
                missing_manifest_frames.append(frame_id)
            else:
                manifest_cast_ids = sorted(manifest_frame.get("castIds") or [])
                if manifest_cast_ids != visible_cast_ids:
                    manifest_cast_conflicts.append(
                        f"{frame_id}: manifest.castIds={manifest_cast_ids} graph.visibleCast={visible_cast_ids}"
                    )

            if frame.dialogue_ids:
                for dialogue_id in frame.dialogue_ids:
                    dialogue_node = graph.dialogue.get(dialogue_id)
                    if not dialogue_node:
                        continue
                    if (
                        dialogue_node.primary_visual_frame == frame_id
                        and _dialogue_requires_visible_primary_speaker(frame, dialogue_node, visible_cast_ids)
                        and dialogue_node.cast_id not in visible_cast_ids
                    ):
                        dialogue_presence_conflicts.append(
                            f"{frame_id}: primary dialogue speaker {dialogue_node.cast_id} missing from visible cast"
                        )

        if missing_manifest_frames:
            issues.append(
                f"{len(missing_manifest_frames)} graph frames missing from manifest.frames[] "
                f"(sample: {', '.join(missing_manifest_frames[:5])})"
            )
        if scene_cast_conflicts:
            issues.append(
                f"{len(scene_cast_conflicts)} frame(s) have visible cast outside scene.cast_present "
                f"(sample: {'; '.join(scene_cast_conflicts[:3])})"
            )
        if manifest_cast_conflicts:
            issues.append(
                f"{len(manifest_cast_conflicts)} frame(s) have manifest castIds that do not match graph visible cast "
                f"(sample: {'; '.join(manifest_cast_conflicts[:3])})"
            )
        if dialogue_presence_conflicts:
            issues.append(
                f"{len(dialogue_presence_conflicts)} primary dialogue frame(s) omit the speaker from visible cast "
                f"(sample: {'; '.join(dialogue_presence_conflicts[:3])})"
            )

        total_graph_frames = max(len(graph.frame_order), 1)
        dialogue_frames = sum(
            1
            for frame_id in graph.frame_order
            if (
                (frame := graph.frames.get(frame_id)) is not None
                and (frame.is_dialogue or bool(frame.dialogue_ids))
            )
        )
        dialogue_ratio = dialogue_frames / total_graph_frames
        if total_graph_frames >= 2 and dialogue_ratio < 0.45:
            issues.append(
                f"Dialogue density too low: {dialogue_frames}/{total_graph_frames} frame(s) "
                f"({dialogue_ratio:.0%}) carry dialogue or dialogue reaction coverage — target is at least 45%"
            )

        suspicious_cast_names: list[str] = []
        seen_cast_tokens: dict[str, str] = {}
        near_duplicate_cast_names: list[str] = []
        for cast_id, cast_node in sorted(graph.cast.items()):
            display_name = getattr(cast_node, "display_name", None) or cast_node.name
            reasons = _suspicious_cast_name_reasons(display_name)
            if reasons:
                suspicious_cast_names.append(f"{cast_id} ({display_name}): {', '.join(reasons)}")
            compact = re.sub(r"[^a-z0-9]+", "", display_name.lower())
            if not compact:
                continue
            for seen_compact, seen_cast_id in seen_cast_tokens.items():
                if compact == seen_compact or _edit_distance_le_one(compact, seen_compact):
                    near_duplicate_cast_names.append(
                        f"{seen_cast_id} and {cast_id} have near-duplicate display names"
                    )
                    break
            seen_cast_tokens.setdefault(compact, cast_id)

        if suspicious_cast_names:
            issues.append(
                f"{len(suspicious_cast_names)} cast name(s) look unnormalized "
                f"(sample: {'; '.join(suspicious_cast_names[:3])})"
            )
        if near_duplicate_cast_names:
            issues.append(
                f"{len(near_duplicate_cast_names)} near-duplicate cast name pair(s) detected "
                f"(sample: {'; '.join(near_duplicate_cast_names[:3])})"
            )

    except Exception as e:
        issues.append(f"graph cast integrity check failed: {e}")

    return issues


def quality_gate_phase_3(base: Path) -> list[str]:
    """Validate Phase 3 outputs (reference and environment images).

    This is the authoritative validation logic. See agent_prompts/archived_intents/ for historical design references.
    """
    issues = []

    manifest = json.loads((base / "project_manifest.json").read_text())
    cast = manifest.get("cast", [])

    # Cast composites
    composites = list((base / "cast" / "composites").glob("*.png"))
    expected_composites = sum(1 for c in cast if c.get("role") != "voice_only")
    if len(composites) < expected_composites:
        issues.append(f"{len(composites)} composites but {expected_composites} non-voice cast members")

    # Check for tiny/corrupt images
    for img in composites:
        if img.stat().st_size < 10240:  # < 10KB is suspicious
            issues.append(f"Composite {img.name} is only {img.stat().st_size} bytes — may be corrupt")

    # Location images
    loc_imgs = list((base / "locations" / "primary").glob("*.png"))
    if len(loc_imgs) < 1:
        issues.append("No location images generated")

    return issues


def quality_gate_phase_4(base: Path) -> list[str]:
    """Validate Phase 4 outputs (composed frames). No TTS checks.

    This is the authoritative validation logic. See agent_prompts/archived_intents/ for historical design references.
    """
    issues = []

    manifest = json.loads((base / "project_manifest.json").read_text())
    frames = manifest.get("frames", [])

    # Composed frames
    composed_dir = base / "frames" / "composed"
    composed = list(composed_dir.glob("*_gen.png")) if composed_dir.exists() else []
    if len(composed) < len(frames) * 0.8:  # Allow 20% failure
        issues.append(f"Only {len(composed)}/{len(frames)} frames composed")

    # Check for tiny composed frames
    for img in composed:
        if img.stat().st_size < 10240:
            issues.append(f"Composed frame {img.name} is only {img.stat().st_size} bytes — may be corrupt")

    try:
        from graph.prompt_pair_validator import (
            IssueSeverity,
            PromptPairCategory,
            validate_all_prompt_pairs,
        )
        from graph.store import GraphStore

        store = GraphStore(str(base))
        graph = store.load()
        image_prompts: dict[str, dict] = {}
        video_prompts: dict[str, dict] = {}
        for frame_id in graph.frame_order:
            image_path = base / "frames" / "prompts" / f"{frame_id}_image.json"
            video_path = base / "video" / "prompts" / f"{frame_id}_video.json"
            if image_path.exists():
                image_prompts[frame_id] = json.loads(image_path.read_text())
            if video_path.exists():
                video_prompts[frame_id] = json.loads(video_path.read_text())

        prompt_issues = [
            issue
            for issue in validate_all_prompt_pairs(graph, image_prompts, video_prompts)
            if issue.severity == IssueSeverity.ERROR
            and issue.category == PromptPairCategory.SUBJECT_COUNT_CONSISTENCY
        ]
        if prompt_issues:
            issues.append(
                f"{len(prompt_issues)} prompt subject-count contradiction(s) remain "
                f"(sample: {'; '.join(issue.description for issue in prompt_issues[:3])})"
            )
    except Exception as e:
        issues.append(f"phase 4 prompt consistency check failed: {e}")

    return issues


def quality_gate_phase_5(base: Path) -> list[str]:
    """Validate Phase 5 outputs (video clips).

    This is the authoritative validation logic. See agent_prompts/archived_intents/ for historical design references.
    """
    issues = []

    manifest = json.loads((base / "project_manifest.json").read_text())
    frames = manifest.get("frames", [])

    clips_dir = base / "video" / "clips"
    clips = list(clips_dir.glob("*.mp4")) if clips_dir.exists() else []
    prompt_dir = base / "video" / "prompts"

    if len(clips) < len(frames) * 0.8:
        issues.append(f"Only {len(clips)}/{len(frames)} video clips generated")

    missing_prompts = []
    missing_clips = []
    for frame in frames:
        frame_id = frame.get("frameId")
        if not frame_id:
            continue
        if not (prompt_dir / f"{frame_id}_video.json").exists():
            missing_prompts.append(frame_id)
        if not (clips_dir / f"{frame_id}.mp4").exists():
            missing_clips.append(frame_id)

    if missing_prompts:
        issues.append(
            f"{len(missing_prompts)} frame(s) missing video prompt JSON "
            f"(sample: {', '.join(missing_prompts[:5])})"
        )
    if missing_clips:
        issues.append(
            f"{len(missing_clips)} frame(s) missing expected clip output "
            f"(sample: {', '.join(missing_clips[:5])})"
        )

    for clip in clips:
        if clip.stat().st_size < 10240:
            issues.append(f"Clip {clip.name} is only {clip.stat().st_size} bytes — may be corrupt")

    return issues


QUALITY_GATES = {
    1: quality_gate_phase_1,
    2: quality_gate_phase_2,
    3: quality_gate_phase_3,
    4: quality_gate_phase_4,
    5: quality_gate_phase_5,
}


def run_quality_gate(phase_num: int, base: Path) -> bool:
    """Run quality gate for a phase. Returns True if passed, False if failed."""
    gate_fn = QUALITY_GATES.get(phase_num)
    if not gate_fn:
        return True

    # Flush pending manifest queue updates before checking
    flush_manifest_queue()

    log(f"Running quality gate for Phase {phase_num}...")
    issues = gate_fn(base)

    if not issues:
        log_ok(f"Quality gate Phase {phase_num}: PASSED")
        return True

    log_err(f"Quality gate Phase {phase_num}: {len(issues)} issue(s) found:")
    for issue in issues:
        print(f"  - {issue}", flush=True)

    # Save gate report
    gate_report = {
        "phase": phase_num,
        "passed": False,
        "issues": issues,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    gate_path = PIPELINE_LOGS_DIR / f"phase_{phase_num}_quality_gate.json"
    gate_path.write_text(json.dumps(gate_report, indent=2))

    return False


def list_dir_files(directory: Path, indent: str = "  ") -> int:
    """Print files in directory tree, return count."""
    count = 0
    if not directory.exists():
        return 0
    for p in sorted(directory.rglob("*")):
        if p.is_file():
            size = p.stat().st_size
            size_str = f"{size:,}B" if size < 1024 else f"{size // 1024:,}KB"
            print(f"{indent}{p.relative_to(directory)}  ({size_str})", flush=True)
            count += 1
    return count


def detect_resume_phase() -> int:
    """Read the manifest and return the first phase that isn't complete (0-6).
    If all phases are complete, returns 7 (nothing to do)."""
    manifest = read_manifest()
    phases = manifest.get("phases", {})
    for i in range(7):
        phase_data = phases.get(f"phase_{i}", {})
        if phase_data.get("status") != "complete":
            return i
        reusable, issues = _phase_reuse_status(i, PROJECT_DIR)
        if not reusable:
            log_warn(
                f"Resume will rerun phase {i} — existing artifacts are incomplete: "
                + "; ".join(issues[:3])
            )
            return i
    return 7  # all done


def verify_prerequisites(target_phase: int) -> None:
    """When running a single phase via --phase N, verify phases 0..N-1 are complete."""
    if target_phase == 0:
        return  # No prerequisites for phase 0

    manifest = read_manifest()
    phases = manifest.get("phases", {})

    for i in range(target_phase):
        phase_key = f"phase_{i}"
        phase_data = phases.get(phase_key, {})
        status = phase_data.get("status", "not_found")
        if status != "complete":
            fail(f"Prerequisite not met: phase_{i} status is '{status}' "
                 f"(expected 'complete'). Run earlier phases first or use "
                 f"full pipeline mode.")
        reusable, issues = _phase_reuse_status(i, PROJECT_DIR)
        if not reusable:
            fail(
                f"Prerequisite artifacts for phase_{i} are incomplete: "
                + "; ".join(issues[:5])
                + ". Rerun that phase or use --resume."
            )

    log_ok(f"All prerequisites for phase {target_phase} verified (phases 0-{target_phase - 1} complete)")


def _dedupe_issues(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        normalized = str(item).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _phase_1_reuse_issues(project_dir: Path) -> list[str]:
    state = _scan_phase_1_state(project_dir)
    issues: list[str] = []
    if not state["skeleton_exists"]:
        issues.append("outline_skeleton.md missing")
    if not state["expected_scene_numbers"]:
        issues.append("no scene numbers detected from outline_skeleton.md")
    if state["missing_scene_numbers"]:
        issues.append(
            "missing scene drafts: "
            + ", ".join(f"{scene_num:02d}" for scene_num in state["missing_scene_numbers"][:8])
        )
    if not state["creative_output_exists"]:
        issues.append("creative_output.md missing")
    if state["creative_output_stale"]:
        issues.append("creative_output.md is stale relative to scene drafts")
    issues.extend(_phase_1_skeleton_issues(project_dir))
    issues.extend(quality_gate_phase_1(project_dir))
    return _dedupe_issues(issues)


def _phase_2_reuse_issues(project_dir: Path) -> list[str]:
    issues = list(quality_gate_phase_2(project_dir))
    graph_path = project_dir / "graph" / "narrative_graph.json"
    image_prompts = list((project_dir / "frames" / "prompts").glob("*_image.json"))
    video_prompts = list((project_dir / "video" / "prompts").glob("*_video.json"))
    if not graph_path.exists():
        issues.append("graph/narrative_graph.json missing")
    if not image_prompts:
        issues.append("frames/prompts missing assembled image prompts")
    if not video_prompts:
        issues.append("video/prompts missing assembled video prompts")
    dialogue_report = project_dir / "logs" / "pipeline" / "dialogue_confirmation_report.json"
    if not dialogue_report.exists():
        issues.append("logs/pipeline/dialogue_confirmation_report.json missing")
    return _dedupe_issues(issues)


def _phase_reuse_status(phase_num: int, project_dir: Path) -> tuple[bool, list[str]]:
    if phase_num == 1:
        issues = _phase_1_reuse_issues(project_dir)
        return (not issues, issues)
    if phase_num == 2:
        issues = _phase_2_reuse_issues(project_dir)
        return (not issues, issues)
    return (True, [])


# ---------------------------------------------------------------------------
# Phase 1 helpers
# ---------------------------------------------------------------------------

_SCENE_DRAFT_RE = re.compile(r"scene_(\d+)_draft\.md$")


def _phase_checkpoint_path(phase_num: int) -> Path:
    PIPELINE_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return PIPELINE_LOGS_DIR / f"phase_{phase_num}_checkpoint.json"


def _write_phase_checkpoint(phase_num: int, payload: dict) -> None:
    path = _phase_checkpoint_path(phase_num)
    data = dict(payload)
    data["phase"] = phase_num
    data["timestamp"] = datetime.now(timezone.utc).isoformat()
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def _detect_scene_numbers_from_skeleton(skeleton_path: Path) -> list[int]:
    if not skeleton_path.exists():
        return []

    text = skeleton_path.read_text(encoding="utf-8")
    header_matches = {
        int(match)
        for match in re.findall(r"^\s*(?:#+\s*)?(?:SCENE|Scene)\s+(\d+)\b", text, re.MULTILINE)
    }
    if header_matches:
        return sorted(header_matches)

    tag_count = len(re.findall(r"^///SCENE:\s*", text, re.MULTILINE))
    return list(range(1, tag_count + 1))


def _detect_existing_scene_drafts(scenes_dir: Path, *, min_bytes: int = 200) -> dict[int, Path]:
    drafts: dict[int, Path] = {}
    if not scenes_dir.exists():
        return drafts

    for path in sorted(scenes_dir.glob("scene_*_draft.md")):
        match = _SCENE_DRAFT_RE.fullmatch(path.name)
        if not match:
            continue
        if path.stat().st_size < min_bytes:
            continue
        drafts[int(match.group(1))] = path
    return drafts


def _scan_phase_1_state(project_dir: Path) -> dict:
    creative_dir = project_dir / "creative_output"
    skeleton_path = creative_dir / "outline_skeleton.md"
    scenes_dir = creative_dir / "scenes"
    creative_output_path = creative_dir / "creative_output.md"
    skeleton_exists = skeleton_path.exists() and skeleton_path.stat().st_size > 0

    scene_numbers = _detect_scene_numbers_from_skeleton(skeleton_path) if skeleton_exists else []
    existing_drafts = _detect_existing_scene_drafts(scenes_dir)
    completed_scene_numbers = (
        [num for num in scene_numbers if num in existing_drafts]
        if scene_numbers
        else sorted(existing_drafts)
    )
    missing_scene_numbers = [num for num in scene_numbers if num not in existing_drafts]

    creative_output_exists = creative_output_path.exists() and creative_output_path.stat().st_size > 1000
    creative_output_stale = False
    if creative_output_exists and existing_drafts:
        creative_output_mtime = creative_output_path.stat().st_mtime
        creative_output_stale = any(
            path.stat().st_mtime > creative_output_mtime
            for path in existing_drafts.values()
        )

    return {
        "skeleton_exists": skeleton_exists,
        "skeleton_path": str(skeleton_path),
        "expected_scene_numbers": scene_numbers,
        "expected_scene_count": len(scene_numbers),
        "completed_scene_numbers": completed_scene_numbers,
        "completed_scene_count": len(completed_scene_numbers),
        "missing_scene_numbers": missing_scene_numbers,
        "missing_scene_count": len(missing_scene_numbers),
        "scene_draft_paths": {
            num: str(path) for num, path in sorted(existing_drafts.items())
        },
        "creative_output_exists": creative_output_exists,
        "creative_output_path": str(creative_output_path),
        "creative_output_stale": creative_output_stale,
    }


def _phase_1_min_scene_count(project_dir: Path) -> int:
    config = _read_onboarding_config(project_dir)
    return minimum_scene_count_for_frame_budget(
        config.get("frameBudget", config.get("outputSize"))
    )


def _phase_1_skeleton_issues(project_dir: Path) -> list[str]:
    skeleton_path = project_dir / "creative_output" / "outline_skeleton.md"
    if not skeleton_path.exists() or skeleton_path.stat().st_size == 0:
        return ["outline_skeleton.md missing"]

    text = skeleton_path.read_text(encoding="utf-8", errors="replace")
    issues: list[str] = []

    scene_count = len(re.findall(r"^///SCENE:\s*", text, re.MULTILINE))
    min_scene_count = _phase_1_min_scene_count(project_dir)
    if scene_count < min_scene_count:
        issues.append(
            f"only {scene_count} explicit ///SCENE tags found; expected at least {min_scene_count}"
        )

    placeholder_patterns = (
        r"\(Additional [^)]+ would follow",
        r"remaining \d+ scenes? follow",
        r"follow similar detailed format",
        r"due to length",
        r"scenes? \d+(?:-\d+)? .* cover",
        r"continuing with full scenes",
        r"remaining chronology",
        r"actual file",
        r"note on remaining scenes",
        r"removed per override",
    )
    for pattern in placeholder_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            issues.append(f"skeleton contains placeholder/summary text matching: {pattern}")

    referenced_scene_numbers = [
        int(match)
        for match in re.findall(r"\bscene[_ ]0*(\d+)\b", text, re.IGNORECASE)
    ]
    if scene_count and referenced_scene_numbers:
        max_referenced_scene = max(referenced_scene_numbers)
        if max_referenced_scene > scene_count:
            issues.append(
                f"skeleton references scene_{max_referenced_scene:02d} but only defines "
                f"{scene_count} explicit ///SCENE blocks"
            )

    if "## B. Character Roster" not in text:
        issues.append("missing character roster section")
    if "## C. Location Roster" not in text:
        issues.append("missing location roster section")
    if "## D. Prop Roster" not in text:
        issues.append("missing prop roster section")

    return issues


def _assemble_creative_output_from_drafts(project_dir: Path, scene_numbers: list[int]) -> Path:
    creative_dir = project_dir / "creative_output"
    scenes_dir = creative_dir / "scenes"
    output_path = creative_dir / "creative_output.md"
    creative_dir.mkdir(parents=True, exist_ok=True)

    assembled_parts: list[str] = []
    missing: list[int] = []
    for scene_num in scene_numbers:
        draft_path = scenes_dir / f"scene_{scene_num:02d}_draft.md"
        if not draft_path.exists():
            missing.append(scene_num)
            continue
        text = draft_path.read_text(encoding="utf-8").strip()
        if text:
            assembled_parts.append(text)

    if missing:
        raise FileNotFoundError(
            "Cannot assemble creative_output.md; missing scene draft(s): "
            + ", ".join(f"{num:02d}" for num in missing)
        )

    if not assembled_parts:
        raise ValueError("Cannot assemble creative_output.md; no scene draft content found")

    assembled_text = "\n\n".join(assembled_parts).strip() + "\n"
    tmp_path = output_path.with_suffix(".md.tmp")
    tmp_path.write_text(assembled_text, encoding="utf-8")
    os.replace(tmp_path, output_path)
    return output_path


def _normalized_onboarding_signature(project_dir: Path) -> dict:
    config = _read_onboarding_config(project_dir)
    normalized = {
        key: value
        for key, value in config.items()
        if key not in {"projectName", "projectId", "sourceFiles"}
    }

    source_digests: list[dict[str, str]] = []
    for rel_path in config.get("sourceFiles", []):
        abs_path = project_dir / rel_path
        if not abs_path.exists() or not abs_path.is_file():
            continue
        digest = hashlib.sha256(abs_path.read_bytes()).hexdigest()
        source_digests.append(
            {
                "name": abs_path.name,
                "sha256": digest,
                "size": str(abs_path.stat().st_size),
            }
        )

    normalized["sourceDigests"] = source_digests
    return normalized


def _find_phase_1_reuse_candidate(project_dir: Path) -> Path | None:
    target_sig = _normalized_onboarding_signature(project_dir)
    creative_dir = project_dir / "creative_output"

    for candidate in sorted(PROJECTS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if candidate == project_dir or not candidate.is_dir():
            continue
        candidate_creative = candidate / "creative_output"
        candidate_state = _scan_phase_1_state(candidate)
        if not candidate_state["skeleton_exists"] or not candidate_state["creative_output_exists"]:
            continue
        if candidate_creative == creative_dir:
            continue
        try:
            candidate_sig = _normalized_onboarding_signature(candidate)
        except Exception:
            continue
        if candidate_sig == target_sig:
            return candidate
    return None


def _restore_phase_1_from_matching_project(project_dir: Path) -> Path | None:
    candidate = _find_phase_1_reuse_candidate(project_dir)
    if candidate is None:
        return None

    source_dir = candidate / "creative_output"
    target_dir = project_dir / "creative_output"
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir, target_dir, dirs_exist_ok=True)
    return candidate


# ---------------------------------------------------------------------------
# Phase runners
# ---------------------------------------------------------------------------

def phase_0_verify(dry_run: bool) -> dict:
    """Phase 0 -- Scaffold already complete. Verify structure."""
    log_header("PHASE 0 -- Verify Scaffold")

    # Check manifest exists and is valid JSON
    if not MANIFEST_PATH.exists():
        fail(f"project_manifest.json not found at {MANIFEST_PATH}")
    try:
        manifest = read_manifest()
    except json.JSONDecodeError as e:
        fail(f"project_manifest.json is not valid JSON: {e}")

    # Check phase_0 status
    p0 = manifest.get("phases", {}).get("phase_0", {})
    if p0.get("status") != "complete":
        fail("Phase 0 is not marked complete in project_manifest.json. "
             "Run the scaffold agent first.")
    log_ok("Phase 0 already complete -- scaffold verified")

    # Check onboarding_config.json exists
    onboarding = PROJECT_DIR / "source_files" / "onboarding_config.json"
    if not onboarding.exists():
        fail(f"onboarding_config.json not found at {onboarding}")
    try:
        json.loads(onboarding.read_text())
        log_ok(f"onboarding_config.json valid ({onboarding.stat().st_size} bytes)")
    except json.JSONDecodeError as e:
        fail(f"onboarding_config.json is not valid JSON: {e}")

    # Verify story source exists — check onboarding sourceFiles, then fall back to defaults
    onboarding_cfg = json.loads(onboarding.read_text())
    source = None
    for sf in onboarding_cfg.get("sourceFiles", []):
        candidate = PROJECT_DIR / sf
        if candidate.exists():
            source = candidate
            break
    if source is None:
        # Legacy fallback
        for name in ("story_seed.txt", "pitch.md"):
            candidate = PROJECT_DIR / "source_files" / name
            if candidate.exists():
                source = candidate
                break
    if source is None:
        fail("No story source found — add source files or check onboarding_config.json sourceFiles")
    log_ok(f"{source.name} found ({source.stat().st_size} bytes)")

    # Check key subdirectories exist
    required_dirs = ["cast", "locations", "props", "creative_output",
                     "source_files", "assets", "frames", "audio", "video", "logs",
                     "scripts", "reports"]
    for d in required_dirs:
        dp = PROJECT_DIR / d
        if not dp.exists():
            log_warn(f"Expected directory missing: {d}/ -- scaffold may be incomplete")
        else:
            log_ok(f"Directory exists: {d}/")

    return manifest


def phase_1_narrative(dry_run: bool, phase_timers: dict) -> None:
    """Phase 1 -- Creative Coordinator writes skeleton (contracts), then
    parallel Grok prose workers write prose per scene, then CC assembles."""
    log_header("PHASE 1 -- Narrative (Contracts + Parallel Prose)")
    timer = Timer()
    phase_timers["phase_1"] = timer

    base = PROJECT_DIR
    creative_dir = base / "creative_output"
    scenes_dir = creative_dir / "scenes"
    skeleton_path = creative_dir / "outline_skeleton.md"
    creative_output_path = creative_dir / "creative_output.md"
    prompt_file = str(PROMPTS_DIR / "creative_coordinator.md")
    result_skeleton = None
    cc_skeleton_prefix = (
        "CRITICAL OVERRIDE — THIS SUPERSEDES ALL INSTRUCTIONS ABOVE.\n"
        "Complete ONLY the skeleton phase (Phase 1: ARCHITECT). "
        "Write creative_output/outline_skeleton.md with the full story foundation, "
        "character roster, location roster, per-scene construction specs, and "
        "continuity chain. Do NOT write scene prose. Do NOT write creative_output.md. "
        "Do NOT proceed to Phase 2 or Phase 3. Skeleton ONLY. "
        "Stop after the skeleton is complete and update your state.\n\n"
        "NON-NEGOTIABLE OUTPUT CONTRACT:\n"
        "- Write every required ///CAST, ///LOCATION, ///LOCATION_DIR, ///PROP, and ///SCENE tag explicitly.\n"
        "- If you claim N scenes, you must emit N distinct explicit ///SCENE blocks and N distinct scene sections.\n"
        "- Do NOT summarize omitted sections.\n"
        "- Do NOT write placeholder notes like 'additional scenes follow', 'remaining scenes cover', "
        "'continuing with full scenes', 'actual file', or 'note on remaining scenes'.\n"
        "- Cover the full source chronology from beginning through ending. Never stop early because a budget fills.\n"
        "- If frameBudget is numeric, compress density to fit it. If frameBudget is auto, use as many scenes as needed.\n"
        "- Overwrite creative_output/outline_skeleton.md completely with the final full skeleton.\n"
    )

    def _checkpoint(stage: str) -> dict:
        state = _scan_phase_1_state(base)
        payload = {
            "stage": stage,
            **state,
        }
        _write_phase_checkpoint(1, payload)
        return state

    def spawn_prose_worker(scene_num: int) -> tuple[int, subprocess.CompletedProcess, Timer]:
        worker_timer = Timer()
        prose_prefix = (
            f"CRITICAL OVERRIDE — THIS SUPERSEDES ALL INSTRUCTIONS ABOVE.\n"
            f"You are a PARALLEL PROSE WORKER. Ignore ALL phase logic, skeleton "
            f"writing, assembly, quality checks, and state updates above.\n"
            f"Your ONLY task:\n"
            f"  1. Read creative_output/outline_skeleton.md\n"
            f"  2. Write Scene {scene_num} prose to: "
            f"creative_output/scenes/scene_{scene_num:02d}_draft.md\n"
            f"  3. Print 'Scene {scene_num} complete.' and STOP.\n\n"
            f"Format: screenplay/novel hybrid. One paragraph = one frame.\n"
            f"Do NOT write other scenes. Do NOT write creative_output.md.\n"
            f"Do NOT write the skeleton. Do NOT run assembly. Do NOT run "
            f"quality checks. Do NOT update state.json or context.json.\n"
            f"Do NOT print summaries, checklists, or compliance tables.\n"
            f"Write the scene file, print the completion line, stop.\n"
        )
        result = run_agent(
            f"prose_worker_scene_{scene_num:02d}", prompt_file,
            dry_run=dry_run, prompt_prefix=prose_prefix,
            model=DEFAULT_STAGE1_REASONING_MODEL,
            timeout=300,
        )
        return scene_num, result, worker_timer

    def run_prose_batch(target_scene_numbers: list[int], *, attempt_label: str) -> dict[int, tuple[subprocess.CompletedProcess, Timer]]:
        if not target_scene_numbers:
            return {}
        log(f"{attempt_label}: spawning {len(target_scene_numbers)} prose worker(s) for missing scenes: "
            + ", ".join(f"{scene_num:02d}" for scene_num in target_scene_numbers))
        prose_results: dict[int, tuple[subprocess.CompletedProcess, Timer]] = {}
        with ThreadPoolExecutor(max_workers=min(len(target_scene_numbers), 10)) as executor:
            futures = {
                executor.submit(spawn_prose_worker, scene_num): scene_num
                for scene_num in target_scene_numbers
            }
            for future in as_completed(futures):
                scene_num = futures[future]
                try:
                    sn, res, wt = future.result()
                    prose_results[sn] = (res, wt)
                    log(f"Prose worker scene {sn} finished in {wt.elapsed_str()} "
                        f"(exit={res.returncode})")
                    if res.returncode != 0:
                        log_err(f"Prose worker scene {sn} failed")
                except Exception as e:
                    log_err(f"Prose worker scene {scene_num} raised: {e}")
        ok_count = sum(1 for result, _ in prose_results.values() if result.returncode == 0)
        log(f"{attempt_label}: {ok_count}/{len(target_scene_numbers)} worker(s) succeeded")
        return prose_results

    state = _checkpoint("phase_1_start")

    # Step 1: CC writes skeleton only (the contracts)
    if state["skeleton_exists"]:
        log("--- Phase 1a: Skeleton (contracts) ---")
        log_ok(
            f"Skipping skeleton agent — existing outline detected at {skeleton_path} "
            f"({skeleton_path.stat().st_size:,} bytes)"
        )
    else:
        log("--- Phase 1a: Skeleton (contracts) ---")
        result_skeleton = run_agent("creative_coordinator", prompt_file,
                                    dry_run=dry_run, prompt_prefix=cc_skeleton_prefix,
                                    model=DEFAULT_STAGE1_REASONING_MODEL)
        check_agent_result("creative_coordinator_skeleton", result_skeleton, timer)
        if not dry_run and skeleton_path.exists():
            skeleton_issues = _phase_1_skeleton_issues(base)
            if skeleton_issues:
                log_warn("Skeleton quality issues detected after first pass:")
                for issue in skeleton_issues:
                    print(f"  - {issue}", flush=True)
                correction_prefix = (
                    cc_skeleton_prefix
                    + "\nCORRECTION PASS:\n"
                    + "\n".join(f"- Fix this: {issue}" for issue in skeleton_issues)
                    + "\nReturn only after the rewritten outline_skeleton.md satisfies all of these constraints."
                )
                retry_timer = Timer()
                retry_result = run_agent(
                    "creative_coordinator",
                    prompt_file,
                    dry_run=dry_run,
                    prompt_prefix=correction_prefix,
                    model=DEFAULT_STAGE1_REASONING_MODEL,
                )
                check_agent_result("creative_coordinator_skeleton_retry", retry_result, retry_timer)
                result_skeleton = retry_result
        if not dry_run and not skeleton_path.exists():
            reused_from = _restore_phase_1_from_matching_project(base)
            if reused_from is not None:
                log_ok(
                    "Restored Phase 1 narrative outputs from matching project "
                    f"{reused_from.name}"
                )

    state = _checkpoint("skeleton_complete")

    # Step 2: Parallel prose workers write prose per scene
    log("--- Phase 1b: Parallel prose writing (Grok per scene) ---")
    if not dry_run:
        if not skeleton_path.exists():
            log_err("Skeleton not found — cannot dispatch parallel prose workers")
        else:
            scene_numbers = state["expected_scene_numbers"]
            if not scene_numbers:
                log_err("Could not detect any scenes from the skeleton — cannot dispatch prose workers")
            else:
                scenes_dir.mkdir(parents=True, exist_ok=True)
                existing_scene_numbers = state["completed_scene_numbers"]
                missing_scene_numbers = state["missing_scene_numbers"]
                log(
                    f"Detected {len(scene_numbers)} scene(s) in skeleton — "
                    f"{len(existing_scene_numbers)} already present, "
                    f"{len(missing_scene_numbers)} missing"
                )
                if existing_scene_numbers:
                    log(
                        "Skipping existing scene draft(s): "
                        + ", ".join(f"{scene_num:02d}" for scene_num in existing_scene_numbers)
                    )
                if missing_scene_numbers:
                    run_prose_batch(missing_scene_numbers, attempt_label="Parallel prose")
                else:
                    log_ok("All expected scene drafts already exist — skipping prose workers")
    else:
        log("[DRY-RUN] Would spawn parallel Grok prose workers per scene", YELLOW)

    state = _checkpoint("prose_complete")

    # Step 3: Deterministic assembly of all scene drafts into creative_output.md
    log("--- Phase 1c: Deterministic assembly ---")
    if not dry_run:
        if not state["expected_scene_numbers"]:
            log_warn("Skipping assembly — no scene numbers could be detected from the skeleton")
        elif state["missing_scene_numbers"]:
            log_warn(
                "Skipping assembly — missing scene draft(s): "
                + ", ".join(f"{scene_num:02d}" for scene_num in state["missing_scene_numbers"])
            )
        else:
            output_path = _assemble_creative_output_from_drafts(
                base,
                state["expected_scene_numbers"],
            )
            verb = "Rebuilt" if state["creative_output_exists"] else "Built"
            log_ok(
                f"{verb} creative_output.md deterministically from "
                f"{state['completed_scene_count']} scene draft(s) -> {output_path}"
            )
            state = _checkpoint("assembly_complete")
    else:
        log("[DRY-RUN] Would stitch scene drafts into creative_output.md deterministically", YELLOW)

    created_files = []
    if not dry_run:
        verify_files("1", [
            skeleton_path,
            creative_output_path,
        ])
        log("Files created by Phase 1:")
        list_dir_files(creative_dir)
        created_files = collect_files_in(creative_dir)
        save_phase_report(1, timer, "phase_1_hybrid", result_skeleton, created_files)

        # Quality gate
        if not run_quality_gate(1, base):
            log_warn("Phase 1 quality gate FAILED — retrying missing prose + deterministic assembly (attempt 2/2)...")
            retry_state = _scan_phase_1_state(base)
            if retry_state["missing_scene_numbers"]:
                run_prose_batch(retry_state["missing_scene_numbers"], attempt_label="Phase 1 recovery")
                retry_state = _scan_phase_1_state(base)
            if retry_state["expected_scene_numbers"] and not retry_state["missing_scene_numbers"]:
                _assemble_creative_output_from_drafts(base, retry_state["expected_scene_numbers"])
                _write_phase_checkpoint(1, {"stage": "assembly_rebuilt", **_scan_phase_1_state(base)})
            if not run_quality_gate(1, base):
                log_warn("Phase 1 quality gate still failing after retry — proceeding with warnings")

    advance_phase(1, 2, dry_run)
    log_ok(f"Phase 1 complete in {timer.elapsed_str()}")


def phase_2_morpheus(dry_run: bool, phase_timers: dict) -> None:
    """Phase 2 -- CC-First deterministic graph construction + enrichment.

    Step 2a:   Python parser (deterministic, <5 seconds)
               Reads CC output files → NarrativeGraph with all entities, frames, dialogue, edges.
    Step 2b:   Parallel frame enricher workers for per-frame enrichment (composition, environment, directing).
    Step 2b.5: Grok cinematic tagging — assigns CinematicTag to every FrameNode.
    Step 2c:   Continuity validator — deterministic graph integrity checks (no LLM).
    Step 2d:   Prompt assembly + materialization (existing deterministic post-processing).
    Optional:  Graph auditor agent spawned after Step 2c if --audit flag is set.
    """
    log_header("PHASE 2 -- CC-First Graph Build (Parser → Frame Enricher → Grok → Validator)")
    timer = Timer()
    phase_timers["phase_2"] = timer

    # ── Pre-flight: initialize graph dir ──────────────────────────────────
    if not dry_run:
        manifest = read_manifest()
        project_id = manifest.get("projectId", manifest.get("project", {}).get("id", "unknown"))
        graph_path = PROJECT_DIR / "graph" / "narrative_graph.json"
        if not graph_path.exists():
            log("Initializing narrative graph directory...")
            _stream_subprocess(
                [sys.executable, str(SKILLS_DIR / "graph_init"),
                 "--project-id", str(project_id), "--project-dir", str(PROJECT_DIR)],
                cwd=PROJECT_DIR, label="graph_init")
            log_ok("Graph directory initialized")

    # ── Build context seed (used by graph auditor agent if --audit) ────────
    if not dry_run:
        log("Building context seed...")
        seed = build_context_seed(PROJECT_DIR)
        seed_kb = len(seed.encode("utf-8")) // 1024
        log_ok(f"Context seed built: {seed_kb}KB")
    else:
        seed = ""

    # ── Step 2a: Python parser (deterministic, <5 seconds) ────────────────
    log_header("  STEP 2a — CC Parser (deterministic)")
    t2a = Timer()
    if not dry_run:
        from graph.cc_parser import parse_cc_output
        from graph.schema import ProjectNode
        from graph.store import GraphStore

        config = _read_onboarding_config(PROJECT_DIR)
        frame_budget = _project_frame_budget(PROJECT_DIR)
        project_node = ProjectNode(
            project_id=config.get("projectId", ""),
            title=config.get("projectName", ""),
            pipeline=config.get("pipeline", "story_upload"),
            creative_freedom=config.get("creativeFreedom", "balanced"),
            creative_freedom_permission=config.get("creativeFreedomPermission", ""),
            creative_freedom_failure_modes=config.get("creativeFreedomFailureModes", ""),
            dialogue_policy=config.get("dialoguePolicy", ""),
            frame_budget=frame_budget,
            output_size=derive_output_size_from_frame_budget(
                config.get("frameBudget", config.get("outputSize", "auto"))
            ),
            output_size_label=derive_output_size_label_from_frame_budget(
                config.get("frameBudget", config.get("outputSizeLabel", "auto"))
            ),
            frame_range=derive_frame_range_from_budget(
                config.get("frameBudget", config.get("frameRange"))
            ),
            media_style=config.get("mediaStyle", "live_clear"),
            media_style_prefix=config.get("mediaStylePrefix", ""),
            aspect_ratio=config.get("aspectRatio", "16:9"),
            style=config.get("style", []),
            genre=config.get("genre", []),
            mood=config.get("mood", []),
            extra_details=config.get("extraDetails", ""),
            source_files=config.get("sourceFiles", []),
        )

        graph = parse_cc_output(PROJECT_DIR, project_node)
        store = GraphStore(PROJECT_DIR)
        store.save(graph)
        from graph.reference_collector import ReferenceImageCollector

        ReferenceImageCollector(graph, Path(PROJECT_DIR)).sync_cast_bible(
            store=store,
            run_id=PIPELINE_RUN_ID,
            sequence_id=project_node.project_id,
        )
        log_ok(
            f"Parser complete in {t2a.elapsed_str()}: "
            f"{len(graph.frames)} frames, {len(graph.cast)} cast, "
            f"{len(graph.locations)} locations, {len(graph.props)} props, "
            f"{len(graph.edges)} edges"
        )
    else:
        log("[DRY-RUN] Would run cc_parser.parse_cc_output() → NarrativeGraph", YELLOW)

    # ── Step 2b: Parallel frame enrichment ────────────────────────────────
    log_header("  STEP 2b — Frame Enrichment (parallel per-frame)")
    t2b = Timer()
    if not dry_run:
        from graph.frame_enricher import (
            build_frame_enricher_inputs, apply_frame_enrichment,
        )

        restored_from_manifest = _rehydrate_phase_2_from_manifest(PROJECT_DIR, graph)
        if restored_from_manifest:
            log_ok(
                "Rehydrated existing Phase 2 enrichment from project_manifest.json for "
                f"{restored_from_manifest} frame(s)"
            )

        inputs = build_frame_enricher_inputs(graph)
        pending_inputs = _pending_frame_enricher_inputs(graph, inputs)
        if not pending_inputs:
            log_ok("All frames already contain core frame enrichment — skipping Step 2b")
            results = []
        else:
            log(
                f"Dispatching {len(pending_inputs)} frame enricher workers via local Grok runner "
                f"(max_concurrent=20, skipped={len(inputs) - len(pending_inputs)} already enriched)..."
            )
            results = _run_frame_enricher_cli_batch(
                pending_inputs,
                dry_run=dry_run,
                max_concurrent=20,
            )

        h_successes = 0
        h_failures = 0
        for result in results:
            if "error" in result:
                h_failures += 1
                log_warn(f"Frame {result.get('frame_id')} enrichment failed: {result['error']}")
            else:
                apply_frame_enrichment(graph, result)
                h_successes += 1
        store.save(graph)
        log_ok(
            f"Frame enrichment complete in {t2b.elapsed_str()}: "
            f"{h_successes} succeeded, {h_failures} failed"
        )
    else:
        log("[DRY-RUN] Would dispatch parallel frame-enricher per-frame enrichment (max_concurrent=20)", YELLOW)

    # ── Step 2b.5: Grok cinematic tagging ─────────────────────────────────
    log_header("  STEP 2b.5 — Grok Cinematic Tagging")
    t2b5 = Timer()
    if not dry_run:
        import asyncio as _asyncio2
        from graph.grok_tagger import tag_all_frames

        xai_key = os.getenv("XAI_API_KEY", "")
        if not xai_key:
            log_warn("XAI_API_KEY not set — skipping Grok cinematic tagging")
        else:
            tag_results = _asyncio2.run(tag_all_frames(PROJECT_DIR, api_key=xai_key))
            log_ok(
                f"Grok tagging complete in {t2b5.elapsed_str()}: "
                f"{tag_results.get('tagged', 0)} tagged, "
                f"{tag_results.get('failed', 0)} failed, "
                f"{tag_results.get('skipped', 0)} skipped"
            )
    else:
        log("[DRY-RUN] Would run Grok cinematic tagging (tag_all_frames)", YELLOW)

    # ── Step 2c: Self-recovering continuity validation ────────────────────
    log_header("  STEP 2c — Self-Recovering Continuity Validation")
    t2c = Timer()
    MAX_FIX_PASSES = 2
    if not dry_run:
        from graph.continuity_validator import validate_continuity
        from graph.frame_enricher import build_frame_enricher_inputs, apply_frame_enrichment
        from graph.store import GraphStore as _GraphStore

        _store_cv = _GraphStore(PROJECT_DIR)
        graph_cv = _store_cv.load()

        unresolved_issues: list[dict] = []

        for pass_num in range(1, MAX_FIX_PASSES + 1):
            log(f"  [Pass {pass_num}/{MAX_FIX_PASSES}] Running validate_continuity(fix=True)...")
            issues = validate_continuity(graph_cv, fix=True, project_dir=PROJECT_DIR)

            cv_errors = [i for i in issues if i["severity"] == "ERROR"]
            cv_warns  = [i for i in issues if i["severity"] == "WARN"]
            auto_fixed = [i for i in issues if i.get("auto_fixed")]
            needs_re_enrich = [i for i in issues if i.get("needs_re_enrichment") and not i.get("auto_fixed")]

            log(
                f"  Pass {pass_num}: {len(cv_errors)} error(s), {len(cv_warns)} warn(s) — "
                f"{len(auto_fixed)} auto-fixed, {len(needs_re_enrich)} need re-enrichment",
                YELLOW if (cv_errors or needs_re_enrich) else RESET,
            )
            for i in auto_fixed[:5]:
                log(f"    [FIXED] {i['check_name']}: {i['message']}")

            if needs_re_enrich and pass_num < MAX_FIX_PASSES:
                log(f"  Dispatching frame re-enrichment for {len(needs_re_enrich)} frame issue(s)...")
                issue_frame_ids = {
                    str(issue.get("frame_id", "")).strip()
                    for issue in needs_re_enrich
                    if issue.get("frame_id")
                }
                re_inputs = [
                    input_dict
                    for input_dict in build_frame_enricher_inputs(graph_cv)
                    if str(input_dict.get("frame_id", "")).strip() in issue_frame_ids
                ]
                re_results = _run_frame_enricher_cli_batch(
                    re_inputs,
                    dry_run=dry_run,
                    max_concurrent=10,
                )
                re_ok = 0
                for r in re_results:
                    if "error" not in r:
                        apply_frame_enrichment(graph_cv, r)
                        re_ok += 1
                log(f"  Re-enrichment complete: {re_ok}/{len(re_results)} succeeded")
                _store_cv.save(graph_cv)
                continue  # re-validate on next pass

            # Final pass or nothing left to fix
            unresolved_issues = [
                i for i in issues if not i.get("auto_fixed") and i.get("severity") == "ERROR"
            ]
            break

        if unresolved_issues:
            log_warn(
                f"Continuity validation: {len(unresolved_issues)} unresolved error(s) after "
                f"{MAX_FIX_PASSES} pass(es) — proceeding with warnings"
            )
            for e in unresolved_issues[:10]:
                log_warn(f"  [UNRESOLVED] {e['check_name']}: {e['message']}")
        else:
            log_ok(f"Continuity validation resolved in {t2c.elapsed_str()}")

        remaining_warns = [i for i in issues if i.get("severity") == "WARN" and not i.get("auto_fixed")]
        for w in remaining_warns[:5]:
            log(f"  [WARN] {w['check_name']}: {w['message']}", YELLOW)
    else:
        log("[DRY-RUN] Would run self-recovering validate_continuity(fix=True) loop", YELLOW)

    # ── Optional: Graph auditor agent (--audit mode only) ─────────────────
    if AUDIT_PHASE2:
        log_header("  GRAPH AUDITOR — QA Agent (--audit)")
        t_audit = Timer()
        if not dry_run:
            auditor_prompt = PROMPTS_DIR / "morpheus_graph_auditor.md"
            if auditor_prompt.exists():
                r_audit = run_agent(
                    "morpheus_graph_auditor",
                    str(auditor_prompt),
                    context_seed=seed, dry_run=False,
                )
                check_agent_result("morpheus_graph_auditor", r_audit, t_audit)
            else:
                log_warn(f"Graph auditor prompt not found: {auditor_prompt} — skipping")
        else:
            log("[DRY-RUN] Would spawn graph auditor agent (morpheus_graph_auditor.md)", YELLOW)

    # ── Step 2d: Prompt assembly + materialization ─────────────────────────
    if not dry_run:
        _run_phase_2_postprocessing(PROJECT_DIR)
    else:
        log("[DRY-RUN] Would run prompt assembly + materialization (_run_phase_2_postprocessing)", YELLOW)

    # ── Verification ──────────────────────────────────────────────────────
    created_files = []
    if not dry_run:
        base = PROJECT_DIR
        verify_files("2", [base / "dialogue.json"])
        for sub in ("cast", "locations", "props"):
            d = base / sub
            if not any(d.glob("*.json")):
                log_warn(f"No JSON files found in {sub}/ — parser may not have materialized fully.")
        manifest = read_manifest()
        if not manifest.get("frames"):
            log_warn("manifest.frames[] is empty — parser may not have populated frames.")

        # Verify graph exists and has data
        graph_path = base / "graph" / "narrative_graph.json"
        if graph_path.exists():
            log_ok(f"Narrative graph: {graph_path.stat().st_size // 1024}KB")
        else:
            log_warn("narrative_graph.json not found — parser may not have saved graph")

        # Verify prompt files exist
        frame_prompts = (
            list((base / "frames" / "prompts").glob("*_image.json"))
            if (base / "frames" / "prompts").exists() else []
        )
        video_prompts = (
            list((base / "video" / "prompts").glob("*_video.json"))
            if (base / "video" / "prompts").exists() else []
        )
        log_ok(f"Assembled prompts: {len(frame_prompts)} image, {len(video_prompts)} video")

        log("Files created by Phase 2:")
        for sub in ("cast", "locations", "props", "graph"):
            list_dir_files(base / sub)
            created_files.extend(collect_files_in(base / sub))
        save_phase_report(2, timer, "cc_first_pipeline",
                          subprocess.CompletedProcess([], 0, "", ""), created_files)

        # Quality gate — retry postprocessing only (no agent spawns)
        if not run_quality_gate(2, base):
            log_warn("Phase 2 quality gate FAILED — re-running postprocessing (attempt 2/2)...")
            _run_phase_2_postprocessing(PROJECT_DIR)
            if not run_quality_gate(2, base):
                log_warn("Phase 2 quality gate still failing after retry — proceeding with warnings")

    advance_phase(2, 3, dry_run)
    log_ok(f"Phase 2 complete in {timer.elapsed_str()}")


def _programmatic_image_checks(base: Path) -> list[str]:
    """Run programmatic checks on generated images. Returns list of issues."""
    issues = []
    for subdir, label in [
        (base / "cast" / "composites", "cast composite"),
        (base / "locations" / "primary", "location image"),
        (base / "props" / "generated", "prop image"),
    ]:
        if not subdir.exists():
            continue
        for img in subdir.glob("*.png"):
            if img.stat().st_size < 10240:
                issues.append(f"{label} {img.name} is {img.stat().st_size} bytes — likely corrupt")
    return issues


# ---------------------------------------------------------------------------
# Prompt-to-output mapping for each asset type
# ---------------------------------------------------------------------------
_ASSET_PROMPT_MAP = [
    # (prompt_dir_relative, output_dir_relative, prompt_suffix, output_suffix, target_type, id_field)
    ("cast/prompts", "cast/composites", "_composite.json", "_ref.png", "cast", "castId"),
    ("locations/prompts", "locations/primary", "_location.json", ".png", "location", "locationId"),
    ("props/prompts", "props/generated", "_prop.json", ".png", "prop", "propId"),
]

# Maps project aspectRatio (from onboarding_config.json) → expected image size key.
# Cast composites are always portrait_9_16 regardless of project AR and are excluded
# from this check. Location and prop prompts must match the project's declared AR.
_AR_TO_SIZE_MAP: dict[str, str] = {
    "16:9": "landscape_16_9",
    "9:16": "portrait_9_16",
    "4:3": "landscape_4_3",
    "1:1": "square_hd",
}

MIN_VALID_SIZE = 10240  # 10KB — anything smaller is likely corrupt/empty
MAX_REGEN_ATTEMPTS = 2


def _programmatic_asset_validation_and_regen(base: Path) -> None:
    """Programmatic replacement for the image_verifier agent.

    For each asset type (cast/location/prop):
      1. Build the canonical prompt from the graph.
      2. Check if the output image exists and is valid (>10KB).
      3. Re-generate missing or corrupt images via sw_fresh_generation.
      4. Write successful asset paths back into the graph and re-project manifest.
    """
    from graph.prompt_assembler import (
        assemble_composite_prompt,
        assemble_location_prompt,
        assemble_prop_prompt,
    )
    from graph.runtime_state import (
        mark_cast_asset,
        mark_location_asset,
        mark_prop_asset,
        save_graph_projection,
    )
    from graph.store import GraphStore

    stats = {"reviewed": 0, "missing_regen": 0, "corrupt_regen": 0, "regen_ok": 0, "regen_fail": 0}

    # Load the project's declared aspect ratio once so we can validate prompt sizes
    # against it before calling sw_fresh_generation.  Cast composites are always
    # portrait_9_16 and are excluded from this check (see _AR_TO_SIZE_MAP).
    _onboarding = _read_onboarding_config(base)
    project_ar: str = str(_onboarding.get("aspectRatio", "")).strip()
    run_id = os.environ.get(RUN_ID_ENV, PIPELINE_RUN_ID)
    phase = os.environ.get(PHASE_ENV, "")
    store = GraphStore(str(base))
    graph = store.load()
    graph_dirty = False

    prompt_builders = {
        "cast": (graph.cast, assemble_composite_prompt),
        "location": (graph.locations, assemble_location_prompt),
        "prop": (graph.props, assemble_prop_prompt),
    }

    for target, (registry, prompt_builder) in prompt_builders.items():
        for entity_id in sorted(registry.keys()):
            prompt_data = prompt_builder(graph, entity_id)
            output_path = base / prompt_data["out_path"]
            prompt_file = base / {
                "cast": f"cast/prompts/{entity_id}_composite.json",
                "location": f"locations/prompts/{entity_id}_location.json",
                "prop": f"props/prompts/{entity_id}_prop.json",
            }[target]
            stats["reviewed"] += 1

            needs_regen = False
            if not output_path.exists():
                log_warn(f"Missing {target} image: {output_path.name}")
                stats["missing_regen"] += 1
                needs_regen = True
            elif output_path.stat().st_size < MIN_VALID_SIZE:
                log_warn(f"Corrupt {target} image ({output_path.stat().st_size}B): {output_path.name}")
                stats["corrupt_regen"] += 1
                needs_regen = True

            if needs_regen:
                gen_prompt = prompt_data.get("prompt", prompt_data.get("text", ""))
                if not gen_prompt:
                    log_warn(f"  No prompt available for {entity_id} — skipping regen")
                    stats["regen_fail"] += 1
                    continue

                try:
                    size = _resolve_regen_image_size(prompt_data, prompt_file=prompt_file)
                except ValueError as exc:
                    log_warn(f"  {exc} — skipping regen")
                    stats["regen_fail"] += 1
                    continue

                if target in ("location", "prop") and project_ar:
                    expected_size = _AR_TO_SIZE_MAP.get(project_ar, "landscape_16_9")
                    if size != expected_size:
                        log_warn(
                            f"  {prompt_file.name}: aspect ratio mismatch — "
                            f"prompt size={size!r} but project aspectRatio={project_ar!r} "
                            f"expects {expected_size!r}. Proceeding with prompt value."
                        )

                regen_ok = False
                for attempt in range(1, MAX_REGEN_ATTEMPTS + 1):
                    log(f"  Regenerating {output_path.name} (attempt {attempt}/{MAX_REGEN_ATTEMPTS})...")
                    regen_result = _stream_subprocess(
                        [
                            sys.executable,
                            str(SKILLS_DIR / "sw_fresh_generation"),
                            "--prompt",
                            gen_prompt,
                            "--size",
                            size,
                            "--out",
                            str(output_path),
                            "--run-id",
                            run_id,
                            "--phase",
                            phase,
                        ],
                        cwd=base,
                        timeout=120,
                        label=f"regen_{entity_id}",
                    )
                    if regen_result.returncode == 0 and output_path.exists() and output_path.stat().st_size >= MIN_VALID_SIZE:
                        log_ok(f"  Regenerated {output_path.name} successfully")
                        regen_ok = True
                        break
                    gen_prompt = gen_prompt.replace("sexy", "").replace("violent", "").replace("bloody", "").strip()

                if regen_ok:
                    stats["regen_ok"] += 1
                else:
                    stats["regen_fail"] += 1
                    log_warn(f"  Failed to regenerate {output_path.name} after {MAX_REGEN_ATTEMPTS} attempts — skipping")
                    continue

            if output_path.exists() and output_path.stat().st_size >= MIN_VALID_SIZE:
                rel_path = str(output_path.relative_to(base))
                if target == "cast":
                    mark_cast_asset(graph, entity_id, rel_path, run_id=run_id, actor="asset_validation", phase=phase)
                elif target == "location":
                    mark_location_asset(graph, entity_id, rel_path, run_id=run_id, actor="asset_validation", phase=phase)
                elif target == "prop":
                    mark_prop_asset(graph, entity_id, rel_path, run_id=run_id, actor="asset_validation", phase=phase)
                graph_dirty = True

    if graph_dirty:
        save_graph_projection(graph, base, store=store)
        log_ok("Graph and manifest refreshed from validated asset outputs")

    log(f"Asset validation complete: {stats['reviewed']} reviewed, "
        f"{stats['missing_regen']} missing, {stats['corrupt_regen']} corrupt, "
        f"{stats['regen_ok']} regenerated OK, {stats['regen_fail']} failed")


def _verify_storyboard_refs_tagged() -> tuple[bool, list[str]]:
    """Collect all ref images from grid storyboard prompts and verify each is tagged.

    Runs after asset generation + batch tagging but before storyboard generation.
    Any untagged images in TAGGED_DIRS are tagged now. Missing files are logged.

    Returns (all_ok, problems) so the caller can decide whether to proceed.
    """
    prompt_dir = PROJECT_DIR / "frames" / "storyboard_prompts"
    if not prompt_dir.exists():
        return True, []

    prompt_files = sorted(prompt_dir.glob("*_grid.json"))
    if not prompt_files:
        # Fallback: check for legacy naming
        prompt_files = sorted(prompt_dir.glob("*_storyboard.json"))
    if not prompt_files:
        return True, []

    # Collect all unique ref image paths across all storyboard prompts
    all_refs: list[str] = []
    for pf in prompt_files:
        data = json.loads(pf.read_text(encoding="utf-8"))
        all_refs.extend(data.get("refs", data.get("ref_images", [])))

    unique_refs = list(dict.fromkeys(all_refs))  # dedupe, preserve order
    if not unique_refs:
        log_warn("No ref_images found in storyboard prompts — storyboards will have no visual references")
        return False, ["NO_REFS: storyboard prompts contain zero ref_images"]

    log(f"Verifying {len(unique_refs)} ref images are tagged before storyboard generation...")

    try:
        from image_tagger import verify_ref_images_tagged
        all_ok, problems = verify_ref_images_tagged(PROJECT_DIR, unique_refs)
        if all_ok:
            log_ok(f"All {len(unique_refs)} storyboard ref images verified tagged")
        else:
            log_warn(f"Tag verification found {len(problems)} issue(s):")
            for p in problems:
                print(f"  - {p}", flush=True)
        return all_ok, problems
    except Exception as e:
        log_warn(f"Tag verification failed: {e}")
        return False, [f"EXCEPTION: {e}"]


def _generate_single_grid_storyboard(prompt_data: dict) -> dict | None:
    """Generate one grid storyboard via the grid_generate module.

    Returns the result dict from grid_generate.generate() on success,
    or None on failure.
    """
    import asyncio as _asyncio

    grid_id = prompt_data.get("grid_id", "unknown")
    grid_layout = prompt_data.get("grid", "1x1")
    cell_prompts = prompt_data.get("cell_prompts", [])
    scene = prompt_data.get("scene", "")
    frame_ids = prompt_data.get("frame_ids", [])
    style_prefix = prompt_data.get("style_prefix", "")
    output_dir = PROJECT_DIR / prompt_data.get("output_dir", f"frames/storyboards/{grid_id}")
    output_dir.mkdir(parents=True, exist_ok=True)

    composite = output_dir / "composite.png"

    # Skip if already generated
    if composite.exists() and composite.stat().st_size > 1000:
        log(f"  {grid_id}: already exists ({composite.stat().st_size:,}B) — skipping")
        # Return a result-like dict so caller can still update the graph
        frames_dir = output_dir / "frames"
        # Check for frame-ID-named cells first, fall back to legacy frame_NNN.png
        frame_files = sorted(frames_dir.glob("f_*.png")) if frames_dir.exists() else []
        if not frame_files:
            frame_files = sorted(frames_dir.glob("frame_*.png")) if frames_dir.exists() else []
        return {
            "composite": str(composite),
            "frames": [str(f) for f in frame_files],
            "grid": grid_layout,
        }

    # Resolve reference images to absolute paths
    ref_paths = []
    for ref in prompt_data.get("refs", []):
        ref_path = PROJECT_DIR / ref if not Path(ref).is_absolute() else Path(ref)
        if ref_path.exists():
            ref_paths.append(str(ref_path))
        else:
            log_warn(f"  {grid_id}: ref image missing: {ref}")

    log(f"  {grid_id}: generating {grid_layout} storyboard ({len(ref_paths)} ref images)...")

    log(f"  {grid_id}: {len(cell_prompts)} cell prompts stacked into {grid_layout} template")

    try:
        from graph.grid_generate import generate as grid_generate
        result = _asyncio.run(grid_generate(
            grid=grid_layout,
            output_dir=output_dir,
            refs=ref_paths,
            cell_prompts=cell_prompts,
            scene=scene,
            frame_ids=frame_ids,
            style_prefix=style_prefix,
            grid_id=grid_id,
            run_id=PIPELINE_RUN_ID,
            phase=os.environ.get(PHASE_ENV, ""),
        ))
        log_ok(f"  {grid_id}: storyboard generated → {result.get('composite', '')}")
        return result
    except Exception as e:
        log_warn(f"  {grid_id}: grid generation failed: {e}")
        return None


async def _generate_single_grid_storyboard_async(prompt_data: dict) -> dict | None:
    """Async variant of _generate_single_grid_storyboard.

    Calls grid_generate() directly with ``await`` so it can be used inside
    a running event loop (e.g. asyncio.gather batches).  All skip-if-exists
    and ref-resolution logic is identical to the sync version.
    """
    grid_id = prompt_data.get("grid_id", "unknown")
    grid_layout = prompt_data.get("grid", "1x1")
    cell_prompts = prompt_data.get("cell_prompts", [])
    scene = prompt_data.get("scene", "")
    frame_ids = prompt_data.get("frame_ids", [])
    style_prefix = prompt_data.get("style_prefix", "")
    output_dir = PROJECT_DIR / prompt_data.get("output_dir", f"frames/storyboards/{grid_id}")
    output_dir.mkdir(parents=True, exist_ok=True)

    composite = output_dir / "composite.png"

    # Skip if already generated
    if composite.exists() and composite.stat().st_size > 1000:
        log(f"  {grid_id}: already exists ({composite.stat().st_size:,}B) — skipping")
        frames_dir = output_dir / "frames"
        frame_files = sorted(frames_dir.glob("f_*.png")) if frames_dir.exists() else []
        if not frame_files:
            frame_files = sorted(frames_dir.glob("frame_*.png")) if frames_dir.exists() else []
        return {
            "composite": str(composite),
            "frames": [str(f) for f in frame_files],
            "grid": grid_layout,
        }

    # Resolve reference images to absolute paths
    ref_paths = []
    for ref in prompt_data.get("refs", []):
        ref_path = PROJECT_DIR / ref if not Path(ref).is_absolute() else Path(ref)
        if ref_path.exists():
            ref_paths.append(str(ref_path))
        else:
            log_warn(f"  {grid_id}: ref image missing: {ref}")

    log(f"  {grid_id}: generating {grid_layout} storyboard ({len(ref_paths)} ref images)...")
    log(f"  {grid_id}: {len(cell_prompts)} cell prompts stacked into {grid_layout} template")

    try:
        from graph.grid_generate import generate as grid_generate
        result = await grid_generate(
            grid=grid_layout,
            output_dir=output_dir,
            refs=ref_paths,
            cell_prompts=cell_prompts,
            scene=scene,
            frame_ids=frame_ids,
            style_prefix=style_prefix,
            grid_id=grid_id,
            run_id=PIPELINE_RUN_ID,
            phase=os.environ.get(PHASE_ENV, ""),
        )
        log_ok(f"  {grid_id}: storyboard generated → {result.get('composite', '')}")
        return result
    except Exception as exc:
        log_warn(f"  {grid_id}: grid generation failed: {exc}")
        return None


def _update_grid_graph(grid_id: str, result: dict) -> None:
    """Update StoryboardGrid on graph with generated paths."""
    try:
        from graph.runtime_state import mark_storyboard_asset, project_relative_path, save_graph_projection
        from graph.store import GraphStore

        store = GraphStore(str(PROJECT_DIR))
        graph = store.load()
        frames = result.get("frames", [])
        mark_storyboard_asset(
            graph,
            grid_id,
            composite_path=project_relative_path(PROJECT_DIR, result.get("composite")),
            cell_image_dir=project_relative_path(PROJECT_DIR, str(Path(frames[0]).parent)) if frames else None,
            run_id=PIPELINE_RUN_ID,
            actor="storyboard_generation",
            phase=os.environ.get(PHASE_ENV, ""),
        )
        save_graph_projection(graph, PROJECT_DIR, store=store)
    except Exception as e:
        log_warn(f"  Could not update grid {grid_id} on graph: {e}")


def _generate_storyboard_grids_phase3(dry_run: bool) -> None:
    """Generate storyboard grid composites + split cell images.
    Sequential — each grid needs the prior grid's output for cascading continuity."""
    if dry_run:
        log("[DRY-RUN] Would generate storyboard grids", YELLOW)
        return

    # Build storyboard grids
    build_result = _stream_subprocess(
        [sys.executable, str(SKILLS_DIR / "graph_build_grids"),
         "--project-dir", str(PROJECT_DIR)],
        cwd=PROJECT_DIR, label="graph_build_grids")
    if build_result.returncode != 0:
        log_warn(f"Grid build failed (exit {build_result.returncode}) — skipping storyboard generation")
        return

    # Re-assemble prompts so grid storyboard prompts are generated
    assemble_result = _stream_subprocess(
        [sys.executable, str(SKILLS_DIR / "graph_assemble_prompts"),
         "--project-dir", str(PROJECT_DIR)],
        cwd=PROJECT_DIR, label="graph_assemble_prompts")
    if assemble_result.returncode != 0:
        log_warn(f"Prompt re-assembly failed after grid build (exit {assemble_result.returncode})")
        return
    _run_project_report(PROJECT_DIR)

    # Generate storyboards from prompt JSONs in order so each grid can inherit
    # the previous grid's output as continuity guidance.
    prompt_dir = PROJECT_DIR / "frames" / "storyboard_prompts"
    if not prompt_dir.exists():
        log_warn("No storyboard_prompts directory — skipping storyboard generation")
        return

    prompt_files = sorted(prompt_dir.glob("*_grid.json"))
    if not prompt_files:
        log_warn("No grid storyboard prompt JSONs found — skipping")
        return

    # Tag verification gate
    refs_ok, ref_problems = _verify_storyboard_refs_tagged()
    if not refs_ok:
        log_warn(f"Storyboard ref verification failed ({len(ref_problems)} issue(s)) — "
                 "storyboards will generate with incomplete references")

    total = len(prompt_files)
    log(f"Generating {total} storyboard guidance grids (batches of 10)...")
    generated = 0

    # Parse all prompt files upfront so JSON errors surface before any network work
    parsed: list[tuple] = []  # (pf, data | None)
    for pf in prompt_files:
        try:
            parsed.append((pf, json.loads(pf.read_text(encoding="utf-8"))))
        except Exception as e:
            log_warn(f"  Could not parse {pf.name}: {e}")
            parsed.append((pf, None))

    import asyncio as _asyncio

    async def _run_batches() -> int:
        """Run all grids in batches of 10, returns count of successes."""
        _generated = 0
        batch_size = 10
        total_batches = (len(parsed) + batch_size - 1) // batch_size

        for batch_idx in range(total_batches):
            batch = parsed[batch_idx * batch_size:(batch_idx + 1) * batch_size]
            batch_num = batch_idx + 1
            log(f"  Batch {batch_num}/{total_batches} ({len(batch)} grids)...")

            async def _run_one(pf, data):
                if data is None:
                    return pf, data, None
                try:
                    result = await _generate_single_grid_storyboard_async(data)
                    return pf, data, result
                except Exception as exc:
                    log_warn(f"  Storyboard generation failed for {pf.name}: {exc}")
                    return pf, data, None

            # Launch up to 10 grids concurrently; individual failures are isolated
            coros = [_run_one(pf, data) for pf, data in batch]
            batch_results = await _asyncio.gather(*coros)

            ok = fail = 0
            for pf, data, result in batch_results:
                if result and data:
                    _generated += 1
                    ok += 1
                    grid_id = data.get("grid_id")
                    if grid_id:
                        _update_grid_graph(grid_id, result)
                else:
                    fail += 1
            log(f"  Batch {batch_num} done — {ok} OK, {fail} failed")

        return _generated

    generated = _asyncio.run(_run_batches())

    log_ok(f"Generated {generated}/{total} storyboard grids")

    # Shot matching step
    log("Running shot matching on generated grids...")
    try:
        from graph.store import GraphStore
        from graph.api import match_shots_in_grid
        store = GraphStore(str(PROJECT_DIR))
        graph = store.load()
        matched = 0
        for grid in graph.storyboard_grids.values():
            groups = match_shots_in_grid(graph, grid)
            matched += len(groups)
        store.save(graph)
        log_ok(f"Shot matching complete: {matched} match groups across all grids")
    except Exception as e:
        log_warn(f"Shot matching failed: {e}")

    # Re-assemble prompts so frame ref_images now include grid cell images
    log("Re-assembling prompts with grid storyboard references...")
    final_assemble = _stream_subprocess(
        [sys.executable, str(SKILLS_DIR / "graph_assemble_prompts"),
         "--project-dir", str(PROJECT_DIR)],
        cwd=PROJECT_DIR, label="graph_assemble_prompts")
    if final_assemble.returncode == 0:
        _run_project_report(PROJECT_DIR)
    log_ok("Prompts re-assembled with grid storyboard references")

    # Persist storyboard grid metadata to project_manifest.json so downstream
    # manifest-driven phases can see the generated storyboard state.
    try:
        from graph.store import GraphStore
        from graph.materializer import materialize_manifest
        store = GraphStore(str(PROJECT_DIR))
        graph = store.load()
        materialize_manifest(graph, MANIFEST_PATH)
        log_ok("Manifest refreshed with storyboard grid metadata")
    except Exception as e:
        log_warn(f"Could not refresh manifest after storyboard generation: {e}")



def phase_3_assets(dry_run: bool, phase_timers: dict) -> None:
    """Phase 3 -- Fully programmatic asset generation, storyboard guidance, and validation."""
    log_header("PHASE 3 -- Asset Generation + Storyboards (Programmatic)")
    timer = Timer()
    phase_timers["phase_3"] = timer

    # Start image tagger watcher — auto-tags reference images as they're generated
    tag_watcher = None
    if not dry_run:
        try:
            from image_tagger import start_tag_watcher, stop_tag_watcher
            tag_watcher = start_tag_watcher(PROJECT_DIR)
        except Exception as e:
            log_warn(f"Image tagger watcher failed to start: {e}")

    # Step 3a: Programmatic generation of cast/location/prop base images
    log("--- Phase 3a: Programmatic asset generation (cast, locations, props) ---")
    if not dry_run:
        gen_result = _stream_subprocess(
            [sys.executable, str(SKILLS_DIR / "graph_generate_assets"),
             "--project-dir", str(PROJECT_DIR), "--batch-size", "10",
             "--skip-existing", "--types", "cast,locations,props"],
            cwd=PROJECT_DIR, label="graph_generate_assets",
        )
        if gen_result.returncode != 0:
            log_warn(f"Asset generation had failures (exit {gen_result.returncode})")
        else:
            log_ok("All base assets generated programmatically")

    # Batch-tag all reference images (catches anything the watcher missed)
    if not dry_run:
        try:
            from image_tagger import tag_all_project_images
            tag_count, tagged_paths = tag_all_project_images(PROJECT_DIR)
            if tag_count:
                log_ok(f"Tagged {tag_count} reference image(s)")
        except Exception as e:
            log_warn(f"Image tagger batch failed: {e}")

    # Step 3b: Programmatic image validation (no agent)
    log("--- Phase 3b: Programmatic image validation ---")
    if not dry_run:
        base = PROJECT_DIR
        img_issues = _programmatic_image_checks(base)
        if img_issues:
            log_warn(f"Image checks found {len(img_issues)} issue(s):")
            for issue in img_issues:
                print(f"  - {issue}", flush=True)
        else:
            log_ok("All generated images pass size/integrity checks")

    # Step 3c: Project prompt audit logs from the canonical graph state.
    log("--- Phase 3c: Project prompt audit logs from graph ---")
    if not dry_run:
        phase3c_assemble = _stream_subprocess(
            [sys.executable, str(SKILLS_DIR / "graph_assemble_prompts"),
             "--project-dir", str(PROJECT_DIR)],
            cwd=PROJECT_DIR, label="graph_assemble_prompts_post_assets")
        if phase3c_assemble.returncode == 0:
            _run_project_report(PROJECT_DIR)
        log_ok("Prompt audit logs refreshed from graph state")

    # Step 3d: Validate assets + update manifest so graph has real paths
    log("--- Phase 3d: Asset validation + manifest sync ---")
    if not dry_run:
        _programmatic_asset_validation_and_regen(PROJECT_DIR)

    # Step 3e: Re-project prompt audit logs after any validation/regeneration.
    log("--- Phase 3e: Refresh prompt audit logs after validation ---")
    if not dry_run:
        phase3e_assemble = _stream_subprocess(
            [sys.executable, str(SKILLS_DIR / "graph_assemble_prompts"),
             "--project-dir", str(PROJECT_DIR)],
            cwd=PROJECT_DIR, label="graph_assemble_prompts")
        if phase3e_assemble.returncode == 0:
            _run_project_report(PROJECT_DIR)
        log_ok("Prompt audit logs refreshed with validated asset paths")

    # Step 3f: Storyboard generation (optional guidance layer)
    log("--- Phase 3f: Storyboard grid generation ---")
    if ENABLE_STORYBOARD_GUIDANCE:
        _generate_storyboard_grids_phase3(dry_run)
    else:
        log("Storyboard guidance disabled — bypassing storyboard prompt/composite generation")

    # Step 3g: Project cover artwork poster + summary
    log("--- Phase 3g: Project cover artwork poster ---")
    if not dry_run:
        _generate_project_cover_art(PROJECT_DIR, dry_run=dry_run)
        _run_project_report(PROJECT_DIR)

    created_files = []
    if not dry_run:
        base = PROJECT_DIR
        log("Files created by Phase 3:")
        list_dir_files(base / "cast" / "composites")
        list_dir_files(base / "locations" / "primary")
        list_dir_files(base / "props" / "generated")
        list_dir_files(base / "frames" / "storyboards")
        list_dir_files(base / "reports")
        created_files = (collect_files_in(base / "cast" / "composites") +
                         collect_files_in(base / "locations" / "primary") +
                         collect_files_in(base / "props" / "generated") +
                         collect_files_in(base / "frames" / "storyboards") +
                         collect_files_in(base / "reports"))
        save_phase_report(3, timer, "phase_3_programmatic", None, created_files)

        # Quality gate (programmatic checks only)
        if not run_quality_gate(3, base):
            log_warn("Phase 3 quality gate FAILED — re-running asset regen (attempt 2/2)...")
            _programmatic_asset_validation_and_regen(base)
            if not run_quality_gate(3, base):
                log_warn("Phase 3 quality gate still failing after retry — proceeding with warnings")

    # Stop image tagger watcher
    if tag_watcher is not None:
        try:
            stop_tag_watcher(tag_watcher)
        except Exception:
            pass

    advance_phase(3, 4, dry_run)
    log_ok(f"Phase 3 complete in {timer.elapsed_str()}")


def _audit_phase4_assets() -> dict:
    """Check that all expected Phase 3 assets exist before composing frames.

    Returns dict with:
        ready: bool — True if all critical assets present
        missing_cast: list[str] — cast IDs with missing/corrupt composites
        missing_locations: list[str] — location IDs with missing/corrupt images
        missing_props: list[str] — prop IDs with missing/corrupt images
        missing_storyboards: list[str] — grid IDs with missing storyboard composites (advisory)
        total_missing: int
    """
    from graph.store import GraphStore

    store = GraphStore(str(PROJECT_DIR))
    graph = store.load()
    base = PROJECT_DIR

    missing_cast = []
    missing_locations = []
    missing_props = []
    missing_storyboards = []

    # Cast composites
    for cast_id, cast in graph.cast.items():
        if cast.composite_path:
            p = base / cast.composite_path
            if not p.exists() or p.stat().st_size < MIN_VALID_SIZE:
                missing_cast.append(cast_id)
        else:
            # No path recorded — check expected location
            expected = base / "cast" / "composites" / f"{cast_id}_ref.png"
            if not expected.exists() or expected.stat().st_size < MIN_VALID_SIZE:
                missing_cast.append(cast_id)

    # Location images
    for loc_id, loc in graph.locations.items():
        if loc.primary_image_path:
            p = base / loc.primary_image_path
            if not p.exists() or p.stat().st_size < MIN_VALID_SIZE:
                missing_locations.append(loc_id)
        else:
            expected = base / "locations" / "primary" / f"{loc_id}.png"
            if not expected.exists() or expected.stat().st_size < MIN_VALID_SIZE:
                missing_locations.append(loc_id)

    # Prop images
    for prop_id, prop in graph.props.items():
        if prop.image_path:
            p = base / prop.image_path
            if not p.exists() or p.stat().st_size < MIN_VALID_SIZE:
                missing_props.append(prop_id)
        else:
            expected = base / "props" / "generated" / f"{prop_id}.png"
            if not expected.exists() or expected.stat().st_size < MIN_VALID_SIZE:
                missing_props.append(prop_id)

    if ENABLE_STORYBOARD_GUIDANCE:
        for grid_id, grid in graph.storyboard_grids.items():
            if grid.composite_image_path:
                p = Path(grid.composite_image_path) if Path(grid.composite_image_path).is_absolute() else base / grid.composite_image_path
                if not p.exists() or p.stat().st_size < 1000:
                    missing_storyboards.append(grid_id)
            else:
                expected = base / "frames" / "storyboards" / grid_id / "composite.png"
                if not expected.exists() or expected.stat().st_size < 1000:
                    missing_storyboards.append(grid_id)

    total = len(missing_cast) + len(missing_locations) + len(missing_props)

    return {
        "ready": total == 0,
        "missing_cast": missing_cast,
        "missing_locations": missing_locations,
        "missing_props": missing_props,
        "missing_storyboards": missing_storyboards,
        "total_missing": total,
    }


def _run_final_frame_worker(
    frame_id: str,
    prompt_data: dict,
    out_rel: str,
) -> dict:
    """Generate one final frame and return structured result data."""
    out_path = PROJECT_DIR / out_rel
    storyboard_ref = prompt_data.get("storyboard_image")
    other_refs = list(prompt_data.get("reference_images") or [])
    cmd = [
        sys.executable, str(SKILLS_DIR / "sw_generate_frame"),
        "--prompt", prompt_data["prompt"],
        "--out", out_rel,
        "--size", prompt_data.get("size", "landscape_16_9"),
        "--frame-id", frame_id,
    ]
    if storyboard_ref:
        cmd.extend(["--storyboard-image", storyboard_ref])
    if other_refs:
        cmd.extend(["--ref-images", ",".join(other_refs)])
    if _frame_prompt_requires_sensitive_context(prompt_data):
        cmd.append("--sensitive-context")

    result = _stream_subprocess(
        cmd,
        cwd=PROJECT_DIR,
        label=f"frame_{frame_id}",
        env={**os.environ, "PROJECT_DIR": str(PROJECT_DIR), "SKILLS_DIR": str(SKILLS_DIR)},
    )
    stdout_text = result.stdout or ""
    stderr_text = result.stderr or ""

    if result.returncode != 0 and "failure_type: UPSTREAM_TRANSIENT" in stdout_text:
        retry_result = _stream_subprocess(
            cmd,
            cwd=PROJECT_DIR,
            label=f"frame_{frame_id}_retry",
            env={**os.environ, "PROJECT_DIR": str(PROJECT_DIR), "SKILLS_DIR": str(SKILLS_DIR)},
        )
        result = retry_result
        stdout_text = retry_result.stdout or ""
        stderr_text = retry_result.stderr or ""

    return {
        "frame_id": frame_id,
        "out_rel": out_rel,
        "out_path": out_path,
        "storyboard_ref": storyboard_ref,
        "other_refs": other_refs,
        "returncode": result.returncode,
        "stdout": stdout_text,
        "stderr": stderr_text,
    }


_SENSITIVE_FRAME_TOKENS = (
    "vial",
    "dropper",
    "administers",
    "administering",
    "drops into",
    "open wide",
    "mouth open",
    "forced to open",
    "peer pressure",
    "drugged",
    "intimate coercion",
)


def _frame_prompt_requires_sensitive_context(prompt_data: dict) -> bool:
    parts = [
        str(prompt_data.get("prompt", "")),
        str(prompt_data.get("negative_prompt", "")),
        str(prompt_data.get("scene_id", "")),
    ]
    haystack = " ".join(parts).lower()
    return any(token in haystack for token in _SENSITIVE_FRAME_TOKENS)


def _generate_final_frames(dry_run: bool) -> tuple[int, int, int]:
    """Generate final frames with a continuously topped-up worker pool."""
    from graph.prompt_assembler import assemble_image_prompt
    from graph.runtime_state import mark_frame_composed, project_relative_path, save_graph_projection
    from graph.store import GraphStore

    store = GraphStore(str(PROJECT_DIR))
    graph = store.load()

    composed_dir = PROJECT_DIR / "frames" / "composed"
    composed_dir.mkdir(parents=True, exist_ok=True)

    generated = 0
    skipped = 0
    failed = 0
    total = len(graph.frame_order)
    graph_dirty = False

    pending_jobs: list[tuple[int, str, str, dict]] = []

    for i, frame_id in enumerate(graph.frame_order, 1):
        out_rel = f"frames/composed/{frame_id}_gen.png"
        out_path = PROJECT_DIR / out_rel

        if out_path.exists() and out_path.stat().st_size > 1000:
            mark_frame_composed(
                graph,
                frame_id,
                out_rel,
                run_id=PIPELINE_RUN_ID,
                actor="frame_generation",
                phase=os.environ.get(PHASE_ENV, ""),
            )
            skipped += 1
            log(f"  [{i}/{total}] {frame_id}: composed frame exists ({out_path.stat().st_size:,}B) — skipping")
            graph_dirty = True
            continue

        prompt_data = assemble_image_prompt(graph, frame_id, project_dir=PROJECT_DIR)

        if dry_run:
            log(
                f"  [{i}/{total}] [DRY-RUN] Would generate {frame_id} "
                f"{'with storyboard + ' if prompt_data.get('storyboard_image') else 'with '}"
                f"{len(list(prompt_data.get('reference_images') or []))} ref(s)",
                YELLOW,
            )
            generated += 1
            continue

        log(
            f"  [{i}/{total}] {frame_id}: generating final frame "
            f"{'storyboard+' if prompt_data.get('storyboard_image') else ''}"
            f"{len(list(prompt_data.get('reference_images') or []))} refs"
        )
        pending_jobs.append((i, frame_id, out_rel, prompt_data))

    if not dry_run and pending_jobs:
        def _handle_worker_completion(job_idx: int, job_frame_id: str, worker_result: dict | None, exc: Exception | None) -> None:
            nonlocal failed, generated, graph_dirty
            if exc is not None:
                failed += 1
                log_err(f"  [{job_idx}/{total}] {job_frame_id}: final frame generation crashed: {exc}")
                return

            assert worker_result is not None
            worker_out_path = worker_result["out_path"]
            if worker_result["returncode"] == 0 and worker_out_path.exists() and worker_out_path.stat().st_size > 1000:
                storyboard_ref = worker_result["storyboard_ref"]
                other_refs = worker_result["other_refs"]
                normalized_refs = [
                    ref for ref in (
                        [project_relative_path(PROJECT_DIR, storyboard_ref)] if storyboard_ref else []
                    ) + [
                        project_relative_path(PROJECT_DIR, ref) for ref in other_refs
                    ]
                    if ref
                ]
                mark_frame_composed(
                    graph,
                    job_frame_id,
                    worker_result["out_rel"],
                    refs_used=normalized_refs,
                    run_id=PIPELINE_RUN_ID,
                    actor="frame_generation",
                    phase=os.environ.get(PHASE_ENV, ""),
                )
                generated += 1
                graph_dirty = True
                return

            failed += 1
            log_err(f"  [{job_idx}/{total}] {job_frame_id}: final frame generation failed")

        with ThreadPoolExecutor(max_workers=min(FRAME_GEN_CONCURRENCY, len(pending_jobs))) as executor:
            pending_iter = iter(pending_jobs)
            active_futures: dict = {}

            def _submit_next() -> bool:
                try:
                    job_idx, job_frame_id, job_out_rel, job_prompt = next(pending_iter)
                except StopIteration:
                    return False
                future = executor.submit(
                    _run_final_frame_worker,
                    job_frame_id,
                    job_prompt,
                    job_out_rel,
                )
                active_futures[future] = (job_idx, job_frame_id)
                return True

            for _ in range(min(FRAME_GEN_CONCURRENCY, len(pending_jobs))):
                _submit_next()

            while active_futures:
                done, _ = wait(active_futures.keys(), return_when=FIRST_COMPLETED)
                for future in done:
                    job_idx, job_frame_id = active_futures.pop(future)
                    try:
                        worker_result = future.result()
                        _handle_worker_completion(job_idx, job_frame_id, worker_result, None)
                    except Exception as exc:
                        _handle_worker_completion(job_idx, job_frame_id, None, exc)
                    _submit_next()

                if graph_dirty:
                    save_graph_projection(graph, PROJECT_DIR, store=store)
                    graph_dirty = False

    if graph_dirty and not dry_run:
        save_graph_projection(graph, PROJECT_DIR, store=store)

    return generated, skipped, failed


def phase_4_production(dry_run: bool, phase_timers: dict) -> None:
    """Phase 4 -- Generate final frames with a continuous worker pool."""
    log_header("PHASE 4 -- Continuous Final Frame Generation")
    timer = Timer()
    phase_timers["phase_4"] = timer
    base = PROJECT_DIR

    # ── Asset readiness gate — core reference assets must exist ──
    if not dry_run:
        from graph.store import GraphStore
        _store = GraphStore(str(PROJECT_DIR))
        _graph = _store.load()

        if ENABLE_STORYBOARD_GUIDANCE:
            # Storyboard guidance is helpful but not required for final frame generation.
            # Build it when absent so later prompts can use it, but keep Phase 4 runnable
            # even when storyboard generation is unavailable or partially missing.
            if not _graph.storyboard_grids or not _graph.seeded_domains.get("storyboard_grids"):
                log_warn(f"Phase 4: no storyboard grids in graph — rebuilding")
                _generate_storyboard_grids_phase3(dry_run=False)

        audit = _audit_phase4_assets()

        # Regen missing cast/location/prop assets
        if audit["missing_cast"] or audit["missing_locations"] or audit["missing_props"]:
            log_warn(f"Phase 4 asset audit: {audit['total_missing']} missing asset(s)")
            if audit["missing_cast"]:
                log_warn(f"  Cast: {', '.join(audit['missing_cast'])}")
            if audit["missing_locations"]:
                log_warn(f"  Locations: {', '.join(audit['missing_locations'])}")
            if audit["missing_props"]:
                log_warn(f"  Props: {', '.join(audit['missing_props'])}")
            log("Falling back to Phase 3 asset regen for missing items...")
            _programmatic_asset_validation_and_regen(base)

        if audit["missing_storyboards"]:
            log_warn(f"Phase 4: {len(audit['missing_storyboards'])} storyboard grid(s) missing")
            log_warn(f"  Storyboards: {', '.join(audit['missing_storyboards'])}")
            log("Proceeding without those storyboard guidance refs; prompts will fall back to core refs.")

    log(f"Generating final frames with a continuously topped-up pool ({FRAME_GEN_CONCURRENCY} concurrent workers)...")
    generated, skipped, failed = _generate_final_frames(dry_run)
    log_ok(f"Final frame generation done: {generated} generated, {skipped} skipped, {failed} failed")

    if not dry_run:
        log("Refreshing prompt audit logs from graph-backed composed frames...")
        phase4_assemble = _stream_subprocess(
            [sys.executable, str(SKILLS_DIR / "graph_assemble_prompts"),
             "--project-dir", str(PROJECT_DIR)],
            cwd=PROJECT_DIR, label="graph_assemble_prompts_phase4")
        if phase4_assemble.returncode == 0:
            _run_project_report(PROJECT_DIR)
        log_ok("Prompt audit logs refreshed with final composed frames")

    if not dry_run:
        log("Files created by Phase 4:")
        list_dir_files(base / "frames" / "composed")
        created_files = collect_files_in(base / "frames" / "composed")
        save_phase_report(4, timer, "phase_4_sequential_generation", None, created_files)

        if not run_quality_gate(4, base):
            log_warn("Phase 4 quality gate has warnings — proceeding")

    advance_phase(4, 5, dry_run)
    log_ok(f"Phase 4 complete in {timer.elapsed_str()}")


def _generate_video_clip(frame_id: str, image_path: Path, prompt: str,
                         duration: int, out_path: Path,
                         dialogue_text: str,
                         dry_run: bool) -> subprocess.CompletedProcess | None:
    """Generate a single video clip from a composed frame. No agent needed."""
    if out_path.exists() and out_path.stat().st_size > 5000:
        log(f"  {frame_id}: clip exists ({out_path.stat().st_size:,}B) — skipping")
        return None

    cmd = [
        sys.executable, str(SKILLS_DIR / "sw_generate_video"),
        "--prompt", prompt,
        "--image", str(image_path),
        "--out", str(out_path),
        "--frame-id", frame_id,
        "--duration", str(duration),
    ]
    if dialogue_text.strip():
        cmd.extend(["--dialogue-text", dialogue_text.strip()])

    if dry_run:
        log(f"  [DRY-RUN] Would generate clip for {frame_id}", YELLOW)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    retryable_tokens = (
        "502 bad gateway",
        "503 service unavailable",
        "429",
        "upstream_transient",
        "temporarily unavailable",
        "please retry",
        "timeout",
    )
    result: subprocess.CompletedProcess | None = None
    for attempt in range(1, VIDEO_GEN_RETRIES + 1):
        result = _stream_subprocess(
            cmd, cwd=PROJECT_DIR, timeout=None, label=f"video_{frame_id}",
            env={**os.environ, "PROJECT_DIR": str(PROJECT_DIR), "SKILLS_DIR": str(SKILLS_DIR)},
        )
        if result.returncode == 0:
            return result
        combined = f"{result.stdout}\n{result.stderr}".lower()
        retryable = any(token in combined for token in retryable_tokens)
        if attempt >= VIDEO_GEN_RETRIES or not retryable:
            return result
        backoff = min(45, 5 * attempt)
        log_warn(
            f"  {frame_id}: video generation attempt {attempt}/{VIDEO_GEN_RETRIES} failed "
            f"with retryable upstream error — retrying in {backoff}s"
        )
        time.sleep(backoff)

    return result


def phase_5_video(dry_run: bool, phase_timers: dict) -> None:
    """Phase 5 -- Pipelined video generation: Grok vision refine → generate per frame.

    Each frame's video prompt is refined by Grok vision, then immediately
    queued for clip generation — no waiting for all frames to refine first.
    """
    log_header("PHASE 5 -- Video Generation (Pipelined Refine → Generate)")
    timer = Timer()
    phase_timers["phase_5"] = timer
    base = PROJECT_DIR

    refine_enabled = not dry_run and bool(os.getenv("XAI_API_KEY"))
    if not dry_run and not refine_enabled:
        log_warn("XAI_API_KEY not set — skipping Grok vision refinement and using graph prompts as-is")

    from graph.frame_prompt_refiner import refine_video_prompt
    from graph.prompt_assembler import assemble_video_prompt
    from graph.runtime_state import mark_frame_video, project_relative_path, save_graph_projection
    from graph.store import GraphStore

    graph = None
    store = None
    try:
        store = GraphStore(str(base))
        graph = store.load()
        frame_ids = [frame_id for frame_id in graph.frame_order if frame_id in graph.frames]
    except FileNotFoundError:
        manifest = json.loads(MANIFEST_PATH.read_text()) if MANIFEST_PATH.exists() else {}
        frame_ids = [str(frame.get("frameId", "")).strip() for frame in manifest.get("frames", []) if str(frame.get("frameId", "")).strip()]

    if not frame_ids:
        log_warn("No frames available for video generation — nothing to generate")
        advance_phase(5, 6, dry_run)
        return

    clips_dir = base / "video" / "clips"
    video_prompt_dir = base / "video" / "prompts"
    if not dry_run:
        clips_dir.mkdir(parents=True, exist_ok=True)
        video_prompt_dir.mkdir(parents=True, exist_ok=True)

    total = len(frame_ids)
    log(f"Pipelining {total} frames: refine → generate (as each completes)")

    # ── Build frame work list
    frame_items = []
    missing = 0
    for i, fid in enumerate(frame_ids, 1):
        video_prompt_file = video_prompt_dir / f"{fid}_video.json"
        if graph is not None:
            prompt_data = assemble_video_prompt(graph, fid, project_dir=base)
        else:
            if not video_prompt_file.exists():
                raise RuntimeError(
                    f"Missing video prompt JSON for frame {fid}: {video_prompt_file}"
                )
            prompt_data = json.loads(video_prompt_file.read_text(encoding="utf-8"))
        image_rel = prompt_data.get("input_image_path", "") or f"frames/composed/{fid}_gen.png"
        image_path = Path(image_rel)
        if not image_path.is_absolute():
            image_path = base / image_path
        if not image_path.exists():
            log_warn(f"  [{i}/{total}] {fid}: composed frame missing — skipping")
            missing += 1
            continue
        frame_items.append((i, fid, image_path, prompt_data, video_prompt_file))

    # ── Pipelined refine → generate
    import asyncio as _aio
    import threading
    from queue import Queue

    gen_queue: Queue = Queue()
    stats = {"refined": 0, "refine_skipped": 0, "refine_failed": 0,
             "generated": 0, "gen_skipped": 0, "gen_failed": 0}
    stats_lock = threading.Lock()
    video_updates: dict[str, str] = {}
    refine_started_at = time.time()
    gen_started_at = time.time()

    def _refine_and_enqueue(item):
        """Refine one frame's video prompt, then put it on the gen queue."""
        idx, fid, image_path, prompt_data, video_prompt_file = item
        prompt_state = dict(prompt_data)
        prompt_state["refined_by"] = ""

        if dry_run:
            prompt_state["refined_by"] = "skipped:dry_run"
            with stats_lock:
                stats["refine_skipped"] += 1
        elif not refine_enabled:
            prompt_state["refined_by"] = "skipped:no_api_key"
            with stats_lock:
                stats["refine_skipped"] += 1
            log_warn(f"  [{idx}/{total}] {fid}: vision refinement skipped (no_api_key) — using graph prompt")
        else:
            try:
                prompt_state = _aio.run(refine_video_prompt(prompt_state, base))
            except Exception as e:
                prompt_state["refined_by"] = f"failed:{type(e).__name__}"
                log_warn(f"  [{idx}/{total}] {fid}: refinement raised {type(e).__name__} — using graph prompt")

            refined_by = str(prompt_state.get("refined_by", ""))
            status_kind = _refine_status_kind(refined_by)
            with stats_lock:
                if status_kind == "refined":
                    stats["refined"] += 1
                elif status_kind == "skipped":
                    stats["refine_skipped"] += 1
                else:
                    stats["refine_failed"] += 1

            if status_kind == "skipped":
                reason = refined_by.split(":", 1)[1] if ":" in refined_by else refined_by or "unknown"
                log_warn(f"  [{idx}/{total}] {fid}: vision refinement skipped ({reason}) — using graph prompt")
            elif status_kind == "failed":
                reason = refined_by.split(":", 1)[1] if ":" in refined_by else refined_by or "unknown"
                log_warn(f"  [{idx}/{total}] {fid}: vision refinement failed ({reason}) — using graph prompt")

        if not dry_run:
            video_prompt_file.write_text(
                json.dumps(prompt_state, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        projected_payload = build_video_request_projection(prompt_state)
        video_prompt = projected_payload.get("motion_prompt", "") or prompt_state.get("prompt", "")
        dialogue_text = projected_payload.get("dialogue_text", "")
        dur = max(2, min(15, int(prompt_state.get("duration", 5))))
        if prompt_state.get("dialogue_fit_status") == "capped_to_model_max":
            log_warn(f"  [{idx}/{total}] {fid}: dialogue wants "
                     f"{prompt_state.get('recommended_duration', dur)}s, capped to 15s")

        out_path = clips_dir / f"{fid}.mp4"
        gen_queue.put((idx, fid, image_path, video_prompt, dialogue_text, dur, out_path))

    def _gen_from_queue():
        """Pull refined frames from queue and generate clips."""
        while True:
            item = gen_queue.get()
            if item is None:  # poison pill
                gen_queue.task_done()
                break
            idx, fid, image_path, video_prompt, dialogue_text, dur, out_path = item
            log(f"  [{idx}/{total}] {fid} ({dur}s) → generating clip")
            result = _generate_video_clip(fid, image_path, video_prompt, dur, out_path, dialogue_text, dry_run)
            done = 0
            generated_ok = 0
            generated_skipped = 0
            generated_failed = 0
            status = "complete"
            color = CYAN
            with stats_lock:
                if result is None:
                    stats["gen_skipped"] += 1
                    if out_path.exists():
                        video_updates[fid] = project_relative_path(base, out_path) or str(out_path)
                    status = "skipped"
                    color = YELLOW
                elif result.returncode == 0:
                    stats["generated"] += 1
                    if out_path.exists():
                        video_updates[fid] = project_relative_path(base, out_path) or str(out_path)
                else:
                    stats["gen_failed"] += 1
                    status = f"failed (exit={result.returncode})"
                    color = RED
                    log_err(f"  {fid} clip generation failed (exit={result.returncode})")
                done = stats["generated"] + stats["gen_skipped"] + stats["gen_failed"]
                generated_ok = stats["generated"]
                generated_skipped = stats["gen_skipped"]
                generated_failed = stats["gen_failed"]
            eta_suffix = _progress_eta_suffix(
                started_at=gen_started_at,
                completed=done,
                total=len(frame_items),
            )
            live_log(
                f"  [Video {done}/{len(frame_items)}] {fid} {status} "
                f"({generated_ok} generated, {generated_skipped} skipped, "
                f"{generated_failed} failed{eta_suffix})",
                color=color,
            )
            gen_queue.task_done()

    # Start generator workers — they consume from queue as items arrive
    gen_workers = []
    for _ in range(VIDEO_GEN_CONCURRENCY):
        t = threading.Thread(target=_gen_from_queue, daemon=True)
        t.start()
        gen_workers.append(t)

    # Refine frames with concurrency — each pushes to gen_queue when done
    with ThreadPoolExecutor(max_workers=VIDEO_REFINE_CONCURRENCY) as refine_pool:
        refine_futures = [refine_pool.submit(_refine_and_enqueue, item)
                          for item in frame_items]
        for future in as_completed(refine_futures):
            try:
                future.result()
            except Exception as e:
                log_err(f"  Refine worker error: {e}")
            done = 0
            refined_ok = 0
            refined_skipped = 0
            refined_failed = 0
            with stats_lock:
                done = stats["refined"] + stats["refine_skipped"] + stats["refine_failed"]
                refined_ok = stats["refined"]
                refined_skipped = stats["refine_skipped"]
                refined_failed = stats["refine_failed"]
            eta_suffix = _progress_eta_suffix(
                started_at=refine_started_at,
                completed=done,
                total=len(frame_items),
            )
            live_log(
                f"  [Refine {done}/{len(frame_items)}] "
                f"({refined_ok} refined, {refined_skipped} skipped, "
                f"{refined_failed} failed{eta_suffix})"
            )

    # Wait for all generation to finish
    gen_queue.join()

    # Send poison pills to stop gen workers
    for _ in gen_workers:
        gen_queue.put(None)
    for t in gen_workers:
        t.join()

    log_ok(f"Refine: {stats['refined']} refined, {stats['refine_skipped']} skipped, "
           f"{stats['refine_failed']} failed")
    log_ok(f"Generate: {stats['generated']} generated, {stats['gen_skipped']} skipped, "
           f"{stats['gen_failed']} failed")

    if not dry_run and graph is not None and store is not None and video_updates:
        for fid, rel_path in video_updates.items():
            mark_frame_video(
                graph,
                fid,
                rel_path,
                run_id=PIPELINE_RUN_ID,
                actor="video_generation",
                phase=os.environ.get(PHASE_ENV, ""),
            )
        save_graph_projection(graph, base, store=store)

    if not dry_run:
        clips = list(clips_dir.glob("*.mp4"))
        log_ok(f"{len(clips)} video clip(s) in video/clips/")
        log("Clips created by Phase 5:")
        list_dir_files(clips_dir)
        created_files = collect_files_in(clips_dir)
        save_phase_report(5, timer, "phase_5_programmatic", None, created_files)

        if not run_quality_gate(5, base):
            log_warn("Phase 5 quality gate has warnings — proceeding")

    advance_phase(5, 6, dry_run)
    log_ok(f"Phase 5 complete in {timer.elapsed_str()}")


# ---------------------------------------------------------------------------
# Phase 6 -- Programmatic Export (ffmpeg)
# ---------------------------------------------------------------------------

def _run_cmd(cmd: list[str], step_label: str, cwd: str | Path = PROJECT_DIR,
             dry_run: bool = False) -> subprocess.CompletedProcess:
    """Run a shell command, printing it first. Fail on non-zero exit."""
    cmd_str = " ".join(str(c) for c in cmd)
    if dry_run:
        log(f"[DRY-RUN] {step_label}: {cmd_str}", YELLOW)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    log(f"  $ {cmd_str}", DIM)
    try:
        result = _stream_subprocess(cmd, cwd=cwd, timeout=3600, label=step_label)
    except subprocess.TimeoutExpired:
        log_err(f"{step_label} timed out after 3600s — continuing")
        return subprocess.CompletedProcess(cmd, returncode=-1, stdout="", stderr="TIMEOUT")
    if result.returncode != 0:
        log_err(f"{step_label} failed (exit={result.returncode})")
        log_warn(f"Export step '{step_label}' failed — continuing pipeline")
    return result


def phase_6_export(dry_run: bool, phase_timers: dict) -> str:
    """
    Phase 6 -- Deterministic export via ffmpeg.

    Steps:
      1. Build ordered clip list from manifest.frames[]
      2. Normalize every clip (libx264 / aac / 1280x720 / 24fps / 48kHz)
      3. Write concat_list.txt
      4. ffmpeg concat -c copy  ->  project_<slug>_draft.mp4
      5. ffmpeg loudnorm        ->  project_<slug>_final.mp4
      6. ffprobe verification
      7. Manifest close
    """
    log_header("PHASE 6 -- Export (ffmpeg)")
    timer = Timer()
    phase_timers["phase_6"] = timer

    base = PROJECT_DIR

    # Re-read manifest fresh at the start of phase 6
    manifest = read_manifest()
    slug = manifest.get("slug", "project")

    clips_dir      = base / "video" / "clips"
    normalized_dir = base / "video" / "clips" / "normalized"
    assembled_dir  = base / "video" / "assembled"
    export_dir     = base / "video" / "export"

    for d in (normalized_dir, assembled_dir, export_dir):
        d.mkdir(parents=True, exist_ok=True)

    draft_path  = assembled_dir / f"project_{slug}_draft.mp4"
    final_path  = export_dir   / f"project_{slug}_final.mp4"
    concat_list = assembled_dir / "concat_list.txt"

    # ------------------------------------------------------------------
    # Step 1 -- Build ordered clip list
    # ------------------------------------------------------------------
    log("Step 1 -- Building ordered clip list from manifest ...")

    # Re-read manifest fresh for frames
    manifest = read_manifest()
    frames = manifest.get("frames", [])
    if not frames and not dry_run:
        log_warn("manifest.frames[] is empty -- nothing to export, attempting fallback.")

    def frame_sort_key(f: dict) -> float:
        return float(f.get("sequenceIndex", 0))

    sorted_frames = sorted(frames, key=frame_sort_key)

    ordered_clips: list[Path] = []
    for frame in sorted_frames:
        seq = frame.get("sequenceIndex", "")
        fid = frame.get("frameId", "")
        # Try chunk pattern first (multi-chunk clips)
        chunk_pattern = list(clips_dir.glob(f"{seq}_{fid}_c*.mp4"))
        if chunk_pattern:
            ordered_clips.extend(sorted(chunk_pattern))
        elif seq and (clips_dir / f"{seq}_{fid}.mp4").exists():
            ordered_clips.append(clips_dir / f"{seq}_{fid}.mp4")
        elif (clips_dir / f"{fid}.mp4").exists():
            ordered_clips.append(clips_dir / f"{fid}.mp4")
        else:
            # Glob for any file containing the frame id
            fallback = sorted(clips_dir.glob(f"*{fid}*.mp4"))
            if fallback:
                ordered_clips.extend(fallback)
            else:
                log_warn(f"  No clip found for frame {fid} — skipping")

    if not ordered_clips and not dry_run:
        ordered_clips = sorted(clips_dir.glob("*.mp4"))
        if not ordered_clips:
            log_warn("No clips found in video/clips/ — export will be skipped")
        log_warn(f"manifest.frames[] had no clip refs -- using {len(ordered_clips)} "
                 f"clips found in video/clips/ (alphabetical order).")

    log_ok(f"Step 1 done -- {len(ordered_clips)} clip(s) in timeline order")

    # ------------------------------------------------------------------
    # Step 2 -- Normalize clips
    # ------------------------------------------------------------------
    log(f"Step 2 -- Normalizing {len(ordered_clips)} clip(s) ...")
    normalized_clips: list[Path] = []

    for clip in ordered_clips:
        out_name = clip.name
        out_path = normalized_dir / out_name
        normalized_clips.append(out_path)

        if out_path.exists():
            log(f"  (skip -- already normalized: {clip.name})", DIM)
            continue

        # Probe whether clip has an audio stream
        has_audio = False
        if not dry_run:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "a",
                 "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                 str(clip)],
                capture_output=True, text=True)
            has_audio = "audio" in probe.stdout

        if has_audio or dry_run:
            # Normal normalize -- clip already has audio
            _run_cmd([
                "ffmpeg", "-y",
                "-i", str(clip),
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-r", "24", "-video_track_timescale", "24000",
                "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,"
                       "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1",
                "-c:a", "aac", "-ar", "48000", "-ac", "2",
                "-movflags", "+faststart",
                str(out_path),
            ], step_label=f"normalize {clip.name}", dry_run=dry_run)
        else:
            # Clip has NO audio -- generate silent audio track to match video duration
            log(f"  (no audio in {clip.name} -- adding silent track)", YELLOW)
            _run_cmd([
                "ffmpeg", "-y",
                "-i", str(clip),
                "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-r", "24", "-video_track_timescale", "24000",
                "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,"
                       "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1",
                "-c:a", "aac", "-ar", "48000", "-ac", "2",
                "-shortest",
                "-movflags", "+faststart",
                str(out_path),
            ], step_label=f"normalize+silence {clip.name}", dry_run=dry_run)

    # Verify normalization actually produced files
    if not dry_run:
        existing_normalized = [p for p in normalized_clips if p.exists()]
        if not existing_normalized:
            log_err(f"Step 2 FAILED -- 0 of {len(normalized_clips)} clips were normalized")
            log_warn("Cannot proceed with concat -- no normalized clips exist")
            save_phase_report(6, timer, "ffmpeg_export", None, [])
            log_ok(f"Phase 6 complete (failed) in {timer.elapsed_str()}")
            return ""
        if len(existing_normalized) < len(normalized_clips):
            log_warn(f"Step 2 partial -- {len(existing_normalized)} of {len(normalized_clips)} clips normalized")
        normalized_clips = existing_normalized

    log_ok(f"Step 2 done -- {len(normalized_clips)} clip(s) normalized")

    # ------------------------------------------------------------------
    # Step 3 -- Write concat_list.txt (use absolute paths to avoid relative_to crashes)
    # ------------------------------------------------------------------
    log("Step 3 -- Writing concat_list.txt ...")
    if not dry_run:
        lines = [
            f"file '{str(p.resolve())}'\n"
            for p in normalized_clips
        ]
        concat_list.write_text("".join(lines))
        log_ok(f"Step 3 done -- {concat_list} ({len(lines)} entries)")
    else:
        log(f"[DRY-RUN] Would write {concat_list} with {len(ordered_clips)} entries",
            YELLOW)

    # ------------------------------------------------------------------
    # Step 4 -- Stitch with -c copy
    # ------------------------------------------------------------------
    log("Step 4 -- Stitching clips via ffmpeg concat ...")
    _run_cmd([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        "-movflags", "+faststart",
        str(draft_path),
    ], step_label="concat stitch", dry_run=dry_run)
    log_ok(f"Step 4 done -> {draft_path.name}")

    # ------------------------------------------------------------------
    # Step 5 -- Audio loudnorm
    # ------------------------------------------------------------------
    log("Step 5 -- Normalizing audio to -16 LUFS ...")
    _run_cmd([
        "ffmpeg", "-y",
        "-i", str(draft_path),
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:v", "copy",
        "-movflags", "+faststart",
        str(final_path),
    ], step_label="loudnorm", dry_run=dry_run)
    log_ok(f"Step 5 done -> {final_path.name}")

    # ------------------------------------------------------------------
    # Step 6 -- ffprobe verification
    # ------------------------------------------------------------------
    log("Step 6 -- Verifying export with ffprobe ...")
    if not dry_run:
        probe_result = _run_cmd([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration,size,bit_rate",
            "-show_entries", "stream=codec_name,width,height,r_frame_rate,sample_rate",
            "-of", "json",
            str(final_path),
        ], step_label="ffprobe verify")

        probe_data = json.loads(probe_result.stdout)
        fmt = probe_data.get("format", {})
        streams = probe_data.get("streams", [])

        if not final_path.exists():
            log_warn("Export file does not exist after ffprobe — export may have failed")
            file_size = 0
        else:
            file_size = final_path.stat().st_size
        if file_size == 0:
            log_warn("Export file is 0 bytes — export may have failed silently")

        duration  = float(fmt.get("duration", 0))

        video_streams = [s for s in streams if s.get("codec_name") in
                         ("h264", "hevc", "vp9", "av1")]
        audio_streams = [s for s in streams if s.get("codec_name") in
                         ("aac", "mp3", "opus", "flac")]

        if not video_streams:
            log_warn("No recognized video stream found in export.")
        if not audio_streams:
            log_warn("No recognized audio stream found in export.")

        vs = video_streams[0] if video_streams else {}
        as_ = audio_streams[0] if audio_streams else {}
        res = f"{vs.get('width', '?')}x{vs.get('height', '?')}"
        codec_str = f"{vs.get('codec_name','?')}/{as_.get('codec_name','?')}"

        log_ok(
            f"Export verified -- duration={duration:.1f}s  "
            f"size={file_size // (1024*1024)}MB  "
            f"codec={codec_str}  res={res}"
        )

        # ------------------------------------------------------------------
        # Step 7 -- Manifest close (re-reads manifest fresh)
        # ------------------------------------------------------------------
        log("Step 7 -- Closing manifest ...")
        rel_export = str(Path("video") / "export" / final_path.name)
        mark_project_complete(
            export_path=rel_export,
            export_duration=duration,
            export_size_bytes=file_size,
            export_codec=codec_str,
            export_resolution=res,
        )
        log_ok("Manifest closed -- project complete")

        # Save phase report
        created_files = collect_files_in(export_dir) + collect_files_in(assembled_dir)
        save_phase_report(6, timer, "ffmpeg_export", None, created_files)

        log_ok(f"Phase 6 complete in {timer.elapsed_str()}")
        return str(final_path)

    else:
        log("[DRY-RUN] Would run ffprobe and close manifest", YELLOW)
        return str(final_path)


# ---------------------------------------------------------------------------
# Summary report
# ---------------------------------------------------------------------------

def print_summary(pipeline_timer: Timer, phase_timers: dict,
                  export_path: str | None) -> None:
    log_header("PIPELINE SUMMARY")
    print(f"  Total duration:  {pipeline_timer.elapsed_str()}", flush=True)
    print(f"\n  Per-phase breakdown:", flush=True)
    for phase, t in phase_timers.items():
        print(f"    {phase:<10}  {t.elapsed_str()}", flush=True)

    if export_path:
        p = Path(export_path)
        if p.exists():
            size_mb = p.stat().st_size // (1024 * 1024)
            print(f"\n  Export: {export_path}  ({size_mb}MB)", flush=True)
        else:
            print(f"\n  Export (dry-run): {export_path}", flush=True)

    # Files-created count per phase dir
    base = PROJECT_DIR
    phase_dirs = {
        "phase_1 (narrative)": base / "creative_output",
        "phase_2 (cast)": base / "cast",
        "phase_2 (locs)": base / "locations",
        "phase_2 (props)": base / "props",
        "phase_3 (frames)": base / "frames",
        "phase_5 (clips)": base / "video" / "clips",
        "phase_6 (export)": base / "video" / "export",
    }
    print(f"\n  Files created per output area:", flush=True)
    for label, d in phase_dirs.items():
        if d.exists():
            count = sum(1 for _ in d.rglob("*") if _.is_file())
            print(f"    {label:<22}  {count} file(s)", flush=True)

    print(f"\n{BOLD}{GREEN}Pipeline complete.{RESET}\n", flush=True)


# ---------------------------------------------------------------------------
# Argument parsing & entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ScreenWire AI -- Headless Pipeline Runner"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the execution plan without running agents or ffmpeg commands.",
    )
    parser.add_argument(
        "--phase",
        type=int,
        choices=[0, 1, 2, 3, 4, 5, 6],
        default=None,
        help="Run only a specific phase (0-6) for debugging.",
    )
    parser.add_argument(
        "--project",
        type=str,
        required=True,
        help="Project directory name under projects/ (e.g., orchids_gambit_001)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override default model for all agents (e.g., grok-4-1-fast-reasoning)",
    )
    parser.add_argument(
        "--parallel-phase4",
        type=int,
        default=0,
        metavar="N",
        help="Run Phase 4 with N parallel Opus workers (e.g., --parallel-phase4 8). "
             "0 = sequential (default).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Auto-detect last completed phase from manifest and continue from the next one.",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="After Phase 2 continuity validation, spawn the graph auditor agent for optional QA.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Stream live progress updates for long-running batch phases.",
    )
    return parser.parse_args()


def main() -> None:
    global PROJECT_DIR, MANIFEST_PATH, LOGS_DIR, PIPELINE_LOGS_DIR, DEFAULT_MODEL, AUDIT_PHASE2, PIPELINE_RUN_ID, LIVE_MODE
    args = parse_args()
    dry_run   = args.dry_run
    only_phase = args.phase
    parallel_p4 = args.parallel_phase4
    AUDIT_PHASE2 = args.audit
    LIVE_MODE = bool(args.live)

    # Override model if specified
    if args.model:
        DEFAULT_MODEL = args.model
        log(f"Model override: {DEFAULT_MODEL}")

    # Resolve project directory
    PROJECT_DIR = PROJECTS_DIR / args.project
    if not PROJECT_DIR.exists():
        fail(f"Project not found: {PROJECT_DIR}\n"
             f"Create one with: python3 create_project.py --name 'My Story' --id {args.project}")
    MANIFEST_PATH = PROJECT_DIR / "project_manifest.json"
    LOGS_DIR = PROJECT_DIR / "logs"
    PIPELINE_LOGS_DIR = LOGS_DIR / "pipeline"
    log(f"Using project: {PROJECT_DIR}")

    PIPELINE_RUN_ID = os.environ.get(RUN_ID_ENV) or generate_run_id()
    os.environ[RUN_ID_ENV] = PIPELINE_RUN_ID
    os.environ[PHASE_ENV] = "pipeline_boot"
    os.environ[LIVE_ENV] = "1" if LIVE_MODE else "0"
    log(f"Run ID: {PIPELINE_RUN_ID}")
    if LIVE_MODE:
        log("Live mode enabled — long-running batch phases will stream progress", CYAN)

    if dry_run:
        log_warn("DRY-RUN MODE -- no agents will be spawned, no files modified.")

    # Deploy shared conventions as CLAUDE.md into project dir (prompt caching)
    _deploy_shared_conventions(PROJECT_DIR)
    _deploy_project_reporting_assets(PROJECT_DIR)

    # Ensure log directories exist
    PIPELINE_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    emit_event(
        PROJECT_DIR,
        event="pipeline_started",
        run_id=PIPELINE_RUN_ID,
        phase="pipeline_boot",
        details={"dry_run": dry_run, "project_dir": str(PROJECT_DIR)},
    )

    pipeline_timer = Timer()
    phase_timers: dict[str, Timer] = {}

    # Start and verify server (needed even for single-phase runs -- skills call it)
    start_server(dry_run)
    wait_for_server(dry_run)

    export_path: str | None = None
    pipeline_failed = False

    # Determine starting phase
    start_phase = 0
    if args.resume:
        start_phase = detect_resume_phase()
        if start_phase >= 7:
            log_ok("All phases already complete — nothing to resume.")
            stop_server()
            return
        log(f"Resuming from phase {start_phase} ({PHASE_NAMES.get(start_phase, '?')})")

    def _run_phase(n: int) -> str | None:
        """Run phase n. Returns export path for phase 6, else None."""
        phase_label = _phase_label(n)
        os.environ[PHASE_ENV] = phase_label
        emit_event(
            PROJECT_DIR,
            event="phase_started",
            run_id=PIPELINE_RUN_ID,
            phase=phase_label,
            details={"phase_number": n, "phase_name": PHASE_NAMES.get(n, str(n))},
        )
        try:
            if only_phase is None and n in (1, 2):
                reusable, issues = _phase_reuse_status(n, PROJECT_DIR)
                if reusable:
                    log_ok(f"Skipping phase {n} — existing outputs are intact and reusable")
                    emit_event(
                        PROJECT_DIR,
                        event="phase_skipped",
                        run_id=PIPELINE_RUN_ID,
                        phase=phase_label,
                        details={
                            "phase_number": n,
                            "phase_name": PHASE_NAMES.get(n, str(n)),
                            "reason": "existing_outputs_reusable",
                        },
                    )
                    return None
                log_warn(
                    f"Re-running phase {n} to heal incomplete artifacts: "
                    + "; ".join(issues[:3])
                )
            match n:
                case 0: phase_0_verify(dry_run)
                case 1: phase_1_narrative(dry_run, phase_timers)
                case 2: phase_2_morpheus(dry_run, phase_timers)
                case 3: phase_3_assets(dry_run, phase_timers)
                case 4: phase_4_production(dry_run, phase_timers)
                case 5: phase_5_video(dry_run, phase_timers)
                case 6:
                    result = phase_6_export(dry_run, phase_timers)
                    emit_event(
                        PROJECT_DIR,
                        event="phase_completed",
                        run_id=PIPELINE_RUN_ID,
                        phase=phase_label,
                        details={"phase_number": n, "phase_name": PHASE_NAMES.get(n, str(n))},
                    )
                    return result
        except Exception as exc:
            emit_event(
                PROJECT_DIR,
                event="phase_failed",
                level="ERROR",
                run_id=PIPELINE_RUN_ID,
                phase=phase_label,
                details={
                    "phase_number": n,
                    "phase_name": PHASE_NAMES.get(n, str(n)),
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            raise

        emit_event(
            PROJECT_DIR,
            event="phase_completed",
            run_id=PIPELINE_RUN_ID,
            phase=phase_label,
            details={"phase_number": n, "phase_name": PHASE_NAMES.get(n, str(n))},
        )
        return None

    try:
        if only_phase is not None:
            # Single-phase mode: verify prerequisites first
            log(f"Running single phase: {only_phase}")
            if not dry_run:
                verify_prerequisites(only_phase)
            export_path = _run_phase(only_phase)
        else:
            # Full or resumed pipeline — run from start_phase through 6
            for phase_num in range(start_phase, 7):
                export_path = _run_phase(phase_num)
    except Exception:
        pipeline_failed = True
        emit_event(
            PROJECT_DIR,
            event="pipeline_failed",
            level="ERROR",
            run_id=PIPELINE_RUN_ID,
            phase=os.environ.get(PHASE_ENV, ""),
        )
        raise

    finally:
        stop_server()

    emit_event(
        PROJECT_DIR,
        event="pipeline_completed",
        level="ERROR" if pipeline_failed else "INFO",
        run_id=PIPELINE_RUN_ID,
        phase="pipeline_complete",
        details={"export_path": export_path},
    )
    print_summary(pipeline_timer, phase_timers, export_path)


if __name__ == "__main__":
    main()
