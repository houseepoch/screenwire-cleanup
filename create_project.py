#!/usr/bin/env python3
"""Create a new ScreenWire pipeline project from the template scaffold.

Usage:
    python3 create_project.py --name "My Story" --id my_story_001
    python3 create_project.py --name "My Story" --id my_story_001 --seed /path/to/pitch.md
    python3 create_project.py --name "My Story" --id my_story_001 --seed /path/to/pitch.md \
        --stickiness 7 --size medium --media-type animation
"""

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
PROJECTS_DIR = APP_DIR / "projects"
TEMPLATE_DIR = PROJECTS_DIR / "_template"


def slugify(name: str) -> str:
    return name.lower().replace(" ", "-").replace("'", "").replace('"', "")


def create_project(
    project_id: str,
    project_name: str,
    seed_file: Path | None = None,
    stickiness: int = 5,
    size: str = "short",
    media_type: str = "live_action",
    pipeline_type: str = "pitch_idea",
) -> Path:
    project_dir = PROJECTS_DIR / project_id

    if project_dir.exists():
        print(f"ERROR: Project directory already exists: {project_dir}")
        sys.exit(1)

    if not TEMPLATE_DIR.exists():
        print(f"ERROR: Template directory not found: {TEMPLATE_DIR}")
        sys.exit(1)

    # Copy template
    shutil.copytree(TEMPLATE_DIR, project_dir)

    # Fill manifest placeholders
    manifest_path = project_dir / "project_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).isoformat()
    slug = slugify(project_name)

    manifest["projectId"] = f"sw_lg_{project_id}"
    manifest["projectName"] = project_name
    manifest["slug"] = slug
    manifest["phases"]["phase_0"]["completedAt"] = now
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    # Write onboarding config
    source_file_ref = "source_files/pitch.md"
    onboarding = {
        "stickiness": stickiness,
        "size": size,
        "media_type": media_type,
        "pipeline_type": pipeline_type,
        "source_file": source_file_ref,
    }
    onboarding_path = project_dir / "source_files" / "onboarding_config.json"
    onboarding_path.write_text(json.dumps(onboarding, indent=2) + "\n", encoding="utf-8")

    # Copy seed file if provided
    if seed_file:
        seed_path = Path(seed_file).resolve()
        if not seed_path.exists():
            print(f"WARNING: Seed file not found: {seed_path}")
        else:
            dest = project_dir / "source_files" / "pitch.md"
            shutil.copy2(seed_path, dest)
            print(f"  Seed file copied: {dest.name}")

    print(f"Project created: {project_dir}")
    print(f"  ID:   {project_id}")
    print(f"  Name: {project_name}")
    print(f"  Slug: {slug}")
    print(f"\nNext steps:")
    print(f"  1. Add your source material to {project_dir}/source_files/")
    print(f"  2. Run: python3 run_pipeline.py --project {project_id}")

    return project_dir


def main():
    parser = argparse.ArgumentParser(description="Create a new ScreenWire pipeline project")
    parser.add_argument("--name", required=True, help="Human-readable project name")
    parser.add_argument("--id", required=True, help="Project directory ID (e.g., orchids_gambit_001)")
    parser.add_argument("--seed", default=None, help="Path to source/pitch file to copy into project")
    parser.add_argument("--stickiness", type=int, default=5, help="Narrative stickiness (1-10, default: 5)")
    parser.add_argument("--size", default="short", choices=["short", "medium", "long"], help="Project size")
    parser.add_argument("--media-type", default="live_action",
                        choices=["live_action", "animation", "mixed"], help="Media type")
    parser.add_argument("--pipeline-type", default="pitch_idea",
                        choices=["pitch_idea", "screenplay", "storyboard"], help="Pipeline entry type")
    args = parser.parse_args()

    create_project(
        project_id=args.id,
        project_name=args.name,
        seed_file=Path(args.seed) if args.seed else None,
        stickiness=args.stickiness,
        size=args.size,
        media_type=args.media_type,
        pipeline_type=args.pipeline_type,
    )


if __name__ == "__main__":
    main()
