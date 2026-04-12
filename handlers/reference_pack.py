from __future__ import annotations

import math
import os
import textwrap
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

CAST_CELL = 748
LOCATION_MAX = 1024
PREVIOUS_MAX = 1024
PROP_CELL = 512
MISC_MAX = 1024
XAI_RESCUE_CELL = 512
XAI_RESCUE_MAX_TILES = 6
PROMPT_SHEET_SIZE = (2048, 2048)
PROMPT_SHEET_FONT = 32
PROMPT_SHEET_MARGIN = 72
PROMPT_IMAGE_TRIGGER_CHARS = 3200
SHEET_BG = (18, 18, 18)
SHEET_FG = (245, 245, 245)
SHEET_GAP = 20


@dataclass
class PackedReferenceSet:
    storyboard_image: Path | None
    reference_images: list[Path]
    prompt_text: str
    prompt_sheet_image: Path | None = None


def _classify_ref(path: Path) -> str:
    posix = path.as_posix().lower()
    if "/cast/composites/" in posix:
        return "cast"
    if "/props/" in posix:
        return "prop"
    if "/locations/" in posix:
        return "location"
    if "/frames/composed/" in posix:
        return "previous"
    return "misc"


def _load_font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSansMono.ttf", size=size)
    except Exception:
        return ImageFont.load_default()


def _resize_to_box(src: Path, dst: Path, max_w: int, max_h: int) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as image:
        converted = image.convert("RGB")
        fitted = ImageOps.contain(converted, (max_w, max_h))
        fitted.save(dst, format="JPEG", quality=88, optimize=True)
    return dst


def _sheet_image(paths: list[Path], dst: Path, *, cell: int, columns: int) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    rows = max(1, math.ceil(len(paths) / columns))
    width = columns * cell + (columns - 1) * SHEET_GAP
    height = rows * cell + (rows - 1) * SHEET_GAP
    sheet = Image.new("RGB", (width, height), SHEET_BG)

    for idx, path in enumerate(paths):
        with Image.open(path) as image:
            fitted = ImageOps.contain(image.convert("RGB"), (cell, cell))
        x = (idx % columns) * (cell + SHEET_GAP) + (cell - fitted.width) // 2
        y = (idx // columns) * (cell + SHEET_GAP) + (cell - fitted.height) // 2
        sheet.paste(fitted, (x, y))

    sheet.save(dst, format="JPEG", quality=88, optimize=True)
    return dst


def _prompt_sheet(prompt_text: str, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    width, height = PROMPT_SHEET_SIZE
    image = Image.new("RGB", (width, height), SHEET_BG)
    draw = ImageDraw.Draw(image)
    font = _load_font(PROMPT_SHEET_FONT)
    usable_width = width - PROMPT_SHEET_MARGIN * 2
    avg_char_width = max(8, int(font.size * 0.62)) if hasattr(font, "size") else 18
    wrap_chars = max(40, usable_width // avg_char_width)
    wrapped_lines: list[str] = []
    for paragraph in prompt_text.splitlines():
        paragraph = paragraph.rstrip()
        if not paragraph:
            wrapped_lines.append("")
            continue
        wrapped_lines.extend(textwrap.wrap(paragraph, width=wrap_chars) or [""])

    y = PROMPT_SHEET_MARGIN
    line_height = PROMPT_SHEET_FONT + 10
    for line in wrapped_lines:
        if y + line_height > height - PROMPT_SHEET_MARGIN:
            break
        draw.text((PROMPT_SHEET_MARGIN, y), line, fill=SHEET_FG, font=font)
        y += line_height

    image.save(dst, format="PNG")
    return dst


def _split_prompt_for_image(prompt_text: str) -> tuple[str, str]:
    if not prompt_text:
        return "", ""
    midpoint = max(1, len(prompt_text) // 2)
    split_at = prompt_text.rfind("\n", 0, midpoint)
    if split_at < midpoint // 2:
        split_at = prompt_text.rfind(" ", 0, midpoint)
    if split_at < midpoint // 2:
        split_at = midpoint
    head = prompt_text[:split_at].rstrip()
    tail = prompt_text[split_at:].lstrip()
    if not head or not tail:
        return prompt_text, ""
    return head, tail


def prompt_image_retry_threshold() -> int:
    raw = os.getenv("SCREENWIRE_PROMPT_IMAGE_TRIGGER_CHARS", "").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return PROMPT_IMAGE_TRIGGER_CHARS


def build_reference_pack(
    *,
    pack_dir: Path,
    prompt_text: str,
    reference_images: list[Path],
    storyboard_image: Path | None = None,
    include_prompt_image: bool = False,
) -> PackedReferenceSet:
    pack_dir.mkdir(parents=True, exist_ok=True)

    always_include_prompt_image = os.getenv("SCREENWIRE_ENABLE_PROMPT_IMAGE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    use_prompt_image = include_prompt_image or always_include_prompt_image
    prompt_sheet_image: Path | None = None
    truncated_prompt = prompt_text
    overflow_prompt = ""
    if use_prompt_image:
        truncated_prompt, overflow_prompt = _split_prompt_for_image(prompt_text)

    packed_storyboard: Path | None = None
    if storyboard_image and storyboard_image.exists():
        packed_storyboard = _resize_to_box(
            storyboard_image,
            pack_dir / "storyboard.jpg",
            PREVIOUS_MAX,
            PREVIOUS_MAX,
        )

    buckets: dict[str, list[Path]] = {
        "previous": [],
        "cast": [],
        "location": [],
        "prop": [],
        "misc": [],
    }
    for path in reference_images:
        if path.exists():
            buckets[_classify_ref(path)].append(path)

    packed_refs: list[Path] = []

    if buckets["previous"]:
        packed_refs.append(
            _resize_to_box(
                buckets["previous"][0],
                pack_dir / "previous.jpg",
                PREVIOUS_MAX,
                PREVIOUS_MAX,
            )
        )

    if buckets["cast"]:
        cast_refs = buckets["cast"]
        if len(cast_refs) == 1:
            packed_refs.append(
                _resize_to_box(cast_refs[0], pack_dir / "cast.jpg", CAST_CELL, CAST_CELL)
            )
        else:
            packed_refs.append(
                _sheet_image(
                    cast_refs,
                    pack_dir / "cast_sheet.jpg",
                    cell=CAST_CELL,
                    columns=min(5, len(cast_refs)),
                )
            )

    if buckets["location"]:
        packed_refs.append(
            _resize_to_box(
                buckets["location"][0],
                pack_dir / "location.jpg",
                LOCATION_MAX,
                LOCATION_MAX,
            )
        )

    if buckets["prop"]:
        prop_refs = buckets["prop"]
        if len(prop_refs) == 1:
            packed_refs.append(
                _resize_to_box(prop_refs[0], pack_dir / "prop.jpg", PROP_CELL, PROP_CELL)
            )
        else:
            packed_refs.append(
                _sheet_image(
                    prop_refs,
                    pack_dir / "prop_sheet.jpg",
                    cell=PROP_CELL,
                    columns=min(3, len(prop_refs)),
                )
            )

    for idx, path in enumerate(buckets["misc"], start=1):
        packed_refs.append(
            _resize_to_box(path, pack_dir / f"misc_{idx}.jpg", MISC_MAX, MISC_MAX)
        )

    if overflow_prompt:
        prompt_sheet_image = _prompt_sheet(overflow_prompt, pack_dir / "prompt_sheet.png")
        packed_refs.append(prompt_sheet_image)

    return PackedReferenceSet(
        storyboard_image=packed_storyboard,
        reference_images=packed_refs,
        prompt_text=truncated_prompt,
        prompt_sheet_image=prompt_sheet_image,
    )


def build_xai_rescue_sheet(
    *,
    pack_dir: Path,
    reference_images: list[Path],
    storyboard_image: Path | None = None,
) -> Path | None:
    """Collapse all rescue refs into one compact sheet for xAI image edit calls."""
    packed = build_reference_pack(
        pack_dir=pack_dir / "packed",
        prompt_text="",
        reference_images=reference_images,
        storyboard_image=storyboard_image,
        include_prompt_image=False,
    )
    tiles: list[Path] = []
    if packed.storyboard_image and packed.storyboard_image.exists():
        tiles.append(packed.storyboard_image)
    tiles.extend(path for path in packed.reference_images if path.exists())
    if not tiles:
        return None

    rescue_tiles = tiles[:XAI_RESCUE_MAX_TILES]
    if len(rescue_tiles) == 1:
        return _resize_to_box(
            rescue_tiles[0],
            pack_dir / "xai_rescue.jpg",
            XAI_RESCUE_CELL * 2,
            XAI_RESCUE_CELL * 2,
        )
    return _sheet_image(
        rescue_tiles,
        pack_dir / "xai_rescue.jpg",
        cell=XAI_RESCUE_CELL,
        columns=min(3, len(rescue_tiles)),
    )
