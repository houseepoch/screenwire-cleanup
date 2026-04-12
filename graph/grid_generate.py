"""
Grid Frame Generator — Nano Banana Pro (Pipeline Utility)
==========================================================

Generates multiple frames in a single 1:1 square image using prompted grid
specification via Replicate's nano-banana model chain, then splits into
individual frame PNGs.

Stripped from screenwire-cc-POWERED/experiments/grid-gen/grid_generate.py.
Only the generate() async function and supporting helpers are kept.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
from telemetry import current_phase, current_run_id

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN", "")

# Model chain and error classification now live in handlers/base.py

# ---------------------------------------------------------------------------
# Grid specifications — guidance-only layouts sized for available ratios
# ---------------------------------------------------------------------------

GRID_SPECS = {
    "1x1": {
        "cols": 1, "rows": 1, "line_w": 2,
        "canvas_w": 1024, "canvas_h": 576, "aspect_ratio": "16:9",
        "canvas_label": "16:9 widescreen",
    },
    "2x1": {
        "cols": 2, "rows": 1, "line_w": 2,
        "canvas_w": 1024, "canvas_h": 576, "aspect_ratio": "16:9",
        "canvas_label": "16:9 widescreen",
    },
    "2x2": {
        "cols": 2, "rows": 2, "line_w": 2,
        "canvas_w": 1024, "canvas_h": 1024, "aspect_ratio": "1:1",
        "canvas_label": "square",
    },
    "3x2": {
        "cols": 3, "rows": 2, "line_w": 2,
        "canvas_w": 1152, "canvas_h": 768, "aspect_ratio": "3:2",
        "canvas_label": "3:2 landscape",
    },
}

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = (
    "Create an image divided into a precise {cols}x{rows} grid "
    "following the attached reference layout exactly. "
    "Clean straight thin black dividing lines between cells. Equal cell sizes. "
    "No text, no labels, no watermarks, no numbers. "
    "{global_style}"
    "Each cell is a sequential cinematic guidance frame used only for continuity planning. "
    "Preserve cast identity, wardrobe, props, architecture, lighting direction, and staging from cell to cell. "
    "Fill each cell exactly as described below (left-to-right, top-to-bottom):\n\n"
    "{cell_prompts}"
)


def _log(tag: str, msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"  [{ts}] [{tag}] {msg}")


# ---------------------------------------------------------------------------
# Grid guide generator
# ---------------------------------------------------------------------------

def generate_grid_guide(grid: str, output_dir: Path | None = None) -> Path:
    """Create a grid guide PNG — black canvas with white divider lines.

    This is the first input image fed to nano-banana-pro so the model
    understands the exact cell layout before filling in content.
    """
    spec = GRID_SPECS[grid]
    cols, rows, line_w = spec["cols"], spec["rows"], spec["line_w"]
    canvas_w, canvas_h = spec["canvas_w"], spec["canvas_h"]

    img = Image.new("RGB", (canvas_w, canvas_h), "white")
    draw = ImageDraw.Draw(img)

    cell_w = canvas_w / cols
    cell_h = canvas_h / rows

    for c in range(1, cols):
        x = round(c * cell_w)
        draw.rectangle([x - line_w // 2, 0, x + line_w // 2, canvas_h], fill="black")

    for r in range(1, rows):
        y = round(r * cell_h)
        draw.rectangle([0, y - line_w // 2, canvas_w, y + line_w // 2], fill="black")

    draw.rectangle([0, 0, canvas_w - 1, canvas_h - 1], outline="black", width=line_w)

    guides_dir = (output_dir or Path.cwd()) / "guides"
    guides_dir.mkdir(parents=True, exist_ok=True)
    out = guides_dir / f"guide_{grid}.png"
    img.save(out, "PNG")
    return out


# Replicate API helpers removed — now handled by handlers/storyboard.py


def _extract_shared_cell_style_prefix(cell_prompts: list[str]) -> tuple[str, list[str]]:
    """Hoist a repeated leading style clause that appears in every cell prompt.

    Legacy storyboard prompts often begin each cell with:
        "<style clause>. . <cell-specific description>"
    If that style clause is identical across all cells, include it once at the
    grid level and strip it from each cell body.
    """
    if not cell_prompts:
        return "", cell_prompts

    separator = ". . "
    prefixes = []
    stripped = []
    for prompt in cell_prompts:
        if separator not in prompt:
            return "", cell_prompts
        prefix, remainder = prompt.split(separator, 1)
        prefixes.append(prefix.strip())
        stripped.append(remainder.strip())

    shared = prefixes[0]
    if len(shared) < 60 or any(p != shared for p in prefixes[1:]):
        return "", cell_prompts

    return shared, stripped


# _download and _to_data_uri removed — now handled by handlers/base.py
# split_grid() removed — StoryboardHandler._extract_cells() is the sole cell-splitting path.


# ---------------------------------------------------------------------------
# Core generate function
# ---------------------------------------------------------------------------

async def generate(grid: str, output_dir: Path,
                   refs: list[str] | None = None,
                   cell_prompts: list[str] | None = None,
                   scene: str = "",
                   frame_ids: list[str] | None = None,
                   style_prefix: str = "",
                   grid_id: str = "",
                   run_id: str = "",
                   phase: str = "") -> dict:
    """Generate a grid storyboard image and extract individual cells.

    Uses the StoryboardHandler for model chain execution (nano-banana-2 →
    nano-banana-pro) and cell extraction via StoryboardHandler._extract_cells().

    Args:
        grid: Grid layout string ("1x1", "2x1", "2x2", or "3x2")
        output_dir: Directory for output files
        refs: Optional list of reference image paths
        cell_prompts: List of per-cell prompt strings (one per frame).
                      Stacked into the template as numbered cells.
        scene: Legacy fallback — only used if cell_prompts is empty.
        frame_ids: Optional list of frame IDs for naming extracted cells.
                   When provided, cells are named {frame_id}.png instead of
                   frame_NNN.png. Order: left-to-right, top-to-bottom.
        style_prefix: Optional shared visual style description to apply once to
                      the entire grid instead of repeating it per cell.

    Returns:
        dict with keys: composite, frames, grid, model, output_size,
        cell_size, elapsed_s
    """
    from handlers import get_handler, StoryboardInput

    if not REPLICATE_API_TOKEN:
        raise RuntimeError("REPLICATE_API_TOKEN not set")

    spec = GRID_SPECS[grid]
    output_dir.mkdir(parents=True, exist_ok=True)
    composite = output_dir / "composite.png"

    guide = generate_grid_guide(grid, output_dir)

    # Build numbered cell descriptions from individual frame prompts
    shared_style = style_prefix.strip()
    effective_cell_prompts = list(cell_prompts or [])
    if effective_cell_prompts:
        inferred_style, stripped_cells = _extract_shared_cell_style_prefix(effective_cell_prompts)
        if inferred_style:
            if not shared_style:
                shared_style = inferred_style
            effective_cell_prompts = stripped_cells

    if effective_cell_prompts:
        numbered = []
        for i, cp in enumerate(effective_cell_prompts):
            numbered.append(f"[Cell {i + 1}] {cp}")
        cell_block = "\n".join(numbered)
    else:
        cell_block = scene

    prompt = PROMPT_TEMPLATE.format(
        cols=spec["cols"],
        rows=spec["rows"],
        canvas_label=spec["canvas_label"],
        global_style=(f"Global visual style for all cells: {shared_style}. " if shared_style else ""),
        cell_prompts=cell_block,
    )

    # Grid guide is the first reference — tells the model the exact layout
    ref_paths: list[Path] = [guide]
    if refs:
        for r in refs:
            p = Path(r)
            if p.exists():
                ref_paths.append(p)

    t0 = time.monotonic()

    # Delegate generation to StoryboardHandler (model chain + retry + API)
    handler = get_handler("storyboard", replicate_token=REPLICATE_API_TOKEN)
    try:
        handler_result = await handler.generate(StoryboardInput(
            grid_id=grid_id or grid,
            prompt=prompt,
            reference_images=ref_paths,
            layout=grid,
            frame_ids=frame_ids or [],
            output_dir=output_dir,
            run_id=run_id or current_run_id(),
            phase=phase or current_phase(),
        ))
    finally:
        await handler.close()

    if not handler_result.success:
        raise RuntimeError(f"All models failed. Last: {handler_result.error}")

    # Move handler composite to the expected location
    if handler_result.composite_path and handler_result.composite_path != composite:
        shutil.move(str(handler_result.composite_path), str(composite))

    elapsed = round(time.monotonic() - t0, 1)
    img = Image.open(composite)
    _log("Gen", f"Done — {handler_result.model_used} — {img.size[0]}x{img.size[1]} — {elapsed}s")

    # Cell extraction is handled by StoryboardHandler._extract_cells (called above)
    frames = handler_result.cell_paths
    cell_size = ""
    if frames:
        cell_img = Image.open(frames[0])
        cell_size = f"{cell_img.size[0]}x{cell_img.size[1]}"

    result = {
        "composite": str(composite),
        "frames": [str(f) for f in frames],
        "grid": grid,
        "model": handler_result.model_used,
        "output_size": f"{img.size[0]}x{img.size[1]}",
        "cell_size": cell_size,
        "elapsed_s": elapsed,
    }

    with open(output_dir / "result.json", "w") as f:
        json.dump(result, f, indent=2)

    return result
