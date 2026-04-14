#!/usr/bin/env python3
"""image_tagger.py — Stamp entity names onto generated reference images.

Overlays a centered top glass label that matches the Morpheus UI aesthetic.
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

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_SIZE_RATIO = 0.038
MIN_FONT_SIZE = 20
MAX_FONT_SIZE = 48
PADDING_X = 18
PADDING_Y = 10
TOP_MARGIN = 18
PANEL_RADIUS = 18
LABEL_MAX_WIDTH_RATIO = 0.72
TEXT_COLOR = (248, 251, 255, 255)
TEXT_STROKE = (10, 16, 24, 220)
PANEL_FILL = (15, 24, 36, 132)
PANEL_GLOSS = (255, 255, 255, 26)
PANEL_BORDER = (255, 255, 255, 72)

# Directories to watch — ONLY reference images get tagged
TAGGED_DIRS = {
    "cast/composites": "cast",
    "locations/primary": "location",
    "props/generated": "prop",
    "assets/active/mood": "mood",
}

# Directories that must NEVER be tagged (scene frames, storyboards, etc.)
NEVER_TAG_DIRS = {
    "frames/composed",
    "frames/storyboards",
    "frames/prompts",
    "video/prompts",
    "video/rendered",
}


# ---------------------------------------------------------------------------
# Core tagging function
# ---------------------------------------------------------------------------

def _load_tag_manifest(project_dir: Path) -> dict:
    """Load the tag manifest tracking which files have been tagged."""
    manifest_path = project_dir / "logs" / "tagged_manifest.json"
    if manifest_path.exists():
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_tag_manifest(project_dir: Path, manifest: dict) -> None:
    """Persist the tag manifest."""
    manifest_path = project_dir / "logs" / "tagged_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def is_tagged(image_path: Path, project_dir: Path) -> bool:
    """Check whether an image has already been tagged (via manifest)."""
    manifest = _load_tag_manifest(project_dir)
    return str(image_path) in manifest


def _fit_font(draw: ImageDraw.ImageDraw, label: str, image_height: int, max_label_width: int) -> ImageFont.ImageFont:
    font_size = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, int(image_height * FONT_SIZE_RATIO)))
    while True:
        try:
            font = ImageFont.truetype(FONT_PATH, font_size)
        except Exception:
            return ImageFont.load_default()
        bbox = draw.textbbox((0, 0), label, font=font, stroke_width=2)
        text_w = bbox[2] - bbox[0]
        if text_w <= max_label_width or font_size <= MIN_FONT_SIZE:
            return font
        font_size -= 2


def tag_image(image_path: Path, label: str, project_dir: Path | None = None) -> bool:
    """Overlay label text in a centered top glass panel. Overwrites in place.

    Returns True on success, False on failure.
    If project_dir is provided, records the tag in the manifest to prevent
    double-tagging and enable verification.
    """
    # Skip if already tagged
    if project_dir is not None:
        manifest = _load_tag_manifest(project_dir)
        if str(image_path) in manifest:
            return True

    try:
        img = Image.open(image_path).convert("RGBA")
    except Exception as e:
        print(f"[ImageTagger] Cannot open {image_path.name}: {e}", file=sys.stderr)
        return False

    w, h = img.size
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _fit_font(draw, label, h, int(w * LABEL_MAX_WIDTH_RATIO) - PADDING_X * 2)
    bbox = draw.textbbox((0, 0), label, font=font, stroke_width=2)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    box_w = max(text_w + PADDING_X * 2, min(int(w * 0.26), int(w * LABEL_MAX_WIDTH_RATIO)))
    box_w = min(box_w, int(w * LABEL_MAX_WIDTH_RATIO))
    box_h = text_h + PADDING_Y * 2
    box_x = max(12, int((w - box_w) / 2))
    box_y = TOP_MARGIN
    box_rect = (box_x, box_y, box_x + box_w, box_y + box_h)

    blurred_region = img.crop(box_rect).filter(ImageFilter.GaussianBlur(radius=12))
    panel = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(panel)
    panel_draw.rounded_rectangle(
        [0, 0, box_w - 1, box_h - 1],
        radius=PANEL_RADIUS,
        fill=PANEL_FILL,
        outline=PANEL_BORDER,
        width=1,
    )
    panel_draw.rounded_rectangle(
        [2, 2, box_w - 3, max(8, int(box_h * 0.52))],
        radius=max(10, PANEL_RADIUS - 4),
        fill=PANEL_GLOSS,
    )
    blurred_region.alpha_composite(panel)
    img.alpha_composite(blurred_region, dest=(box_x, box_y))

    text_x = box_x + (box_w - text_w) / 2
    text_y = box_y + (box_h - text_h) / 2 - bbox[1]
    draw = ImageDraw.Draw(img)
    draw.text(
        (text_x, text_y),
        label,
        font=font,
        fill=TEXT_COLOR,
        stroke_width=2,
        stroke_fill=TEXT_STROKE,
    )

    # Save back — convert to RGB if original was JPEG/PNG without alpha
    original_format = image_path.suffix.lower()
    if original_format in (".jpg", ".jpeg"):
        img = img.convert("RGB")
        img.save(image_path, "JPEG", quality=95)
    else:
        img.save(image_path, "PNG")

    print(f"[ImageTagger] Tagged: {image_path.name} → \"{label}\"")

    # Record in manifest
    if project_dir is not None:
        manifest = _load_tag_manifest(project_dir)
        manifest[str(image_path)] = {
            "label": label,
            "tagged_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        _save_tag_manifest(project_dir, manifest)

    return True


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

def tag_all_project_images(project_dir: Path) -> tuple[int, set[str]]:
    """Tag all existing reference images (cast/location/prop/mood).

    Only tags files in TAGGED_DIRS. Scene frames and storyboards are never tagged.
    Skips images already recorded in the tag manifest (prevents double-overlay).

    Returns (count_tagged, set_of_tagged_absolute_paths).
    """
    tagged_paths: set[str] = set()
    failed_paths: list[str] = []
    for rel_dir, entity_type in TAGGED_DIRS.items():
        dir_path = project_dir / rel_dir
        if not dir_path.exists():
            continue
        for img_path in sorted(dir_path.glob("*.png")) + sorted(dir_path.glob("*.jpg")):
            # Safety: verify path is not in a forbidden directory
            try:
                rel = img_path.relative_to(project_dir)
                if any(str(rel).startswith(blocked) for blocked in NEVER_TAG_DIRS):
                    continue
            except ValueError:
                pass
            label = resolve_label(img_path, entity_type, project_dir)
            if tag_image(img_path, label, project_dir=project_dir):
                tagged_paths.add(str(img_path))
            else:
                failed_paths.append(str(img_path))

    if failed_paths:
        print(f"[ImageTagger] WARNING: {len(failed_paths)} image(s) failed to tag:", file=sys.stderr)
        for fp in failed_paths:
            print(f"  - {fp}", file=sys.stderr)

    return len(tagged_paths), tagged_paths


def verify_ref_images_tagged(project_dir: Path, ref_image_paths: list[str]) -> tuple[bool, list[str]]:
    """Verify that every reference image path exists and has been tagged.

    For any untagged image that falls within a TAGGED_DIR, attempt to tag it now.

    Returns (all_ok, list_of_problem_paths).
    """
    manifest = _load_tag_manifest(project_dir)
    problems: list[str] = []

    for ref in ref_image_paths:
        ref_path = Path(ref) if Path(ref).is_absolute() else project_dir / ref
        if not ref_path.exists():
            problems.append(f"MISSING: {ref}")
            continue

        # Check if already tagged
        if str(ref_path) in manifest:
            continue

        # Determine if this path is in a TAGGED_DIR (should have been tagged)
        try:
            rel = ref_path.relative_to(project_dir)
            rel_str = str(rel)
        except ValueError:
            continue  # Outside project — not our concern

        matched_type = None
        for tagged_dir, entity_type in TAGGED_DIRS.items():
            if rel_str.startswith(tagged_dir):
                matched_type = entity_type
                break

        if matched_type is None:
            continue  # Not in a tagged directory — no tag expected

        # Attempt to tag now
        label = resolve_label(ref_path, matched_type, project_dir)
        if tag_image(ref_path, label, project_dir=project_dir):
            print(f"[ImageTagger] Late-tagged: {ref_path.name} → \"{label}\"")
        else:
            problems.append(f"TAG_FAILED: {ref}")

    return len(problems) == 0, problems


# ---------------------------------------------------------------------------
# Non-blocking watcher for pipeline integration
# ---------------------------------------------------------------------------


def start_tag_watcher(project_dir: Path) -> "Observer | None":
    """Start a background watcher that auto-tags reference images as they arrive.

    Returns the Observer instance (call stop_tag_watcher() when done) or None
    if watchdog is not installed.  Tags existing images first, then watches.
    """
    # Tag existing images first
    count, _ = tag_all_project_images(project_dir)
    if count:
        print(f"[ImageTagger] Tagged {count} existing reference images.")

    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        print("[ImageTagger] watchdog not installed — auto-tagging disabled", file=sys.stderr)
        return None

    class _TagHandler(FileSystemEventHandler):
        def __init__(self, entity_type: str, proj_dir: Path):
            self.entity_type = entity_type
            self.proj_dir = proj_dir

        def _should_tag(self, path: Path) -> bool:
            if path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
                return False
            try:
                rel = path.relative_to(self.proj_dir)
                for blocked in NEVER_TAG_DIRS:
                    if str(rel).startswith(blocked):
                        return False
            except ValueError:
                pass
            return True

        def _do_tag(self, path: Path) -> None:
            time.sleep(0.5)
            if path.exists() and path.stat().st_size > 0:
                label = resolve_label(path, self.entity_type, self.proj_dir)
                tag_image(path, label, project_dir=self.proj_dir)

        def on_created(self, event):
            if event.is_directory:
                return
            path = Path(event.src_path)
            if self._should_tag(path):
                self._do_tag(path)

        def on_moved(self, event):
            """Catch atomic writes (tmp → final rename via os.replace)."""
            if event.is_directory:
                return
            path = Path(event.dest_path)
            if self._should_tag(path):
                self._do_tag(path)

    observer = Observer()
    for rel_dir, entity_type in TAGGED_DIRS.items():
        dir_path = project_dir / rel_dir
        dir_path.mkdir(parents=True, exist_ok=True)
        handler = _TagHandler(entity_type, project_dir)
        observer.schedule(handler, str(dir_path), recursive=True)
    observer.daemon = True
    observer.start()
    print("[ImageTagger] Background watcher started for reference images.")
    return observer


def stop_tag_watcher(observer) -> None:
    """Stop the background tag watcher."""
    if observer is None:
        return
    try:
        observer.stop()
        observer.join(timeout=3)
        print("[ImageTagger] Background watcher stopped.")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Interactive watch mode — blocks until interrupted
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
                tag_image(path, label, project_dir=self.proj_dir)

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
                        tag_image(f, label, project_dir=project_dir)


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
        tag_image(Path(args.file), label, project_dir=project_dir)
    elif args.watch:
        # Tag existing first, then watch
        count, _ = tag_all_project_images(project_dir)
        print(f"[ImageTagger] Tagged {count} existing images.")
        watch_project(project_dir)
    else:
        count, _ = tag_all_project_images(project_dir)
        print(f"[ImageTagger] Tagged {count} images.")
