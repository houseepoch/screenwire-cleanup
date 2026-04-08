#!/usr/bin/env python3
"""ScreenWire AI — Headless Pipeline Runner (MVP Test Harness)

Drives the full ScreenWire AI pipeline from Phase 0 -> Phase 6 without
human input. Starts the FastAPI server, polls /health until ready, spawns
Claude CLI agents for each phase sequentially, then runs Phase 6 export
programmatically via ffmpeg.

Usage:
    python3 run_pipeline.py [--dry-run] [--phase N]
"""

import argparse
import atexit
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

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

DEFAULT_MODEL = "claude-opus-4-6"

# Resolve claude CLI path (Windows subprocess.run can't find bare "claude")
import shutil as _shutil
CLAUDE_CLI = _shutil.which("claude") or "claude"

LOGS_DIR: Path | None = None          # Set in main() after --project
PIPELINE_LOGS_DIR: Path | None = None  # Set in main() after --project

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


# ---------------------------------------------------------------------------
# Streaming subprocess helper
# ---------------------------------------------------------------------------

def _stream_subprocess(cmd, cwd=None, env=None, timeout=None, label="process"):
    """Run a subprocess, streaming stdout/stderr in real-time while capturing them.

    Uses os.read() on non-blocking file descriptors instead of readline()
    so that output appears immediately even when the child process doesn't
    emit newlines (e.g. claude --print buffering).

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
                    for line in text.splitlines(keepends=True):
                        print(f"{DIM}[{label}]{RESET} {line}", end="", flush=True)
                else:
                    stderr_parts.append(chunk)
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

    env = {**os.environ, "PROJECT_DIR": str(PROJECT_DIR)}
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
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))


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
    timeout: int | None = None,
) -> subprocess.CompletedProcess:
    """Spawn a Claude CLI agent and wait for it to finish.

    Args:
        prompt_prefix: Optional text sent as part of the user message (not the
                       system prompt) so that the base system prompt remains
                       identical across parallel workers, enabling API-level
                       prompt caching.
    """
    if project_dir is None:
        project_dir = PROJECT_DIR

    prompt_path = Path(prompt_file)
    if not prompt_path.exists():
        fail(f"Prompt file not found: {prompt_file}")

    system_prompt = prompt_path.read_text()

    # Expand {{include:path}} markers — reference files resolved relative to prompt dir
    system_prompt = _expand_includes(system_prompt, prompt_path.parent)

    # prompt_prefix is APPENDED to the system prompt (not prepended).
    # This keeps the large shared base prompt as the cacheable prefix — the
    # Anthropic API caches matching prefixes (~1024+ token threshold), so all
    # parallel workers sharing the same base prompt pay full input cost only once.
    # The short per-worker override at the end doesn't break prefix caching and
    # retains system-prompt authority (stronger than user message overrides).
    if prompt_prefix:
        system_prompt = system_prompt + "\n\n---\n\n" + prompt_prefix

    env = {**os.environ, "PROJECT_DIR": str(project_dir), "SKILLS_DIR": str(SKILLS_DIR)}
    # Remove CLAUDECODE env var to prevent nested-session detection
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
        CLAUDE_CLI,
        "--print",
        "-p", trigger_msg,
        "--system-prompt-file", prompt_tmp_path,
        "--dangerously-skip-permissions",
        "--output-format", "text",
        "--model", model,
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
        )
    finally:
        # Clean up temp prompt file
        try:
            os.unlink(prompt_tmp_path)
        except OSError:
            pass
    return result


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
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))
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


def quality_gate_phase_1(base: Path) -> list[str]:
    """Validate Phase 1 outputs. Returns list of failure reasons (empty = pass)."""
    issues = []
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
        if len(drafts) < 2:
            issues.append(f"Only {len(drafts)} scene draft(s) in creative_output/scenes/ — expected at least 2")
    else:
        issues.append("creative_output/scenes/ directory missing — no scene drafts found")
    return issues


def quality_gate_phase_2(base: Path) -> list[str]:
    """Validate Phase 2 outputs. Returns list of failure reasons."""
    issues = []

    # Manifest frame count
    manifest = json.loads((base / "project_manifest.json").read_text())
    frames = manifest.get("frames", [])
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
    if len(cast_jsons) < 2:
        issues.append(f"Only {len(cast_jsons)} cast profile(s) — expected at least 2")

    loc_jsons = list((base / "locations").glob("*.json"))
    if len(loc_jsons) < 1:
        issues.append(f"No location profiles found")

    return issues


def quality_gate_phase_3(base: Path) -> list[str]:
    """Validate Phase 3 outputs (reference and environment images)."""
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
    """Validate Phase 4 outputs (composed frames). No TTS checks."""
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

    return issues


def quality_gate_phase_5(base: Path) -> list[str]:
    """Validate Phase 5 outputs (video clips)."""
    issues = []

    manifest = json.loads((base / "project_manifest.json").read_text())
    frames = manifest.get("frames", [])

    clips_dir = base / "video" / "clips"
    clips = list(clips_dir.glob("*.mp4")) if clips_dir.exists() else []

    if len(clips) < len(frames) * 0.8:
        issues.append(f"Only {len(clips)}/{len(frames)} video clips generated")

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

    log_ok(f"All prerequisites for phase {target_phase} verified (phases 0-{target_phase - 1} complete)")


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
                     "source_files", "assets", "frames", "audio", "video", "logs"]
    for d in required_dirs:
        dp = PROJECT_DIR / d
        if not dp.exists():
            log_warn(f"Expected directory missing: {d}/ -- scaffold may be incomplete")
        else:
            log_ok(f"Directory exists: {d}/")

    return manifest


def phase_1_narrative(dry_run: bool, phase_timers: dict) -> None:
    """Phase 1 -- Creative Coordinator writes skeleton (contracts), then
    parallel Haiku workers write prose per scene, then CC assembles."""
    log_header("PHASE 1 -- Narrative (Contracts + Parallel Prose)")
    timer = Timer()
    phase_timers["phase_1"] = timer

    prompt_file = str(PROMPTS_DIR / "creative_coordinator.md")

    # Step 1: CC writes skeleton only (the contracts)
    log("--- Phase 1a: Skeleton (contracts) ---")
    cc_skeleton_prefix = (
        "CRITICAL OVERRIDE — THIS SUPERSEDES ALL INSTRUCTIONS ABOVE.\n"
        "Complete ONLY the skeleton phase (Phase 1: ARCHITECT). "
        "Write creative_output/outline_skeleton.md with the full story foundation, "
        "character roster, location roster, per-scene construction specs, and "
        "continuity chain. Do NOT write scene prose. Do NOT write creative_output.md. "
        "Do NOT proceed to Phase 2 or Phase 3. Skeleton ONLY. "
        "Stop after the skeleton is complete and update your state."
    )
    result_skeleton = run_agent("creative_coordinator", prompt_file,
                                dry_run=dry_run, prompt_prefix=cc_skeleton_prefix)
    check_agent_result("creative_coordinator_skeleton", result_skeleton, timer)

    # Step 2: Parallel Haiku workers write prose per scene
    log("--- Phase 1b: Parallel prose writing (Haiku per scene) ---")
    if not dry_run:
        base = PROJECT_DIR
        skeleton_path = base / "creative_output" / "outline_skeleton.md"
        if not skeleton_path.exists():
            log_err("Skeleton not found — cannot dispatch parallel prose workers")
        else:
            # Count scenes from skeleton
            skeleton_text = skeleton_path.read_text(encoding="utf-8")
            # Heuristic: count "SCENE X" or "Scene X" headers
            import re as _re
            scene_headers = _re.findall(r'(?:SCENE|Scene)\s+(\d+)', skeleton_text)
            scene_count = max(len(set(scene_headers)), 1)
            log(f"Detected {scene_count} scene(s) in skeleton — spawning Haiku workers")

            haiku_model = "claude-haiku-4-5-20251001"

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
                    model=haiku_model,
                    timeout=300,  # 5 min per scene — Haiku should finish well under this
                )
                return scene_num, result, worker_timer

            # Spawn all scene workers in parallel
            prose_results = {}
            with ThreadPoolExecutor(max_workers=min(scene_count, 10)) as executor:
                futures = {
                    executor.submit(spawn_prose_worker, i): i
                    for i in range(1, scene_count + 1)
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

            ok_count = sum(1 for r, _ in prose_results.values() if r.returncode == 0)
            log(f"Parallel prose: {ok_count}/{scene_count} workers succeeded")
    else:
        log("[DRY-RUN] Would spawn parallel Haiku prose workers per scene", YELLOW)

    # Step 3: CC assembles all scene drafts into creative_output.md
    # Guard: skip if a runaway prose worker already produced the assembly
    co_path = PROJECT_DIR / "creative_output" / "creative_output.md"
    result_assembly = None
    cc_assembly_prefix = (
        "CRITICAL OVERRIDE — THIS SUPERSEDES ALL INSTRUCTIONS ABOVE.\n"
        "Complete ONLY the assembly phase (Phase 3: ASSEMBLY). "
        "All scene drafts have been written by parallel workers to "
        "creative_output/scenes/scene_*_draft.md. Read all scene drafts in sequence. "
        "Run continuity check, smooth transitions, verify beat coverage, "
        "and write the final creative_output/creative_output.md. "
        "Do NOT write the skeleton. Do NOT regenerate scene prose that is already good. "
        "Do NOT run Phase 1 or Phase 2. Assembly ONLY."
    )
    if not dry_run and co_path.exists() and co_path.stat().st_size > 1000:
        log_warn("creative_output.md already exists (likely written by a prose worker "
                 "that ran the full CC pipeline). Skipping assembly spawn.")
        log(f"  Existing file: {co_path.stat().st_size:,} bytes")
    else:
        log("--- Phase 1c: Assembly ---")
        result_assembly = run_agent("creative_coordinator", prompt_file,
                                    dry_run=dry_run, prompt_prefix=cc_assembly_prefix)
        check_agent_result("creative_coordinator_assembly", result_assembly, timer)

    created_files = []
    if not dry_run:
        base = PROJECT_DIR
        verify_files("1", [
            base / "creative_output" / "outline_skeleton.md",
            base / "creative_output" / "creative_output.md",
        ])
        log("Files created by Phase 1:")
        list_dir_files(base / "creative_output")
        created_files = collect_files_in(base / "creative_output")
        save_phase_report(1, timer, "creative_coordinator", result_assembly, created_files)

        # Quality gate
        if not run_quality_gate(1, base):
            log_warn("Phase 1 quality gate FAILED — re-running assembly (attempt 2/2)...")
            timer2 = Timer()
            result2 = run_agent("creative_coordinator", prompt_file,
                                dry_run=dry_run, prompt_prefix=cc_assembly_prefix)
            check_agent_result("creative_coordinator", result2, timer2)
            if not run_quality_gate(1, base):
                log_warn("Phase 1 quality gate still failing after retry — proceeding with warnings")

    advance_phase(1, 2, dry_run)
    log_ok(f"Phase 1 complete in {timer.elapsed_str()}")


def phase_2_morpheus(dry_run: bool, phase_timers: dict) -> None:
    """Phase 2 -- Morpheus builds narrative graph, assembles prompts, materializes flat files."""
    log_header("PHASE 2 -- Morpheus (Graph Build + Prompt Assembly)")
    timer = Timer()
    phase_timers["phase_2"] = timer

    # Initialize graph before spawning Morpheus
    if not dry_run:
        manifest = read_manifest()
        project_id = manifest.get("projectId", manifest.get("project", {}).get("id", "unknown"))
        graph_path = PROJECT_DIR / "graph" / "narrative_graph.json"
        if not graph_path.exists():
            log("Initializing narrative graph...")
            _stream_subprocess(
                [sys.executable, str(SKILLS_DIR / "graph_init"),
                 "--project-id", str(project_id), "--project-dir", str(PROJECT_DIR)],
                cwd=PROJECT_DIR, label="graph_init")
            log_ok("Graph initialized")

    result = run_agent("morpheus", str(PROMPTS_DIR / "morpheus.md"),
                       dry_run=dry_run)
    check_agent_result("morpheus", result, timer)

    created_files = []
    if not dry_run:
        base = PROJECT_DIR
        verify_files("2", [base / "dialogue.json"])
        for sub in ("cast", "locations", "props"):
            d = base / sub
            if not any(d.glob("*.json")):
                log_warn(f"No JSON files found in {sub}/ -- morpheus may not "
                         f"have materialized fully.")
        manifest = read_manifest()
        if not manifest.get("frames"):
            log_warn("manifest.frames[] is empty -- morpheus may not have "
                     "populated frames.")

        # Verify graph exists and has data
        graph_path = base / "graph" / "narrative_graph.json"
        if graph_path.exists():
            log_ok(f"Narrative graph: {graph_path.stat().st_size // 1024}KB")
        else:
            log_warn("narrative_graph.json not found — morpheus may not have saved graph")

        # Verify prompt files exist
        frame_prompts = list((base / "frames" / "prompts").glob("*_image.json")) if (base / "frames" / "prompts").exists() else []
        video_prompts = list((base / "video" / "prompts").glob("*_video.json")) if (base / "video" / "prompts").exists() else []
        log_ok(f"Assembled prompts: {len(frame_prompts)} image, {len(video_prompts)} video")

        log("Files created by Phase 2:")
        for sub in ("cast", "locations", "props", "graph"):
            list_dir_files(base / sub)
            created_files.extend(collect_files_in(base / sub))
        save_phase_report(2, timer, "morpheus", result, created_files)

        # Quality gate
        if not run_quality_gate(2, base):
            log_warn("Phase 2 quality gate FAILED — re-running morpheus (attempt 2/2)...")
            timer2 = Timer()
            result2 = run_agent("morpheus", str(PROMPTS_DIR / "morpheus.md"),
                                dry_run=dry_run)
            check_agent_result("morpheus", result2, timer2)
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

MIN_VALID_SIZE = 10240  # 10KB — anything smaller is likely corrupt/empty
MAX_REGEN_ATTEMPTS = 2


def _programmatic_asset_validation_and_regen(base: Path) -> None:
    """Programmatic replacement for the image_verifier agent.

    For each asset type (cast/location/prop):
      1. Read prompt files to know what SHOULD exist.
      2. Check if the output image exists and is valid (>10KB).
      3. Re-generate missing or corrupt images via sw_fresh_generation.
      4. Queue manifest updates for all valid assets.
    """
    stats = {"reviewed": 0, "missing_regen": 0, "corrupt_regen": 0, "regen_ok": 0, "regen_fail": 0}

    manifest_updates: list[dict] = []

    for prompt_rel, output_rel, prompt_suffix, output_suffix, target, id_field in _ASSET_PROMPT_MAP:
        prompt_dir = base / prompt_rel
        output_dir = base / output_rel
        if not prompt_dir.exists():
            continue
        output_dir.mkdir(parents=True, exist_ok=True)

        for prompt_file in sorted(prompt_dir.glob(f"*{prompt_suffix}")):
            # Derive entity ID and expected output path
            entity_id = prompt_file.stem.replace(prompt_suffix.replace(".json", ""), "")
            output_path = output_dir / f"{entity_id}{output_suffix}"
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
                # Read the original prompt from the JSON file
                try:
                    prompt_data = json.loads(prompt_file.read_text(encoding="utf-8"))
                    gen_prompt = prompt_data.get("prompt", prompt_data.get("text", ""))
                    if not gen_prompt:
                        log_warn(f"  No prompt found in {prompt_file.name} — skipping regen")
                        stats["regen_fail"] += 1
                        continue
                except (json.JSONDecodeError, OSError) as e:
                    log_warn(f"  Failed to read prompt {prompt_file.name}: {e}")
                    stats["regen_fail"] += 1
                    continue

                # Determine size preset from prompt data
                size = prompt_data.get("image_size", "landscape_16_9")

                # Attempt regen via sw_fresh_generation (higher quality)
                regen_ok = False
                for attempt in range(1, MAX_REGEN_ATTEMPTS + 1):
                    log(f"  Regenerating {output_path.name} (attempt {attempt}/{MAX_REGEN_ATTEMPTS})...")
                    regen_result = _stream_subprocess(
                        [sys.executable, str(SKILLS_DIR / "sw_fresh_generation"),
                         "--prompt", gen_prompt,
                         "--size", size,
                         "--out", str(output_path)],
                        cwd=base, timeout=120, label=f"regen_{entity_id}",
                    )
                    if regen_result.returncode == 0 and output_path.exists() and output_path.stat().st_size >= MIN_VALID_SIZE:
                        log_ok(f"  Regenerated {output_path.name} successfully")
                        regen_ok = True
                        break
                    # On safety filter or other failure, try rephrasing (strip adjectives)
                    gen_prompt = gen_prompt.replace("sexy", "").replace("violent", "").replace("bloody", "").strip()

                if regen_ok:
                    stats["regen_ok"] += 1
                else:
                    stats["regen_fail"] += 1
                    log_warn(f"  Failed to regenerate {output_path.name} after {MAX_REGEN_ATTEMPTS} attempts — skipping")
                    continue

            # Build manifest update for valid assets
            if output_path.exists() and output_path.stat().st_size >= MIN_VALID_SIZE:
                rel_path = str(output_path.relative_to(base))
                if target == "cast":
                    manifest_updates.append({
                        "target": "cast", id_field: entity_id,
                        "set": {"compositePath": rel_path, "compositeStatus": "generated"},
                    })
                elif target == "location":
                    manifest_updates.append({
                        "target": "location", id_field: entity_id,
                        "set": {"primaryImagePath": rel_path, "imageStatus": "generated"},
                    })
                elif target == "prop":
                    manifest_updates.append({
                        "target": "prop", id_field: entity_id,
                        "set": {"imagePath": rel_path, "imageStatus": "generated"},
                    })

    # Flush manifest updates via sw_queue_update
    if manifest_updates:
        payload = json.dumps({"updates": manifest_updates})
        _stream_subprocess(
            [sys.executable, str(SKILLS_DIR / "sw_queue_update"), "--payload", payload],
            cwd=base, label="manifest_queue_update",
        )
        flush_manifest_queue()
        log_ok(f"Queued {len(manifest_updates)} manifest update(s)")

    log(f"Asset validation complete: {stats['reviewed']} reviewed, "
        f"{stats['missing_regen']} missing, {stats['corrupt_regen']} corrupt, "
        f"{stats['regen_ok']} regenerated OK, {stats['regen_fail']} failed")


def _populate_voice_nodes(dry_run: bool) -> None:
    """Populate VoiceNode entries in the graph for all speaking cast members.
    Replaces the Voice Director agent — runs programmatically."""
    if dry_run:
        log("[DRY-RUN] Would populate voice nodes", YELLOW)
        return

    _stream_subprocess(
        [sys.executable, str(SKILLS_DIR / "graph_populate_voices"),
         "--project-dir", str(PROJECT_DIR)],
        cwd=PROJECT_DIR, label="graph_populate_voices")
    log_ok("Voice nodes populated in graph")


def _generate_location_variants(dry_run: bool) -> None:
    """Generate location direction variants and state variants via edit_image."""
    if dry_run:
        log("[DRY-RUN] Would generate location direction variants", YELLOW)
        return

    result = _stream_subprocess(
        [sys.executable, str(SKILLS_DIR / "graph_generate_assets"),
         "--project-dir", str(PROJECT_DIR), "--batch-size", "10",
         "--skip-existing"],
        cwd=PROJECT_DIR, timeout=600, label="graph_generate_assets",
    )
    if result.stdout:
        for line in result.stdout.strip().split("\n"):
            log(f"  {line}")
    if result.returncode != 0:
        log_warn(f"Location variant generation had failures (exit {result.returncode})")
    else:
        log_ok("Location direction variants generated")


def _verify_storyboard_refs_tagged() -> tuple[bool, list[str]]:
    """Collect all ref_images from storyboard prompts and verify each is tagged.

    Runs after asset generation + batch tagging but before storyboard generation.
    Any untagged images in TAGGED_DIRS are tagged now. Missing files are logged.

    Returns (all_ok, problems) so the caller can decide whether to proceed.
    """
    prompt_dir = PROJECT_DIR / "frames" / "storyboard_prompts"
    if not prompt_dir.exists():
        return True, []

    prompt_files = sorted(prompt_dir.glob("*_storyboard.json"))
    if not prompt_files:
        return True, []

    # Collect all unique ref_image paths across all storyboard prompts
    all_refs: list[str] = []
    for pf in prompt_files:
        data = json.loads(pf.read_text(encoding="utf-8"))
        all_refs.extend(data.get("ref_images", []))

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


def _generate_single_storyboard(prompt_data: dict) -> bool:
    """Generate one storyboard image via direct HTTP POST to the server.

    Returns True on success, False on failure. Resolves ref_images to absolute
    paths and sends them inline so the server can attach them to the prediction.
    """
    import httpx as _httpx

    chain_id = prompt_data.get("chain_id") or prompt_data.get("scene_id", "unknown")
    out_rel = prompt_data.get("out_path", f"frames/storyboards/{chain_id}_storyboard.png")
    out_abs = PROJECT_DIR / out_rel
    out_abs.parent.mkdir(parents=True, exist_ok=True)

    # Skip if already generated
    if out_abs.exists() and out_abs.stat().st_size > 1000:
        log(f"  {chain_id}: already exists ({out_abs.stat().st_size:,}B) — skipping")
        return True

    # Resolve reference images to absolute paths, verify they exist
    ref_images = []
    for ref in prompt_data.get("ref_images", []):
        ref_path = PROJECT_DIR / ref if not Path(ref).is_absolute() else Path(ref)
        if ref_path.exists():
            ref_images.append(str(ref_path))
        else:
            log_warn(f"  {chain_id}: ref image missing: {ref}")

    body = {
        "prompt": prompt_data["prompt"],
        "image_size": prompt_data.get("size", "landscape_16_9"),
        "output_path": str(out_abs),
        "output_format": "png",
        "reference_images": ref_images,
    }

    log(f"  {chain_id}: generating storyboard ({len(ref_images)} ref images)...")
    try:
        resp = _httpx.post(
            f"{SERVER_URL}/internal/fresh-generation",
            json=body, timeout=180,
        )
        if resp.status_code >= 400:
            log_warn(f"  {chain_id}: server returned {resp.status_code}: {resp.text[:200]}")
            return False
        data = resp.json()
        if data.get("success"):
            log_ok(f"  {chain_id}: storyboard generated → {out_abs.name}")
            return True
        log_warn(f"  {chain_id}: generation unsuccessful: {data}")
        return False
    except Exception as e:
        log_warn(f"  {chain_id}: HTTP error: {e}")
        return False


def _update_chain_graph(chain_id: str, image_path: str) -> None:
    """Update ChainedFrameGroup on graph with storyboard path."""
    try:
        from graph.store import GraphStore
        store = GraphStore(str(PROJECT_DIR))
        graph = store.load()
        chain = graph.chained_frame_groups.get(chain_id)
        if chain:
            if chain.storyboard_image_path and chain.storyboard_image_path != image_path:
                chain.storyboard_history.append(chain.storyboard_image_path)
            chain.storyboard_image_path = image_path
            chain.storyboard_status = "generated"
            store.save(graph)
    except Exception as e:
        log_warn(f"  Could not update chain {chain_id} on graph: {e}")


def _generate_storyboards_phase3(dry_run: bool) -> None:
    """Generate multi-panel storyboards for each chained frame group.
    Fully programmatic — reads prompt JSONs and POSTs directly to the server.
    No agent or skill subprocesses for the generation step."""
    if dry_run:
        log("[DRY-RUN] Would generate chained frame storyboards", YELLOW)
        return

    # Build chained frame groups
    _stream_subprocess(
        [sys.executable, str(SKILLS_DIR / "graph_build_chains"),
         "--project-dir", str(PROJECT_DIR)],
        cwd=PROJECT_DIR, label="graph_build_chains")

    # Re-assemble prompts so chain storyboard prompts are generated
    _stream_subprocess(
        [sys.executable, str(SKILLS_DIR / "graph_assemble_prompts"),
         "--project-dir", str(PROJECT_DIR)],
        cwd=PROJECT_DIR, label="graph_assemble_prompts")

    # Generate storyboards directly from prompt JSONs
    prompt_dir = PROJECT_DIR / "frames" / "storyboard_prompts"
    if not prompt_dir.exists():
        log_warn("No storyboard_prompts directory — skipping storyboard generation")
        return

    prompt_files = sorted(prompt_dir.glob("*_storyboard.json"))
    if not prompt_files:
        log_warn("No storyboard prompt JSONs found — skipping")
        return

    # Tag verification gate — ensure every ref image is tagged before generation
    refs_ok, ref_problems = _verify_storyboard_refs_tagged()
    if not refs_ok:
        log_warn(f"Storyboard ref verification failed ({len(ref_problems)} issue(s)) — "
                 "storyboards will generate with incomplete references")

    log(f"Generating {len(prompt_files)} chain storyboards (programmatic)...")
    generated = 0
    for pf in prompt_files:
        data = json.loads(pf.read_text(encoding="utf-8"))
        if _generate_single_storyboard(data):
            generated += 1
            chain_id = data.get("chain_id")
            if chain_id:
                out_rel = data.get("out_path", f"frames/storyboards/{chain_id}_storyboard.png")
                _update_chain_graph(chain_id, out_rel)

    log_ok(f"Generated {generated}/{len(prompt_files)} chain storyboards")

    # Re-assemble prompts so frame ref_images now include chain storyboard paths
    log("Re-assembling prompts with chain storyboard references...")
    _stream_subprocess(
        [sys.executable, str(SKILLS_DIR / "graph_assemble_prompts"),
         "--project-dir", str(PROJECT_DIR)],
        cwd=PROJECT_DIR, label="graph_assemble_prompts")
    log_ok("Prompts re-assembled with chain storyboard references")


def phase_3_assets(dry_run: bool, phase_timers: dict) -> None:
    """Phase 3 -- Fully programmatic: asset generation, voice nodes, location variants,
    storyboard generation, and programmatic quality validation."""
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

    # Step 3a: Programmatic generation of all cast/location/prop base images
    log("--- Phase 3a: Programmatic asset generation ---")
    if not dry_run:
        gen_result = _stream_subprocess(
            [sys.executable, str(SKILLS_DIR / "graph_generate_assets"),
             "--project-dir", str(PROJECT_DIR), "--batch-size", "10",
             "--skip-existing"],
            cwd=PROJECT_DIR, timeout=600, label="graph_generate_assets",
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

    # Step 3c: Populate voice nodes (replaces Voice Director agent)
    log("--- Phase 3c: Voice node population ---")
    _populate_voice_nodes(dry_run)

    # Step 3d: Generate location direction variants + state variants
    log("--- Phase 3d: Location direction & state variants ---")
    _generate_location_variants(dry_run)

    # Step 3e: Validate assets + update manifest so graph has real paths
    log("--- Phase 3e: Asset validation + manifest sync ---")
    if not dry_run:
        _programmatic_asset_validation_and_regen(PROJECT_DIR)

    # Step 3f: Sync manifest → graph + re-assemble prompts (now with real paths)
    log("--- Phase 3f: Sync assets → graph → re-assemble prompts ---")
    if not dry_run:
        _stream_subprocess(
            [sys.executable, str(SKILLS_DIR / "graph_sync_assets"),
             "--project-dir", str(PROJECT_DIR)],
            cwd=PROJECT_DIR, label="graph_sync_assets")
        _stream_subprocess(
            [sys.executable, str(SKILLS_DIR / "graph_assemble_prompts"),
             "--project-dir", str(PROJECT_DIR)],
            cwd=PROJECT_DIR, label="graph_assemble_prompts")
        log_ok("Asset paths synced, prompts re-assembled with ref images")

    # Step 3g: Storyboard generation (includes tag verification gate)
    log("--- Phase 3g: Storyboard generation (chained frames) ---")
    _generate_storyboards_phase3(dry_run)

    created_files = []
    if not dry_run:
        base = PROJECT_DIR
        log("Files created by Phase 3:")
        list_dir_files(base / "cast" / "composites")
        list_dir_files(base / "locations" / "primary")
        list_dir_files(base / "props" / "generated")
        list_dir_files(base / "frames" / "storyboards")
        list_dir_files(base / "voices")
        created_files = (collect_files_in(base / "cast" / "composites") +
                         collect_files_in(base / "locations" / "primary") +
                         collect_files_in(base / "props" / "generated") +
                         collect_files_in(base / "frames" / "storyboards") +
                         collect_files_in(base / "voices"))
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


def _generate_frame(prompt_json: Path, dry_run: bool) -> subprocess.CompletedProcess | None:
    """Generate a single composed frame from its prompt JSON. No agent needed."""
    data = json.loads(prompt_json.read_text())
    frame_id = data["frame_id"]
    out_path = data.get("out_path", f"frames/composed/{frame_id}_gen.png")
    out_abs = PROJECT_DIR / out_path

    # Skip if already generated
    if out_abs.exists() and out_abs.stat().st_size > 1000:
        log(f"  {frame_id}: already exists ({out_abs.stat().st_size:,}B) — skipping")
        return None

    ref_args = ""
    if data.get("ref_images"):
        ref_args = ",".join(str(r) for r in data["ref_images"])

    cmd = [
        sys.executable, str(SKILLS_DIR / "sw_generate_frame"),
        "--prompt", data["prompt"],
        "--out", out_path,
        "--size", data.get("size", "landscape_16_9"),
    ]
    if ref_args:
        cmd += ["--ref-images", ref_args]

    if dry_run:
        log(f"  [DRY-RUN] Would generate {frame_id}", YELLOW)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return _stream_subprocess(
        cmd, cwd=PROJECT_DIR, timeout=None, label=f"frame_{frame_id}",
        env={**os.environ, "PROJECT_DIR": str(PROJECT_DIR), "SKILLS_DIR": str(SKILLS_DIR)},
    )


def _audit_phase4_assets() -> dict:
    """Check that all expected Phase 3 assets exist before composing frames.

    Returns dict with:
        ready: bool — True if all critical assets present
        missing_cast: list[str] — cast IDs with missing/corrupt composites
        missing_locations: list[str] — location IDs with missing/corrupt images
        missing_props: list[str] — prop IDs with missing/corrupt images
        missing_storyboards: list[str] — chain IDs with missing storyboards
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

    # Storyboards
    for chain_id, chain in graph.chained_frame_groups.items():
        if chain.storyboard_image_path:
            p = base / chain.storyboard_image_path
            if not p.exists() or p.stat().st_size < 1000:
                missing_storyboards.append(chain_id)
        else:
            expected = base / "frames" / "storyboards" / f"{chain_id}_storyboard.png"
            if not expected.exists() or expected.stat().st_size < 1000:
                missing_storyboards.append(chain_id)

    total = len(missing_cast) + len(missing_locations) + len(missing_props) + len(missing_storyboards)

    return {
        "ready": total == 0,
        "missing_cast": missing_cast,
        "missing_locations": missing_locations,
        "missing_props": missing_props,
        "missing_storyboards": missing_storyboards,
        "total_missing": total,
    }


def phase_4_production(dry_run: bool, phase_timers: dict,
                       skip_tts: bool = False) -> None:
    """Phase 4 -- Programmatic frame composition from assembled prompts.
    No agents — calls sw_generate_frame directly per frame."""
    log_header("PHASE 4 -- Frame Composition (Programmatic)")
    timer = Timer()
    phase_timers["phase_4"] = timer
    base = PROJECT_DIR

    # ── Asset readiness gate — fall back to Phase 3 regen if anything missing ──
    if not dry_run:
        audit = _audit_phase4_assets()
        if not audit["ready"]:
            log_warn(f"Phase 4 asset audit: {audit['total_missing']} missing asset(s)")
            if audit["missing_cast"]:
                log_warn(f"  Cast: {', '.join(audit['missing_cast'])}")
            if audit["missing_locations"]:
                log_warn(f"  Locations: {', '.join(audit['missing_locations'])}")
            if audit["missing_props"]:
                log_warn(f"  Props: {', '.join(audit['missing_props'])}")
            if audit["missing_storyboards"]:
                log_warn(f"  Storyboards: {', '.join(audit['missing_storyboards'])}")

            log("Falling back to Phase 3 asset regen for missing items...")
            _programmatic_asset_validation_and_regen(base)

            # Regenerate missing storyboards
            if audit["missing_storyboards"]:
                log("Regenerating missing storyboards...")
                _generate_storyboards_phase3(dry_run=False)

            # Re-audit after regen
            audit = _audit_phase4_assets()
            if not audit["ready"]:
                log_warn(f"Still {audit['total_missing']} missing after regen — proceeding with available assets")
            else:
                log_ok("All missing assets recovered")

    # Ensure graph has latest asset paths and prompts include ref_images
    log("Syncing asset paths and re-assembling prompts...")
    if not dry_run:
        _stream_subprocess(
            [sys.executable, str(SKILLS_DIR / "graph_sync_assets"),
             "--project-dir", str(PROJECT_DIR)],
            cwd=PROJECT_DIR, label="graph_sync_assets")
        _stream_subprocess(
            [sys.executable, str(SKILLS_DIR / "graph_assemble_prompts"),
             "--project-dir", str(PROJECT_DIR)],
            cwd=PROJECT_DIR, label="graph_assemble_prompts")

    prompts_dir = base / "frames" / "prompts"
    prompt_files = sorted(prompts_dir.glob("*_image.json"))

    if not prompt_files:
        log_warn("No frame prompt files found in frames/prompts/ — nothing to compose")
        advance_phase(4, 5, dry_run)
        return

    total = len(prompt_files)
    log(f"Composing {total} frames from assembled prompts...")

    generated = 0
    skipped = 0
    failed = 0

    FRAME_CONCURRENCY = 10

    def _gen_one(item: tuple[int, Path]) -> tuple[str, int]:
        """Returns (frame_id, status) where status: 0=ok, 1=fail, 2=skip."""
        i, pf = item
        frame_id = pf.stem.replace("_image", "")
        log(f"[{i}/{total}] {frame_id}")
        result = _generate_frame(pf, dry_run)
        if result is None:
            return frame_id, 2
        return frame_id, 0 if result.returncode == 0 else 1

    work_items = list(enumerate(prompt_files, 1))

    if dry_run:
        for item in work_items:
            fid, status = _gen_one(item)
            if status == 2:
                skipped += 1
            elif status == 0:
                generated += 1
            else:
                failed += 1
    else:
        with ThreadPoolExecutor(max_workers=FRAME_CONCURRENCY) as pool:
            futures = {pool.submit(_gen_one, item): item for item in work_items}
            for future in as_completed(futures):
                fid, status = future.result()
                if status == 0:
                    generated += 1
                elif status == 2:
                    skipped += 1
                else:
                    failed += 1
                    log_err(f"  {fid} failed")

    log_ok(f"Frame composition done: {generated} generated, {skipped} skipped, {failed} failed")

    if not dry_run:
        log("Files created by Phase 4:")
        list_dir_files(base / "frames" / "composed")
        created_files = collect_files_in(base / "frames" / "composed")
        save_phase_report(4, timer, "phase_4_programmatic", None, created_files)

        if not run_quality_gate(4, base):
            log_warn("Phase 4 quality gate has warnings — proceeding")

    advance_phase(4, 5, dry_run)
    log_ok(f"Phase 4 complete in {timer.elapsed_str()}")


def phase_4_production_parallel(dry_run: bool, phase_timers: dict,
                                 num_workers: int = 10) -> None:
    """Phase 4 -- Parallel programmatic frame composition.
    Splits frames into batches and generates in parallel threads."""
    log_header("PHASE 4 -- Frame Composition (Parallel)")
    timer = Timer()
    phase_timers["phase_4"] = timer
    base = PROJECT_DIR

    # Ensure graph has latest asset paths and prompts include ref_images
    log("Syncing asset paths and re-assembling prompts...")
    if not dry_run:
        _stream_subprocess(
            [sys.executable, str(SKILLS_DIR / "graph_sync_assets"),
             "--project-dir", str(PROJECT_DIR)],
            cwd=PROJECT_DIR, label="graph_sync_assets")
        _stream_subprocess(
            [sys.executable, str(SKILLS_DIR / "graph_assemble_prompts"),
             "--project-dir", str(PROJECT_DIR)],
            cwd=PROJECT_DIR, label="graph_assemble_prompts")

    prompts_dir = base / "frames" / "prompts"
    prompt_files = sorted(prompts_dir.glob("*_image.json"))
    total = len(prompt_files)

    if total == 0:
        log_warn("No frame prompt files found — nothing to compose")
        advance_phase(4, 5, dry_run)
        return

    actual_workers = min(num_workers, total)
    log(f"Composing {total} frames with {actual_workers} parallel workers...")

    generated = 0
    skipped = 0
    failed = 0

    def gen_one(pf: Path) -> tuple[str, int]:
        """Returns (frame_id, status) where status: 0=ok, 1=fail, 2=skip."""
        result = _generate_frame(pf, dry_run)
        fid = pf.stem.replace("_image", "")
        if result is None:
            return fid, 2
        return fid, 0 if result.returncode == 0 else 1

    if dry_run:
        for pf in prompt_files:
            gen_one(pf)
    else:
        with ThreadPoolExecutor(max_workers=actual_workers) as executor:
            futures = {executor.submit(gen_one, pf): pf for pf in prompt_files}
            for future in as_completed(futures):
                fid, status = future.result()
                if status == 0:
                    generated += 1
                elif status == 2:
                    skipped += 1
                else:
                    failed += 1

    log_ok(f"Parallel composition done: {generated} generated, {skipped} skipped, {failed} failed")

    if not dry_run:
        log("Files created by Phase 4:")
        list_dir_files(base / "frames" / "composed")
        created_files = collect_files_in(base / "frames" / "composed")
        save_phase_report(4, timer, "phase_4_parallel", None, created_files)

        if not run_quality_gate(4, base):
            log_warn("Phase 4 quality gate has warnings — proceeding")

    advance_phase(4, 5, dry_run)
    log_ok(f"Phase 4 (parallel) complete in {timer.elapsed_str()}")


def _generate_video_clip(frame_id: str, image_path: Path, prompt: str,
                         duration: int, out_path: Path,
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
        "--duration", str(duration),
    ]

    if dry_run:
        log(f"  [DRY-RUN] Would generate clip for {frame_id}", YELLOW)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return _stream_subprocess(
        cmd, cwd=PROJECT_DIR, timeout=None, label=f"video_{frame_id}",
        env={**os.environ, "PROJECT_DIR": str(PROJECT_DIR), "SKILLS_DIR": str(SKILLS_DIR)},
    )


def phase_5_video(dry_run: bool, phase_timers: dict) -> None:
    """Phase 5 -- Programmatic video clip generation from composed frames.
    No agents — calls sw_generate_video directly per frame."""
    log_header("PHASE 5 -- Video Generation (Programmatic)")
    timer = Timer()
    phase_timers["phase_5"] = timer
    base = PROJECT_DIR

    # Get frame list from manifest
    manifest = json.loads(MANIFEST_PATH.read_text())
    frames = manifest.get("frames", [])

    if not frames:
        log_warn("No frames in manifest — nothing to generate")
        advance_phase(5, 6, dry_run)
        return

    clips_dir = base / "video" / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    total = len(frames)
    log(f"Generating {total} video clips from composed frames...")

    generated = 0
    skipped = 0
    failed = 0

    # Build work items
    work_items = []
    for i, f in enumerate(frames, 1):
        fid = f.get("frameId", "")
        image_path = base / "frames" / "composed" / f"{fid}_gen.png"

        if not image_path.exists():
            log_warn(f"  [{i}/{total}] {fid}: composed frame missing — skipping")
            failed += 1
            continue

        video_prompt_file = base / "video" / "prompts" / f"{fid}_video.json"
        if video_prompt_file.exists():
            prompt_data = json.loads(video_prompt_file.read_text())
            video_prompt = prompt_data.get("prompt", "")[:500]
        else:
            video_prompt = f"Cinematic scene, subtle motion, frame {fid}"

        dur = max(3, min(15, int(f.get("suggestedDuration", 5))))
        out_path = clips_dir / f"{fid}.mp4"
        work_items.append((i, fid, image_path, video_prompt, dur, out_path))

    # Generate video clips 10 at a time
    VIDEO_CONCURRENCY = 10

    def _gen_one(item):
        idx, fid, image_path, video_prompt, dur, out_path = item
        log(f"[{idx}/{total}] {fid} ({dur}s)")
        return fid, _generate_video_clip(fid, image_path, video_prompt, dur, out_path, dry_run)

    with ThreadPoolExecutor(max_workers=VIDEO_CONCURRENCY) as pool:
        futures = {pool.submit(_gen_one, item): item for item in work_items}
        for future in as_completed(futures):
            fid, result = future.result()
            if result is None:
                skipped += 1
            elif result.returncode == 0:
                generated += 1
            else:
                failed += 1
                log_err(f"  {fid} clip generation failed (exit={result.returncode})")

    log_ok(f"Video generation done: {generated} generated, {skipped} skipped, {failed} failed")

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
        help="Override default model for all agents (e.g., claude-haiku-4-5-20251001)",
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
        "--skip-tts",
        action="store_true",
        help="Deprecated no-op. The TTS stage was removed; Phase 4 now derives timing "
             "from dialogue-aware prompt assembly for native video audio.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Auto-detect last completed phase from manifest and continue from the next one.",
    )
    return parser.parse_args()


def main() -> None:
    global PROJECT_DIR, MANIFEST_PATH, LOGS_DIR, PIPELINE_LOGS_DIR, DEFAULT_MODEL
    args = parse_args()
    dry_run   = args.dry_run
    only_phase = args.phase
    parallel_p4 = args.parallel_phase4
    skip_tts = args.skip_tts

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

    if dry_run:
        log_warn("DRY-RUN MODE -- no agents will be spawned, no files modified.")

    # Deploy shared conventions as CLAUDE.md into project dir (prompt caching)
    _deploy_shared_conventions(PROJECT_DIR)

    # Ensure log directories exist
    PIPELINE_LOGS_DIR.mkdir(parents=True, exist_ok=True)

    pipeline_timer = Timer()
    phase_timers: dict[str, Timer] = {}

    # Start and verify server (needed even for single-phase runs -- skills call it)
    start_server(dry_run)
    wait_for_server(dry_run)

    export_path: str | None = None

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
        match n:
            case 0: phase_0_verify(dry_run)
            case 1: phase_1_narrative(dry_run, phase_timers)
            case 2: phase_2_morpheus(dry_run, phase_timers)
            case 3: phase_3_assets(dry_run, phase_timers)
            case 4:
                if parallel_p4 > 0:
                    phase_4_production_parallel(dry_run, phase_timers,
                                                 num_workers=parallel_p4)
                else:
                    phase_4_production(dry_run, phase_timers,
                                       skip_tts=skip_tts)
            case 5: phase_5_video(dry_run, phase_timers)
            case 6: return phase_6_export(dry_run, phase_timers)
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

    finally:
        stop_server()

    print_summary(pipeline_timer, phase_timers, export_path)


if __name__ == "__main__":
    main()
