#!/usr/bin/env python3
"""ScreenWire AI — Pipeline Checkpoint Tool

Check pipeline state, fix manifest phase status, or reset a phase.

Usage:
  python3 checkpoint.py --project sw_test002_blackhawk-jungle status
  python3 checkpoint.py --project sw_test002_blackhawk-jungle fix-phase 1 complete
  python3 checkpoint.py --project sw_test002_blackhawk-jungle fix-phase 2 ready
  python3 checkpoint.py --project sw_test002_blackhawk-jungle reset-phase 3
  python3 checkpoint.py --project sw_test002_blackhawk-jungle files
"""

import argparse
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

APP_DIR = Path(__file__).resolve().parent


def get_project_dir(project: str) -> Path:
    return APP_DIR / "projects" / project


def read_manifest(project_dir: Path) -> dict:
    mp = project_dir / "project_manifest.json"
    if not mp.exists():
        print(f"ERROR: No manifest at {mp}")
        sys.exit(1)
    return json.loads(mp.read_text())


def write_manifest(project_dir: Path, manifest: dict) -> None:
    mp = project_dir / "project_manifest.json"
    manifest["version"] = manifest.get("version", 0) + 1
    manifest["updatedAt"] = datetime.now(timezone.utc).isoformat()
    mp.write_text(json.dumps(manifest, indent=2))
    print(f"Manifest written (v{manifest['version']})")


def cmd_status(project_dir: Path) -> None:
    m = read_manifest(project_dir)
    print(f"Project: {m.get('projectName', '?')} ({m.get('projectId', '?')})")
    print(f"Status:  {m.get('status', '?')}")
    print(f"Version: {m.get('version', '?')}")
    print()
    print("Phase Status:")
    for i in range(7):
        key = f"phase_{i}"
        phase = m.get("phases", {}).get(key, {})
        status = phase.get("status", "unknown")
        completed = phase.get("completedAt", "")
        marker = "✓" if status == "complete" else ("→" if status == "ready" else "·")
        print(f"  {marker} Phase {i}: {status}  {completed}")
    print()
    print(f"Cast:      {len(m.get('cast', []))} entries")
    print(f"Locations: {len(m.get('locations', []))} entries")
    print(f"Props:     {len(m.get('props', []))} entries")
    print(f"Frames:    {len(m.get('frames', []))} entries")

    # Count files per output area
    print()
    print("Output Files:")
    areas = {
        "creative_output": "creative_output/**/*",
        "cast profiles": "cast/*.json",
        "location profiles": "locations/*.json",
        "prop profiles": "props/*.json",
        "cast composites": "cast/composites/*",
        "location images": "locations/primary/*",
        "composed frames": "frames/composed/*",
        "dialogue audio": "audio/dialogue/**/*.mp3",
        "video clips": "video/clips/*.mp4",
        "video export": "video/export/*",
    }
    for label, glob_pat in areas.items():
        files = list(project_dir.glob(glob_pat))
        if files:
            total_kb = sum(f.stat().st_size for f in files if f.is_file()) / 1024
            print(f"  {label}: {len(files)} file(s), {total_kb:.0f}KB total")


def cmd_fix_phase(project_dir: Path, phase_num: int, new_status: str) -> None:
    m = read_manifest(project_dir)
    key = f"phase_{phase_num}"
    if key not in m.get("phases", {}):
        m.setdefault("phases", {})[key] = {}
    m["phases"][key]["status"] = new_status
    if new_status == "complete":
        m["phases"][key]["completedAt"] = datetime.now(timezone.utc).isoformat()
    # Also set next phase to ready if marking complete
    if new_status == "complete" and phase_num < 6:
        next_key = f"phase_{phase_num + 1}"
        m.setdefault("phases", {})[next_key] = m.get("phases", {}).get(next_key, {})
        m["phases"][next_key]["status"] = "ready"
        print(f"Phase {phase_num}: complete, Phase {phase_num + 1}: ready")
    else:
        print(f"Phase {phase_num}: {new_status}")
    write_manifest(project_dir, m)


def cmd_reset_phase(project_dir: Path, phase_num: int) -> None:
    m = read_manifest(project_dir)
    key = f"phase_{phase_num}"
    m.setdefault("phases", {})[key] = {"status": "ready"}
    # Reset all subsequent phases to pending
    for i in range(phase_num + 1, 7):
        m["phases"][f"phase_{i}"] = {"status": "pending"}
    print(f"Phase {phase_num} reset to 'ready', phases {phase_num+1}-6 reset to 'pending'")
    write_manifest(project_dir, m)


def cmd_files(project_dir: Path) -> None:
    """List all non-empty output files with sizes."""
    for root, dirs, files in os.walk(project_dir):
        # Skip dispatch and dead_letters
        dirs[:] = [d for d in dirs if d not in ("dead_letters", "__pycache__")]
        for f in sorted(files):
            fp = Path(root) / f
            if fp.stat().st_size > 0:
                rel = fp.relative_to(project_dir)
                size = fp.stat().st_size
                if size > 1024 * 1024:
                    size_str = f"{size / (1024*1024):.1f}MB"
                elif size > 1024:
                    size_str = f"{size / 1024:.0f}KB"
                else:
                    size_str = f"{size}B"
                print(f"  {rel}  ({size_str})")


def main():
    parser = argparse.ArgumentParser(description="ScreenWire AI Pipeline Checkpoint Tool")
    parser.add_argument("--project", required=True, help="Project directory name")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Show pipeline status")
    sub.add_parser("files", help="List all output files")

    fix = sub.add_parser("fix-phase", help="Set a phase status")
    fix.add_argument("phase", type=int)
    fix.add_argument("new_status", choices=["ready", "complete", "pending", "in_progress"])

    reset = sub.add_parser("reset-phase", help="Reset a phase (and all subsequent)")
    reset.add_argument("phase", type=int)

    args = parser.parse_args()
    project_dir = get_project_dir(args.project)

    if not project_dir.exists():
        print(f"ERROR: Project not found: {project_dir}")
        sys.exit(1)

    if args.command == "status":
        cmd_status(project_dir)
    elif args.command == "files":
        cmd_files(project_dir)
    elif args.command == "fix-phase":
        cmd_fix_phase(project_dir, args.phase, args.new_status)
    elif args.command == "reset-phase":
        cmd_reset_phase(project_dir, args.phase)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
