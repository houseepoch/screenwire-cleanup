#!/usr/bin/env python3
"""image_tagger.py — Stamp entity names onto generated images.

Overlays bold yellow text with black outline in the upper-right corner.
Used by the Sentinel watcher to auto-tag cast composites, location primaries,
and prop images as they are generated.

Can also be run standalone:
    python3 image_tagger.py --project-dir /path/to/project [--watch]
"""

import json
import os
import re
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_SIZE_RATIO = 0.04  # Font size as ratio of image height (4%)
MIN_FONT_SIZE = 18
MAX_FONT_SIZE = 48
PADDING = 12
CORNER_MARGIN = 16
OUTLINE_WIDTH = 3
TEXT_COLOR = (255, 255, 0)  # Yellow fill
OUTLINE_COLOR = (0, 0, 0)  # Black outline
BG_COLOR = (0, 0, 0, 140)  # Semi-transparent black background

# Directories to watch (relative to project root)
TAGGED_DIRS = {
    "cast/composites": "cast",
    "locations/primary": "location",
    "props/generated": "prop",
    "assets/active/mood": "mood",
}


# ---------------------------------------------------------------------------
# Core tagging function
# ---------------------------------------------------------------------------

def tag_image(image_path: Path, label: str) -> None:
    """Overlay label text on image in upper-right corner. Overwrites in place."""
    try:
        img = Image.open(image_path).convert("RGBA")
    except Exception as e:
        print(f"[ImageTagger] Cannot open {image_path.name}: {e}", file=sys.stderr)
        return

    w, h = img.size
    font_size = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, int(h * FONT_SIZE_RATIO)))

    try:
        font = ImageFont.truetype(FONT_PATH, font_size)
    except Exception:
        font = ImageFont.load_default()

    # Create overlay for semi-transparent background
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Measure text
    bbox = draw.textbbox((0, 0), label, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    # Position: upper-right corner with margin
    box_w = text_w + PADDING * 2
    box_h = text_h + PADDING * 2
    box_x = w - box_w - CORNER_MARGIN
    box_y = CORNER_MARGIN

    # Draw background box
    draw.rounded_rectangle(
        [box_x, box_y, box_x + box_w, box_y + box_h],
        radius=6,
        fill=BG_COLOR,
    )

    # Draw text with outline
    text_x = box_x + PADDING
    text_y = box_y + PADDING

    # Black outline (draw text offset in all directions)
    for dx in range(-OUTLINE_WIDTH, OUTLINE_WIDTH + 1):
        for dy in range(-OUTLINE_WIDTH, OUTLINE_WIDTH + 1):
            if dx == 0 and dy == 0:
                continue
            draw.text((text_x + dx, text_y + dy), label, font=font, fill=OUTLINE_COLOR)

    # Yellow fill
    draw.text((text_x, text_y), label, font=font, fill=TEXT_COLOR)

    # Composite and save
    tagged = Image.alpha_composite(img, overlay)

    # Save back — convert to RGB if original was JPEG/PNG without alpha
    original_format = image_path.suffix.lower()
    if original_format in (".jpg", ".jpeg"):
        tagged = tagged.convert("RGB")
        tagged.save(image_path, "JPEG", quality=95)
    else:
        tagged.save(image_path, "PNG")

    print(f"[ImageTagger] Tagged: {image_path.name} → \"{label}\"")


# ---------------------------------------------------------------------------
# Label resolution from filename and project data
# ---------------------------------------------------------------------------

def resolve_label(image_path: Path, entity_type: str, project_dir: Path) -> str:
    """Derive a human-readable label from the image filename and project profiles."""
    stem = image_path.stem  # e.g., "cast_001_prather_ref" or "loc_001_blackhawk_interior"

    if entity_type == "cast":
        # Try to read cast profile for character name
        cast_id = _extract_entity_id(stem, prefix="cast")
        profile_path = project_dir / "cast" / f"{cast_id}.json"
        if profile_path.exists():
            try:
                profile = json.loads(profile_path.read_text())
                return profile.get("name", cast_id)
            except Exception:
                pass
        # Fallback: clean up the stem
        return _humanize_id(cast_id)

    elif entity_type == "location":
        loc_id = _extract_entity_id(stem, prefix="loc")
        profile_path = project_dir / "locations" / f"{loc_id}.json"
        if profile_path.exists():
            try:
                profile = json.loads(profile_path.read_text())
                return profile.get("name", loc_id)
            except Exception:
                pass
        return _humanize_id(loc_id)

    elif entity_type == "prop":
        prop_id = _extract_entity_id(stem, prefix="prop")
        profile_path = project_dir / "props" / f"{prop_id}.json"
        if profile_path.exists():
            try:
                profile = json.loads(profile_path.read_text())
                return profile.get("name", prop_id)
            except Exception:
                pass
        return _humanize_id(prop_id)

    elif entity_type == "mood":
        return f"Mood Board {stem}"

    return stem


def _extract_entity_id(stem: str, prefix: str) -> str:
    """Extract entity ID from filename stem. e.g., 'cast_001_prather_ref' → 'cast_001_prather'."""
    # Remove common suffixes
    clean = re.sub(r'_(ref|primary|generated|gen)$', '', stem)
    return clean


def _humanize_id(entity_id: str) -> str:
    """Convert 'cast_001_prather' → 'Prather' or 'loc_001_blackhawk_interior' → 'Blackhawk Interior'."""
    # Remove prefix and number
    parts = entity_id.split("_")
    # Skip prefix (cast/loc/prop) and number (001)
    name_parts = []
    for p in parts:
        if p in ("cast", "loc", "prop", "mood") or re.match(r'^\d+$', p):
            continue
        name_parts.append(p.capitalize())
    return " ".join(name_parts) if name_parts else entity_id


# ---------------------------------------------------------------------------
# Batch tag all existing images in a project
# ---------------------------------------------------------------------------

def tag_all_project_images(project_dir: Path) -> int:
    """Tag all existing cast/location/prop/mood images. Returns count tagged."""
    count = 0
    for rel_dir, entity_type in TAGGED_DIRS.items():
        dir_path = project_dir / rel_dir
        if not dir_path.exists():
            continue
        for img_path in sorted(dir_path.glob("*.png")) + sorted(dir_path.glob("*.jpg")):
            label = resolve_label(img_path, entity_type, project_dir)
            tag_image(img_path, label)
            count += 1
    return count


# ---------------------------------------------------------------------------
# Watch mode — monitor directories for new images
# ---------------------------------------------------------------------------

def watch_project(project_dir: Path) -> None:
    """Watch project directories and tag new images as they appear."""
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        print("[ImageTagger] watchdog not installed, using polling fallback", file=sys.stderr)
        _poll_watch(project_dir)
        return

    class TagHandler(FileSystemEventHandler):
        def __init__(self, entity_type: str, proj_dir: Path):
            self.entity_type = entity_type
            self.proj_dir = proj_dir

        def on_created(self, event):
            if event.is_directory:
                return
            path = Path(event.src_path)
            if path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
                return
            # Wait briefly for file to be fully written
            time.sleep(0.5)
            if path.exists() and path.stat().st_size > 0:
                label = resolve_label(path, self.entity_type, self.proj_dir)
                tag_image(path, label)

    observer = Observer()
    for rel_dir, entity_type in TAGGED_DIRS.items():
        dir_path = project_dir / rel_dir
        dir_path.mkdir(parents=True, exist_ok=True)
        handler = TagHandler(entity_type, project_dir)
        observer.schedule(handler, str(dir_path), recursive=False)
        print(f"[ImageTagger] Watching: {rel_dir}/")

    observer.daemon = True
    observer.start()
    print("[ImageTagger] Watcher running. Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


def _poll_watch(project_dir: Path) -> None:
    """Fallback polling watcher if watchdog is not available."""
    seen: set[str] = set()

    # Initial scan
    for rel_dir, _ in TAGGED_DIRS.items():
        dir_path = project_dir / rel_dir
        if dir_path.exists():
            for f in dir_path.iterdir():
                seen.add(str(f))

    print("[ImageTagger] Polling watcher running (5s interval).")
    while True:
        time.sleep(5)
        for rel_dir, entity_type in TAGGED_DIRS.items():
            dir_path = project_dir / rel_dir
            if not dir_path.exists():
                continue
            for f in dir_path.iterdir():
                key = str(f)
                if key not in seen and f.suffix.lower() in (".png", ".jpg", ".jpeg"):
                    seen.add(key)
                    if f.stat().st_size > 0:
                        label = resolve_label(f, entity_type, project_dir)
                        tag_image(f, label)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Tag project images with entity names.")
    parser.add_argument("--project-dir", required=True, help="Path to project directory")
    parser.add_argument("--watch", action="store_true", help="Watch for new images and tag automatically")
    parser.add_argument("--file", help="Tag a single file with a given label")
    parser.add_argument("--label", help="Label for --file mode")
    args = parser.parse_args()

    project_dir = Path(args.project_dir)

    if args.file:
        label = args.label or Path(args.file).stem
        tag_image(Path(args.file), label)
    elif args.watch:
        # Tag existing first, then watch
        count = tag_all_project_images(project_dir)
        print(f"[ImageTagger] Tagged {count} existing images.")
        watch_project(project_dir)
    else:
        count = tag_all_project_images(project_dir)
        print(f"[ImageTagger] Tagged {count} images.")
