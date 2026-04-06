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
import json
import math
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

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
    1: "Narrative Writing",
    2: "Graph Construction",
    3: "Asset Generation & Verification",
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
# Agent spawning
# ---------------------------------------------------------------------------

def run_agent(
    agent_id: str,
    prompt_file: str,
    project_dir: Path | None = None,
    model: str = DEFAULT_MODEL,
    dry_run: bool = False,
    prompt_prefix: str = "",
) -> subprocess.CompletedProcess:
    """Spawn a Claude CLI agent and wait for it to finish.

    Args:
        prompt_prefix: Optional text prepended to the system prompt (e.g. to
                       override sub-phase waiting behavior).
    """
    if project_dir is None:
        project_dir = PROJECT_DIR

    prompt_path = Path(prompt_file)
    if not prompt_path.exists():
        fail(f"Prompt file not found: {prompt_file}")

    system_prompt = prompt_path.read_text()
    if prompt_prefix:
        system_prompt = prompt_prefix + "\n\n" + system_prompt

    env = {**os.environ, "PROJECT_DIR": str(project_dir), "SKILLS_DIR": str(SKILLS_DIR)}
    # Remove CLAUDECODE env var to prevent nested-session detection
    env.pop("CLAUDECODE", None)

    # Build the user message (trigger prompt).
    # The system prompt goes via --system-prompt-file to avoid command line length limits on Windows.
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

    timeout_str = f"{PHASE_TIMEOUT}s" if PHASE_TIMEOUT else "unlimited"
    log(f"Spawning agent '{agent_id}' ...  (timeout={timeout_str})")
    try:
        result = subprocess.run(
            cmd,
            cwd=str(project_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=PHASE_TIMEOUT,
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

    # Check scene outlines exist
    outlines_dir = base / "creative_output" / "scene_outlines"
    if outlines_dir.exists():
        outlines = list(outlines_dir.glob("*.md"))
        if len(outlines) < 2:
            issues.append(f"Only {len(outlines)} scene outline(s) — expected at least 2")
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
    """Validate Phase 4 outputs (composed frames + timeline). No TTS checks."""
    issues = []

    manifest = json.loads((base / "project_manifest.json").read_text())
    frames = manifest.get("frames", [])

    # Timeline
    timeline_path = base / "logs" / "composition_verifier" / "timeline.json"
    if not timeline_path.exists():
        # Also check old path for backwards compat
        timeline_path = base / "logs" / "production_coordinator" / "timeline.json"
    if not timeline_path.exists():
        issues.append("timeline.json missing")
    else:
        try:
            tl = json.loads(timeline_path.read_text())
            tl_frames = tl.get("frames", [])
            if len(tl_frames) < len(frames):
                issues.append(f"timeline.json has {len(tl_frames)} frames but manifest has {len(frames)}")
        except json.JSONDecodeError:
            issues.append("timeline.json is invalid JSON")

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

    # Verify story source exists (story_seed.txt or pitch.md)
    seed = PROJECT_DIR / "source_files" / "story_seed.txt"
    pitch = PROJECT_DIR / "source_files" / "pitch.md"
    if not seed.exists() and not pitch.exists():
        fail(f"No story source found — need story_seed.txt or pitch.md in source_files/")
    source = seed if seed.exists() else pitch
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
    """Phase 1 -- Creative Coordinator writes all narrative content."""
    log_header("PHASE 1 -- Narrative (Creative Writing)")
    timer = Timer()
    phase_timers["phase_1"] = timer

    prompt_file = str(PROMPTS_DIR / "creative_coordinator.md")

    # Prepend instruction to complete all sub-phases in one pass
    cc_prefix = (
        "IMPORTANT OVERRIDE: Complete ALL three sub-phases (skeleton, scene outlines, "
        "scene drafts / creative_output.md) in ONE continuous pass. Do NOT stop between "
        "sub-phases. Do NOT wait for review or approval. Do NOT write 'awaiting_review' "
        "to any state file. Proceed through all sub-phases autonomously until "
        "creative_output.md is fully written."
    )

    result = run_agent("creative_coordinator", prompt_file,
                       dry_run=dry_run, prompt_prefix=cc_prefix)
    check_agent_result("creative_coordinator", result, timer)

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
        save_phase_report(1, timer, "creative_coordinator", result, created_files)

        # Quality gate
        if not run_quality_gate(1, base):
            log_warn("Phase 1 quality gate FAILED — re-running agent (attempt 2/2)...")
            timer2 = Timer()
            result2 = run_agent("creative_coordinator", prompt_file,
                                dry_run=dry_run, prompt_prefix=cc_prefix)
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
            import subprocess as sp
            sp.run([sys.executable, str(SKILLS_DIR / "graph_init"),
                    "--project-id", str(project_id), "--project-dir", str(PROJECT_DIR)],
                   cwd=str(PROJECT_DIR), capture_output=True, text=True)
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


def phase_3_assets(dry_run: bool, phase_timers: dict) -> None:
    """Phase 3 -- Programmatic asset generation + Image Verifier review. Sequential for MVP."""
    log_header("PHASE 3 -- Asset Generation + Image Verification")
    timer = Timer()
    phase_timers["phase_3"] = timer

    # Step 1: Programmatic generation of all cast/location/prop images
    log("--- Phase 3a: Programmatic asset generation ---")
    if not dry_run:
        import subprocess as _sp
        gen_result = _sp.run(
            [sys.executable, str(SKILLS_DIR / "graph_generate_assets"),
             "--project-dir", str(PROJECT_DIR), "--batch-size", "10"],
            cwd=str(PROJECT_DIR), capture_output=True, text=True, timeout=600,
        )
        if gen_result.stdout:
            for line in gen_result.stdout.strip().split("\n"):
                log(f"  {line}")
        if gen_result.returncode != 0:
            log_warn(f"Asset generation had failures (exit {gen_result.returncode})")
            if gen_result.stderr:
                log_warn(gen_result.stderr[:500])
        else:
            log_ok("All assets generated programmatically")

    # Step 2: Image Verifier agent reviews and fixes errors
    log("--- Phase 3b: Image Verifier (review + fix) ---")
    sc_timer = Timer()
    sc_result = run_agent("image_verifier", str(PROMPTS_DIR / "image_verifier.md"),
                          dry_run=dry_run)
    check_agent_result("image_verifier", sc_result, sc_timer)

    # Tag generated images with entity names (cast, locations, props, mood)
    if not dry_run:
        log("--- Phase 3 post: Auto-tagging generated images ---")
        try:
            sys.path.insert(0, str(APP_DIR))
            from image_tagger import tag_all_project_images
            tag_count = tag_all_project_images(PROJECT_DIR)
            log_ok(f"Tagged {tag_count} images with entity names")
        except Exception as exc:
            log_warn(f"Image tagging failed (non-blocking): {exc}")

    created_files = []
    if not dry_run:
        base = PROJECT_DIR
        log("Files created by Phase 3:")
        list_dir_files(base / "assets")
        list_dir_files(base / "frames" / "composed")
        list_dir_files(base / "cast" / "composites")
        list_dir_files(base / "locations" / "primary")
        created_files = (collect_files_in(base / "assets") +
                         collect_files_in(base / "frames" / "composed") +
                         collect_files_in(base / "cast" / "composites") +
                         collect_files_in(base / "locations" / "primary"))
        save_phase_report(3, timer, "image_verifier", sc_result, created_files)

        # Quality gate
        if not run_quality_gate(3, base):
            log_warn("Phase 3 quality gate FAILED — re-running image verifier (attempt 2/2)...")
            sc_timer2 = Timer()
            sc_result2 = run_agent("image_verifier", str(PROMPTS_DIR / "image_verifier.md"),
                                    dry_run=dry_run)
            check_agent_result("image_verifier", sc_result2, sc_timer2)
            if not run_quality_gate(3, base):
                log_warn("Phase 3 quality gate still failing after retry — proceeding with warnings")

    # Sync generated asset paths back into graph and re-assemble prompts with ref images
    if not dry_run:
        log("--- Phase 3 post: Syncing assets into graph + re-assembling prompts ---")
        import subprocess as _sp
        _sp.run([sys.executable, str(SKILLS_DIR / "graph_sync_assets"),
                 "--project-dir", str(PROJECT_DIR)], cwd=str(PROJECT_DIR))
        _sp.run([sys.executable, str(SKILLS_DIR / "graph_assemble_prompts"),
                 "--project-dir", str(PROJECT_DIR)], cwd=str(PROJECT_DIR))
        log_ok("Asset paths synced into graph, prompts re-assembled with ref images")

    advance_phase(3, 4, dry_run)
    log_ok(f"Phase 3 complete in {timer.elapsed_str()}")


def _generate_storyboards(dry_run: bool) -> None:
    """Generate scene storyboard images before frame composition.

    Reads storyboard prompt JSONs assembled in Phase 2/3, generates a multi-panel
    storyboard image per scene via sw_generate_sceneboard --all. These become the
    first reference image for every frame in that scene, ensuring visual consistency.
    """
    storyboard_prompt_dir = PROJECT_DIR / "frames" / "storyboard_prompts"
    storyboards_dir = PROJECT_DIR / "frames" / "storyboards"

    if not storyboard_prompt_dir.exists() or not list(storyboard_prompt_dir.glob("*_storyboard.json")):
        log_warn("No storyboard prompts found — skipping storyboard generation")
        return

    prompt_count = len(list(storyboard_prompt_dir.glob("*_storyboard.json")))
    log(f"Generating {prompt_count} scene storyboards...")

    if dry_run:
        log(f"[DRY-RUN] Would generate {prompt_count} storyboards", YELLOW)
        return

    storyboards_dir.mkdir(parents=True, exist_ok=True)

    import subprocess as _sp
    gen_result = _sp.run(
        [sys.executable, str(SKILLS_DIR / "sw_generate_sceneboard"),
         "--all", "--project-dir", str(PROJECT_DIR)],
        cwd=str(PROJECT_DIR), capture_output=True, text=True, timeout=600,
        env={**os.environ, "SKILLS_DIR": str(SKILLS_DIR), "PROJECT_DIR": str(PROJECT_DIR)},
    )
    if gen_result.stdout:
        for line in gen_result.stdout.strip().split("\n"):
            log(f"  {line}")
    if gen_result.returncode != 0:
        log_warn(f"Storyboard generation had failures (exit {gen_result.returncode})")
        if gen_result.stderr:
            log_warn(gen_result.stderr[:500])
    else:
        generated = len(list(storyboards_dir.glob("*.png")))
        log_ok(f"Generated {generated}/{prompt_count} scene storyboards")

    # Re-assemble prompts so frame ref_images now include storyboard paths
    log("Re-assembling prompts with storyboard references...")
    _sp.run([sys.executable, str(SKILLS_DIR / "graph_assemble_prompts"),
             "--project-dir", str(PROJECT_DIR)], cwd=str(PROJECT_DIR))
    log_ok("Prompts re-assembled with storyboard references")


def phase_4_production(dry_run: bool, phase_timers: dict,
                       skip_tts: bool = False) -> None:
    """Phase 4 -- Storyboard generation + Composition Verifier generates composed frames."""
    log_header("PHASE 4 -- Composition Verification")
    timer = Timer()
    phase_timers["phase_4"] = timer

    if skip_tts:
        log_warn("--skip-tts is deprecated and has no effect. The TTS stage was removed; timing is derived from dialogue-aware prompt assembly.")

    # Step 1: Generate scene storyboards BEFORE frame composition
    log("--- Phase 4a: Scene storyboard generation ---")
    _generate_storyboards(dry_run)

    # Step 2: Composition Verifier generates individual frames (with storyboard refs)
    log("--- Phase 4b: Frame composition ---")
    result = run_agent("composition_verifier",
                       str(PROMPTS_DIR / "composition_verifier.md"),
                       dry_run=dry_run)
    check_agent_result("composition_verifier", result, timer)

    created_files = []
    if not dry_run:
        base = PROJECT_DIR
        timeline = base / "logs" / "composition_verifier" / "timeline.json"
        if not timeline.exists():
            log_warn("timeline.json not found -- composition_verifier may not "
                     "have completed fully.")
        else:
            log_ok(f"timeline.json found ({timeline.stat().st_size} bytes)")
        log("Files created by Phase 4:")
        list_dir_files(base / "frames" / "composed")
        created_files = collect_files_in(base / "frames" / "composed")
        save_phase_report(4, timer, "composition_verifier", result, created_files)

        # Quality gate
        if not run_quality_gate(4, base):
            log_warn("Phase 4 quality gate FAILED — re-running composition verifier (attempt 2/2)...")
            timer2 = Timer()
            result2 = run_agent("composition_verifier",
                                str(PROMPTS_DIR / "composition_verifier.md"),
                                dry_run=dry_run)
            check_agent_result("composition_verifier", result2, timer2)
            if not run_quality_gate(4, base):
                log_warn("Phase 4 quality gate still failing after retry — proceeding with warnings")

    advance_phase(4, 5, dry_run)
    log_ok(f"Phase 4 complete in {timer.elapsed_str()}")


def phase_4_production_parallel(dry_run: bool, phase_timers: dict,
                                 num_workers: int = 8) -> None:
    """Phase 4 -- Parallel Production Coordination.

    Splits frames into chunks and spawns parallel Opus agents.
    Each worker handles a subset of frames for composition and timing metadata.
    """
    log_header("PHASE 4 -- Production Coordination (PARALLEL)")
    timer = Timer()
    phase_timers["phase_4"] = timer
    base = PROJECT_DIR

    # Step 1: Generate scene storyboards BEFORE frame composition
    log("--- Phase 4a: Scene storyboard generation ---")
    _generate_storyboards(dry_run)

    # Step 2: Parallel frame composition
    log("--- Phase 4b: Frame composition (parallel) ---")

    # Read manifest to get frame list and split into chunks
    manifest = json.loads(MANIFEST_PATH.read_text())
    frames = manifest.get("frames", [])
    total_frames = len(frames)

    if total_frames == 0:
        log_warn("No frames in manifest — skipping parallel Phase 4")

    # Calculate chunk size and create worker assignments
    chunk_size = max(1, math.ceil(total_frames / num_workers))
    chunks = []
    for i in range(0, total_frames, chunk_size):
        chunk_frames = frames[i:i + chunk_size]
        frame_ids = [f["frameId"] for f in chunk_frames]
        scene_ids = sorted(set(f.get("sceneId", "") for f in chunk_frames))
        chunks.append({
            "worker_index": len(chunks),
            "frame_ids": frame_ids,
            "scene_ids": scene_ids,
            "start_idx": i,
            "end_idx": min(i + chunk_size, total_frames),
        })

    actual_workers = len(chunks)
    log(f"Splitting {total_frames} frames into {actual_workers} parallel workers "
        f"(~{chunk_size} frames each)")

    for c in chunks:
        log(f"  Worker {c['worker_index']}: frames {c['start_idx']}-{c['end_idx']-1} "
            f"({len(c['frame_ids'])} frames, scenes: {', '.join(c['scene_ids'])})")

    prompt_file = str(PROMPTS_DIR / "production_coordinator.md")

    def spawn_worker(chunk: dict) -> tuple[int, subprocess.CompletedProcess, Timer]:
        """Spawn a single production worker for a frame chunk."""
        idx = chunk["worker_index"]
        worker_id = f"production_worker_{idx:02d}"
        worker_timer = Timer()

        frame_id_list = ", ".join(chunk["frame_ids"])
        prefix = (
            f"IMPORTANT OVERRIDE — PARALLEL WORKER MODE:\n"
            f"You are worker {idx} of {actual_workers} in a parallel production run.\n"
            f"You are ONLY responsible for these specific frames: [{frame_id_list}]\n"
            f"Frame index range: {chunk['start_idx']} to {chunk['end_idx']-1} "
            f"(of {total_frames} total)\n"
            f"Scenes covered: {', '.join(chunk['scene_ids'])}\n\n"
            f"SCOPE RULES:\n"
            f"- There is no separate TTS or dialogue-audio stage in this pipeline\n"
            f"- ONLY compose frame images for YOUR frames\n"
            f"- Use the existing dialogue text and prompt metadata for duration planning on YOUR frames\n"
            f"- DO NOT generate audio files or images for frames outside your range\n"
            f"- DO NOT write timeline.json — the orchestrator will merge results\n"
            f"- DO write per-frame manifest updates via sw_queue_update as normal\n"
            f"- Your log directory: logs/production_coordinator/worker_{idx:02d}/\n"
            f"- Use that directory for your state.json and events.jsonl\n\n"
            f"Skip the timeline assembly step entirely — just process your frames "
            f"and exit. Another process will handle timeline merging.\n"
        )

        log(f"Spawning worker {idx} ({len(chunk['frame_ids'])} frames)...")
        result = run_agent(worker_id, prompt_file, dry_run=dry_run,
                          prompt_prefix=prefix)
        return idx, result, worker_timer

    # Spawn all workers in parallel
    results: dict[int, tuple[subprocess.CompletedProcess, Timer]] = {}
    failed_workers: list[int] = []

    if dry_run:
        for chunk in chunks:
            idx, result, wtimer = spawn_worker(chunk)
            results[idx] = (result, wtimer)
    else:
        with ThreadPoolExecutor(max_workers=actual_workers) as executor:
            futures = {
                executor.submit(spawn_worker, chunk): chunk["worker_index"]
                for chunk in chunks
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    widx, result, wtimer = future.result()
                    results[widx] = (result, wtimer)
                    log(f"Worker {widx} finished in {wtimer.elapsed_str()} "
                        f"(exit={result.returncode})")
                    if result.returncode != 0:
                        log_err(f"Worker {widx} failed (exit={result.returncode})")
                        if result.stderr:
                            print(f"\n--- Worker {widx} STDERR ---\n"
                                  f"{result.stderr[-2000:]}\n--- END ---\n",
                                  flush=True)
                        failed_workers.append(widx)
                except Exception as e:
                    log_err(f"Worker {idx} raised exception: {e}")
                    failed_workers.append(idx)

    # Report results
    total_ok = actual_workers - len(failed_workers)
    log(f"Parallel production complete: {total_ok}/{actual_workers} workers succeeded")

    if failed_workers:
        log_warn(f"Failed workers: {failed_workers}")

    if not dry_run:
        # Now run a single timeline assembly agent
        log("Spawning timeline assembler...")
        timeline_prefix = (
            "IMPORTANT OVERRIDE — TIMELINE ASSEMBLY ONLY:\n"
            "All frame images have already been generated by "
            "parallel workers. Your ONLY job is:\n"
            "1. Read all frame images in frames/composed/ and the assembled prompt JSON files\n"
            "2. Read dialogue.json for duration and delivery context\n"
            "3. Build timeline.json with correct durations and ordering\n"
            "4. Queue the final bulk manifest update\n"
            "5. Write your state.json\n\n"
            "DO NOT regenerate any images.\n"
            "DO NOT run any TTS, dialogue-audio, or composition workstreams.\n"
            "ONLY build the timeline metadata.\n"
        )
        assembler_timer = Timer()
        assembler_result = run_agent("timeline_assembler", prompt_file,
                                     dry_run=dry_run, prompt_prefix=timeline_prefix)
        log(f"Timeline assembler finished in {assembler_timer.elapsed_str()} "
            f"(exit={assembler_result.returncode})")

        if assembler_result.returncode != 0:
            log_err("Timeline assembler failed")
            if assembler_result.stderr:
                print(f"\n--- STDERR ---\n{assembler_result.stderr[-4000:]}\n--- END ---\n",
                      flush=True)

        # File listing
        log("Files created by Phase 4 (parallel):")
        list_dir_files(base / "frames" / "composed")
        created_files = collect_files_in(base / "frames" / "composed")
        save_phase_report(4, timer, "production_coordinator_parallel",
                         assembler_result, created_files)

        # Quality gate
        if not run_quality_gate(4, base):
            log_warn("Phase 4 quality gate FAILED after parallel run — "
                     "proceeding with warnings")

    advance_phase(4, 5, dry_run)
    log_ok(f"Phase 4 (parallel) complete in {timer.elapsed_str()}")


def phase_5_video(dry_run: bool, phase_timers: dict) -> None:
    """Phase 5 -- Video Verifier generates clips from pre-built prompts."""
    log_header("PHASE 5 -- Video Verification")
    log_warn("This phase may take 20-60 minutes depending on clip count.")
    timer = Timer()
    phase_timers["phase_5"] = timer

    result = run_agent("video_verifier", str(PROMPTS_DIR / "video_verifier.md"),
                       dry_run=dry_run, model=DEFAULT_MODEL)
    check_agent_result("video_verifier", result, timer)

    created_files = []
    if not dry_run:
        base = PROJECT_DIR
        clips_dir = base / "video" / "clips"
        clips = list(clips_dir.glob("*.mp4"))
        if not clips:
            log_warn("No video clips found in video/clips/ after Phase 5 — continuing")
        log_ok(f"{len(clips)} video clip(s) found in video/clips/")
        log("Clips created by Phase 5:")
        list_dir_files(clips_dir)
        created_files = collect_files_in(clips_dir)
        save_phase_report(5, timer, "video_verifier", result, created_files)

        # Quality gate
        if not run_quality_gate(5, base):
            log_warn("Phase 5 quality gate FAILED — re-running video verifier (attempt 2/2)...")
            timer2 = Timer()
            result2 = run_agent("video_verifier", str(PROMPTS_DIR / "video_verifier.md"),
                                dry_run=dry_run, model=DEFAULT_MODEL)
            check_agent_result("video_verifier", result2, timer2)
            if not run_quality_gate(5, base):
                log_warn("Phase 5 quality gate still failing after retry — proceeding with warnings")

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
        result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                                timeout=3600)
    except subprocess.TimeoutExpired:
        log_err(f"{step_label} timed out after 3600s — continuing")
        return subprocess.CompletedProcess(cmd, returncode=-1, stdout="", stderr="TIMEOUT")
    if result.returncode != 0:
        log_err(f"{step_label} failed (exit={result.returncode})")
        if result.stderr:
            print(result.stderr[-3000:], flush=True)
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
        return float(f.get("timelineStart", f.get("sequenceIndex", 0)))

    sorted_frames = sorted(frames, key=frame_sort_key)

    ordered_clips: list[Path] = []
    for frame in sorted_frames:
        seq = frame.get("sequenceIndex", "")
        fid = frame.get("frameId", "")
        # Try chunk pattern first (multi-chunk clips)
        chunk_pattern = list(clips_dir.glob(f"{seq}_{fid}_c*.mp4"))
        if chunk_pattern:
            ordered_clips.extend(sorted(chunk_pattern))
        elif seq:
            clip = clips_dir / f"{seq}_{fid}.mp4"
            ordered_clips.append(clip)
        else:
            # sequenceIndex missing — glob for any file ending with _{fid}.mp4
            fallback = sorted(clips_dir.glob(f"*_{fid}.mp4"))
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
        "phase_1": base / "creative_output",
        "phase_2 (cast)": base / "cast",
        "phase_2 (locs)": base / "locations",
        "phase_2 (props)": base / "props",
        "phase_3 (assets)": base / "assets",
        "phase_3 (frames)": base / "frames",
        "phase_4 (audio)": base / "audio",
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

    # Ensure log directories exist
    PIPELINE_LOGS_DIR.mkdir(parents=True, exist_ok=True)

    pipeline_timer = Timer()
    phase_timers: dict[str, Timer] = {}

    # Start and verify server (needed even for single-phase runs -- skills call it)
    start_server(dry_run)
    wait_for_server(dry_run)

    export_path: str | None = None

    try:
        if only_phase is not None:
            # Single-phase mode: verify prerequisites first
            log(f"Running single phase: {only_phase}")
            if not dry_run:
                verify_prerequisites(only_phase)

            match only_phase:
                case 0:
                    phase_0_verify(dry_run)
                case 1:
                    phase_1_narrative(dry_run, phase_timers)
                case 2:
                    phase_2_morpheus(dry_run, phase_timers)
                case 3:
                    phase_3_assets(dry_run, phase_timers)
                case 4:
                    if parallel_p4 > 0:
                        phase_4_production_parallel(dry_run, phase_timers,
                                                     num_workers=parallel_p4)
                    else:
                        phase_4_production(dry_run, phase_timers,
                                           skip_tts=skip_tts)
                case 5:
                    phase_5_video(dry_run, phase_timers)
                case 6:
                    export_path = phase_6_export(dry_run, phase_timers)
        else:
            # Full pipeline
            phase_0_verify(dry_run)
            phase_1_narrative(dry_run, phase_timers)
            phase_2_morpheus(dry_run, phase_timers)
            phase_3_assets(dry_run, phase_timers)
            if parallel_p4 > 0:
                phase_4_production_parallel(dry_run, phase_timers,
                                             num_workers=parallel_p4)
            else:
                phase_4_production(dry_run, phase_timers,
                                   skip_tts=skip_tts)
            phase_5_video(dry_run, phase_timers)
            export_path = phase_6_export(dry_run, phase_timers)

    finally:
        stop_server()

    print_summary(pipeline_timer, phase_timers, export_path)


if __name__ == "__main__":
    main()
