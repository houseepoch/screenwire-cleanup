#!/usr/bin/env python3
"""Create a new ScreenWire pipeline project from the template scaffold.

Usage:
    python3 create_project.py --name "My Story" --id my_story_001
    python3 create_project.py --name "My Story" --id my_story_001 --seed /path/to/pitch.md
    python3 create_project.py --name "My Story" --id my_story_001 --seed /path/to/pitch.md \
        --stickiness 3 --size short --media-style chiaroscuro_live
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

# ---------------------------------------------------------------------------
# Stickiness scale (1-5) — creative freedom levels
# ---------------------------------------------------------------------------

STICKINESS_PERMISSIONS = {
    1: "Reformat. Restructure and rewrite the source material into operational format without altering story content. No new characters, scenes, dialogue, or events. The source dictates what exists — you dictate how it reads on the page.",
    2: "Remaster. Adhere to the source material faithfully while enriching quality. Smooth transitions, add sensory detail, deepen descriptions, fill gaps that make scenes feel complete. Same story, higher fidelity. No new plot elements, characters, or narrative departures.",
    3: "Expand. Follow the source material's direction but round out incomplete areas. Add transitional scenes, supporting details, and environmental context the source implies but doesn't show. All additions must serve what's already demonstrated — supporting information, not new story.",
    4: "Reimagine. Use the source's story, narrative, and themes as a creative foundation. You may introduce new cast, locations, and writing to serve existing arcs. The original tone, themes, and trajectory are respected — but the canvas is wider.",
    5: "Create. The source is a seed idea. Write an original story inspired by its guidance, introducing rich characters, props, locations, and story events to fill out the targeted output size. Full creative ownership.",
}

# ---------------------------------------------------------------------------
# Project size definitions — frame count ranges
# ---------------------------------------------------------------------------

PROJECT_SIZES = {
    "short":      {"label": "Short",        "frame_range": [10, 20],    "scene_range": [1, 3]},
    "short_film": {"label": "Short Film",   "frame_range": [50, 125],   "scene_range": [5, 15]},
    "televised":  {"label": "Televised",    "frame_range": [200, 300],  "scene_range": [20, 40]},
    "feature":    {"label": "Feature",      "frame_range": [750, 1250], "scene_range": [60, 120]},
}

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
    stickiness: int = 3,
    size: str = "short",
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

    # Validate stickiness
    if stickiness not in range(1, 6):
        print(f"ERROR: Stickiness must be 1-5, got {stickiness}")
        sys.exit(1)

    # Validate size
    if size not in PROJECT_SIZES:
        print(f"ERROR: Size must be one of {list(PROJECT_SIZES.keys())}, got '{size}'")
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
    size_def = PROJECT_SIZES[size]
    onboarding = {
        "projectName": project_name,
        "projectId": f"sw_lg_{project_id}",
        "stickinessLevel": stickiness,
        "stickinessPermission": STICKINESS_PERMISSIONS[stickiness],
        "outputSize": size,
        "outputSizeLabel": size_def["label"],
        "frameRange": size_def["frame_range"],
        "sceneRange": size_def["scene_range"],
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
    print(f"  Stickiness: {stickiness} — {STICKINESS_PERMISSIONS[stickiness][:60]}...")
    print(f"  Size:       {size} ({size_def['frame_range'][0]}-{size_def['frame_range'][1]} frames)")
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
    parser.add_argument("--stickiness", type=int, default=3,
                        choices=[1, 2, 3, 4, 5],
                        help="Creative freedom (1=reformat, 2=remaster, 3=expand, 4=reimagine, 5=create)")
    parser.add_argument("--size", default="short",
                        choices=list(PROJECT_SIZES.keys()),
                        help="Project size (short=10-20 frames, short_film=50-125, televised=200-300, feature=750-1250)")
    parser.add_argument("--media-style", default="live_clear",
                        choices=list(MEDIA_STYLE_PREFIX.keys()),
                        help="Media style determines image generation prefix")
    parser.add_argument("--pipeline-type", default="story_upload",
                        choices=["story_upload", "pitch_idea", "music_video"],
                        help="Pipeline entry type")
    args = parser.parse_args()

    create_project(
        project_id=args.id,
        project_name=args.name,
        seed_file=Path(args.seed) if args.seed else None,
        stickiness=args.stickiness,
        size=args.size,
        media_style=args.media_style,
        pipeline_type=args.pipeline_type,
    )


if __name__ == "__main__":
    main()
