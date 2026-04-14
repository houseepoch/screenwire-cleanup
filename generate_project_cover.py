#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import run_pipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a project cover image for an existing ScreenWire project.")
    parser.add_argument(
        "--project",
        required=True,
        help="Project directory name under projects/.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parent
    project_dir = repo_root / "projects" / args.project
    if not project_dir.exists():
        print(f"Project not found: {project_dir}", file=sys.stderr)
        return 1

    run_pipeline._generate_project_cover_art(project_dir, dry_run=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
