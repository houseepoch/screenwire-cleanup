#!/usr/bin/env python3
"""Create a new ScreenWire pipeline project from the template scaffold.

Usage:
    python3 create_project.py --name "My Story" --id my_story_001
    python3 create_project.py --name "My Story" --id my_story_001 --seed /path/to/pitch.md
    python3 create_project.py --name "My Story" --id my_story_001 --seed /path/to/pitch.md \
        --creative-freedom creative --frame-budget 220 --media-style chiaroscuro_live
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from screenwire_contracts import (
    FRAME_BUDGET_PRESETS,
    creative_freedom_contract,
    default_dialogue_workflow,
    normalize_frame_budget,
)

APP_DIR = Path(os.getenv("SCREENWIRE_APP_ROOT", Path(__file__).resolve().parent)).resolve()
PROJECTS_DIR = Path(os.getenv("SCREENWIRE_PROJECTS_ROOT", APP_DIR / "projects")).resolve()
TEMPLATE_DIR = Path(os.getenv("SCREENWIRE_TEMPLATE_ROOT", PROJECTS_DIR / "_template")).resolve()

# ---------------------------------------------------------------------------
# Creative freedom tiers
# ---------------------------------------------------------------------------

CREATIVE_FREEDOM_TIERS = ("strict", "balanced", "creative", "unbounded")

# ---------------------------------------------------------------------------
# Legacy size aliases — translated into nominal frame budgets
# ---------------------------------------------------------------------------

PROJECT_SIZES = FRAME_BUDGET_PRESETS

# ---------------------------------------------------------------------------
# Media style → image generation prefix
# ---------------------------------------------------------------------------

MEDIA_STYLE_PREFIX = {
    "new_digital_anime":  "anime modern, a high-fidelity, polished 2D digital anime illustration aesthetic with clean, defined linework, smooth gradient shading, and advanced photorealistic material rendering, featuring a high-contrast palette. ",
    "live_retro_grain":   "live action- Captured using a refined, fine-grain vintage analog film emulation, defined by diffused, shadowless studio portraiture lighting, an intentionally warm color grade saturating beige textiles and skin tones. ",
    "chiaroscuro_live":   "live action, A moody, high-contrast cinematic film aesthetic defined by dramatic chiaroscuro lighting driven by warm, glowing practical sources, featuring a rich color grade of deep crimsons and amber highlights that contrast sharply against cool, desaturated blue ambient moonlight, finished with crushed black levels, enveloping heavy shadows, and a subtle 35mm film grain. ",
    "chiaroscuro_3d":     "3d computer generated graphic art unreal game play render, A moody, high-contrast aesthetic defined by dramatic chiaroscuro lighting driven by warm, glowing practical sources, featuring a rich color grade of deep crimsons and amber highlights that contrast sharply against cool, desaturated blue ambient moonlight, finished with crushed black levels, enveloping heavy shadows. ",
    "chiaroscuro_anime":  "anime modern, a high-fidelity, polished 2D digital anime illustration aesthetic A moody, high-contrast aesthetic defined by dramatic chiaroscuro lighting driven by warm, glowing practical sources, featuring a rich color grade of deep crimsons and amber highlights that contrast sharply against cool, desaturated blue ambient. ",
    "black_ink_anime":    "anime, gritty, 2D cel-shaded animation aesthetic defined by thick, variable-weight black ink outlines and stark, high-contrast hard shadows using pure black blocking, featuring a desaturated foreground color palette set against a stylized retro broadcast film grain. ",
    "live_soft_light":    "live action, A bright, nostalgic 35mm cinematic film aesthetic characterized by very soft, diffused naturalistic lighting and a shallow depth of field, featuring a muted pastel color palette with creamy, pristine skin tones, finished with a gentle film grain and a warm, inviting vintage studio grade. ",
    "live_clear":         "live action, stark, high-contrast modern digital photography aesthetic defined by dramatic, directional overhead spotlighting that intensely isolates the luminous subject. The color palette is strictly minimalist, emphasizing stark whites and natural warm tones that sharply contrast with the deep, light-absorbing shadows, captured with ultra-sharp clinical resolution and pristine clarity. ",
}

# Display name → slug mapping for UI/CLI
MEDIA_STYLE_DISPLAY = {
    "new_digital_anime":  "New Digital Anime",
    "live_retro_grain":   "Live Retro Grain",
    "chiaroscuro_live":   "Chiaroscuro Live",
    "chiaroscuro_3d":     "Chiaroscuro 3d",
    "chiaroscuro_anime":  "Chiaroscuro Anime",
    "black_ink_anime":    "Black Ink Anime",
    "live_soft_light":    "Live Soft Light",
    "live_clear":         "Live Clear",
}


def slugify(name: str) -> str:
    return name.lower().replace(" ", "-").replace("'", "").replace('"', "")


def create_project(
    project_id: str,
    project_name: str,
    seed_file: Path | None = None,
    creative_freedom: str = "balanced",
    frame_budget: int | str | None = None,
    media_style: str = "live_clear",
    pipeline_type: str = "story_upload",
) -> Path:
    project_dir = PROJECTS_DIR / project_id

    if project_dir.exists():
        print(f"ERROR: Project directory already exists: {project_dir}")
        sys.exit(1)

    if not TEMPLATE_DIR.exists():
        print(f"ERROR: Template directory not found: {TEMPLATE_DIR}")
        sys.exit(1)

    # Validate creative freedom
    if creative_freedom not in CREATIVE_FREEDOM_TIERS:
        print(
            f"ERROR: creative freedom must be one of {CREATIVE_FREEDOM_TIERS}, got '{creative_freedom}'"
        )
        sys.exit(1)

    # Validate frame budget
    try:
        normalized_budget = normalize_frame_budget(frame_budget)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)

    # Validate media style
    if media_style not in MEDIA_STYLE_PREFIX:
        print(f"ERROR: Media style must be one of {list(MEDIA_STYLE_PREFIX.keys())}, got '{media_style}'")
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

    # Resolve source files
    source_files = []
    if seed_file:
        source_files.append(f"source_files/{Path(seed_file).name}")
    else:
        source_files.append("source_files/pitch.md")

    # Write onboarding config with full detail
    freedom = creative_freedom_contract(creative_freedom)
    onboarding = {
        "projectName": project_name,
        "projectId": f"sw_lg_{project_id}",
        "creativeFreedom": creative_freedom,
        "creativeFreedomPermission": freedom["permission"],
        "creativeFreedomFailureModes": freedom["failure_modes"],
        "dialoguePolicy": freedom["dialogue_policy"],
        "dialogueWorkflow": default_dialogue_workflow(),
        "frameBudget": "auto" if normalized_budget is None else normalized_budget,
        "mediaStyle": media_style,
        "mediaStylePrefix": MEDIA_STYLE_PREFIX[media_style],
        "pipeline": pipeline_type,
        "aspectRatio": "16:9",
        "style": [],
        "genre": [],
        "mood": [],
        "extraDetails": "",
        "sourceFiles": source_files,
    }
    onboarding_path = project_dir / "source_files" / "onboarding_config.json"
    onboarding_path.write_text(json.dumps(onboarding, indent=2) + "\n", encoding="utf-8")

    # Copy seed file if provided
    if seed_file:
        seed_path = Path(seed_file).resolve()
        if not seed_path.exists():
            print(f"WARNING: Seed file not found: {seed_path}")
        else:
            dest = project_dir / "source_files" / seed_path.name
            shutil.copy2(seed_path, dest)
            print(f"  Seed file copied: {dest.name}")

    print(f"Project created: {project_dir}")
    print(f"  ID:         {project_id}")
    print(f"  Name:       {project_name}")
    print(f"  Slug:       {slug}")
    print(f"  Creative:   {creative_freedom} — {freedom['permission'][:60]}...")
    print(
        "  Frame Cap:  "
        + (
            "auto (uncapped, maximum-quality full coverage)"
            if normalized_budget is None
            else str(normalized_budget)
        )
    )
    print(f"  Style:      {MEDIA_STYLE_DISPLAY.get(media_style, media_style)}")
    print(f"\nNext steps:")
    print(f"  1. Add your source material to {project_dir}/source_files/")
    print(f"  2. Run: python3 run_pipeline.py --project {project_id}")

    return project_dir


def main():
    parser = argparse.ArgumentParser(description="Create a new ScreenWire pipeline project")
    parser.add_argument("--name", required=True, help="Human-readable project name")
    parser.add_argument("--id", required=True, help="Project directory ID (e.g., orchids_gambit_001)")
    parser.add_argument("--seed", default=None, help="Path to source/pitch file to copy into project")
    parser.add_argument("--creative-freedom", default="balanced",
                        choices=list(CREATIVE_FREEDOM_TIERS),
                        help="Creative freedom tier (strict, balanced, creative, unbounded)")
    parser.add_argument(
        "--frame-budget",
        default="auto",
        help=(
            "Maximum frame count as a positive integer, or 'auto' for uncapped, "
            "highest-effort full-story coverage"
        ),
    )
    parser.add_argument(
        "--size",
        default=None,
        choices=list(PROJECT_SIZES.keys()),
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--media-style", default="live_clear",
                        choices=list(MEDIA_STYLE_PREFIX.keys()),
                        help="Media style determines image generation prefix")
    parser.add_argument("--pipeline-type", default="story_upload",
                        choices=["story_upload", "pitch_idea", "music_video"],
                        help="Pipeline entry type")
    args = parser.parse_args()

    frame_budget = args.frame_budget
    if args.size and str(frame_budget).strip().lower() == "auto":
        frame_budget = PROJECT_SIZES[args.size]

    create_project(
        project_id=args.id,
        project_name=args.name,
        seed_file=Path(args.seed) if args.seed else None,
        creative_freedom=args.creative_freedom,
        frame_budget=frame_budget,
        media_style=args.media_style,
        pipeline_type=args.pipeline_type,
    )


if __name__ == "__main__":
    main()
