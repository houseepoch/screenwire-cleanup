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

import asyncio
import base64
import json
import mimetypes
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN", "")

IMAGE_MODEL_CHAIN = [
    "google/nano-banana-2",
    "google/nano-banana-pro",
]


def _extract_replicate_error_code(error_msg: str) -> str:
    error_msg = error_msg or ""
    import re

    match = re.search(r"\((E\d+)\)", error_msg)
    if match:
        return match.group(1)

    match = re.search(r"\(code:\s*([A-Z]+)\)", error_msg)
    if match:
        return match.group(1)

    return "UNKNOWN"


def _is_retryable_error(error_msg: str) -> bool:
    error_msg = (error_msg or "").lower()
    code = _extract_replicate_error_code(error_msg.upper())
    return (
        "please retry" in error_msg
        or "high demand" in error_msg
        or "at capacity" in error_msg
        or code in {"E003", "PA"}
    )

# ---------------------------------------------------------------------------
# Grid specifications — 16:9 widescreen (1K guide, 2K output)
# ---------------------------------------------------------------------------

CANVAS_W = 1024
CANVAS_H = 576  # 1024 / 16 * 9

GRID_SPECS = {
    "4x4": {"cols": 4, "rows": 4, "line_w": 2},
    "3x3": {"cols": 3, "rows": 3, "line_w": 2},
}

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = (
    "Create a 16:9 widescreen image divided into a precise {cols}x{rows} grid "
    "following the attached reference layout exactly. "
    "Clean straight thin black dividing lines between cells. Equal cell sizes. "
    "No text, no labels, no watermarks, no numbers. "
    "{global_style}"
    "Each cell is a sequential cinematic frame, photorealistic and self-contained. "
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

    img = Image.new("RGB", (CANVAS_W, CANVAS_H), "white")
    draw = ImageDraw.Draw(img)

    cell_w = CANVAS_W / cols
    cell_h = CANVAS_H / rows

    for c in range(1, cols):
        x = round(c * cell_w)
        draw.rectangle([x - line_w // 2, 0, x + line_w // 2, CANVAS_H], fill="black")

    for r in range(1, rows):
        y = round(r * cell_h)
        draw.rectangle([0, y - line_w // 2, CANVAS_W, y + line_w // 2], fill="black")

    draw.rectangle([0, 0, CANVAS_W - 1, CANVAS_H - 1], outline="black", width=line_w)

    guides_dir = (output_dir or Path.cwd()) / "guides"
    guides_dir.mkdir(parents=True, exist_ok=True)
    out = guides_dir / f"guide_{grid}.png"
    img.save(out, "PNG")
    return out


# ---------------------------------------------------------------------------
# Replicate API helpers
# ---------------------------------------------------------------------------

async def _replicate_predict(client: httpx.AsyncClient, model: str, pred_input: dict) -> dict:
    resp = await client.post(
        f"https://api.replicate.com/v1/models/{model}/predictions",
        json={"input": pred_input},
        headers={
            "Authorization": f"Bearer {REPLICATE_API_TOKEN}",
            "Content-Type": "application/json",
            "Prefer": "wait",
        },
        timeout=None,
    )
    resp.raise_for_status()
    return resp.json()


async def _poll_prediction(client: httpx.AsyncClient, prediction_id: str) -> dict:
    headers = {"Authorization": f"Bearer {REPLICATE_API_TOKEN}"}
    url = f"https://api.replicate.com/v1/predictions/{prediction_id}"
    for _ in range(120):
        await asyncio.sleep(5)
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") in ("succeeded", "failed", "canceled"):
            return data
    return {"status": "timeout"}


def _adapt_input(model: str, base: dict) -> dict:
    """Adapt prediction input per model schema.

    nano-banana-2:  prompt, image_input, aspect_ratio, resolution, google_search, image_search, output_format
    nano-banana-pro: prompt, image_input, aspect_ratio, resolution, safety_filter_level, allow_fallback_model, output_format
    nano-banana:     prompt, image_input, aspect_ratio, output_format
    """
    inp = dict(base)
    if model == "google/nano-banana-2":
        inp.pop("safety_filter_level", None)
        inp.pop("allow_fallback_model", None)
    elif model == "google/nano-banana-pro":
        inp.pop("google_search", None)
        inp.pop("image_search", None)
        inp.setdefault("safety_filter_level", "block_only_high")
    elif model == "google/nano-banana":
        allowed = {"prompt", "image_input", "aspect_ratio", "output_format"}
        inp = {k: v for k, v in inp.items() if k in allowed}
    return inp


def _can_use_pro_capacity_rescue(model: str, pred_input: dict, error_msg: str) -> bool:
    return (
        model == "google/nano-banana-pro"
        and pred_input.get("resolution") == "4K"
        and _is_retryable_error(error_msg)
    )


def _build_pro_capacity_rescue_input(pred_input: dict) -> dict:
    rescue = dict(pred_input)
    rescue["resolution"] = "2K"
    rescue["allow_fallback_model"] = True
    rescue.setdefault("safety_filter_level", "block_only_high")
    return rescue


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


async def _download(client: httpx.AsyncClient, url: str, output: Path):
    tmp = output.with_suffix(output.suffix + ".tmp")
    async with client.stream("GET", url) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as f:
            async for chunk in resp.aiter_bytes(8192):
                f.write(chunk)
    os.replace(tmp, output)


def _to_data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


# ---------------------------------------------------------------------------
# Smart grid splitter
# ---------------------------------------------------------------------------

def _find_dividers_multi(arr, count: int, total_px: int,
                         tolerance: float = 0.20) -> list[int] | None:
    """Multi-strategy divider detection along one axis.

    Tries four strategies in order, returns the first that yields
    exactly (count - 1) dividers near the expected grid positions.

    Strategies:
      1. Edge gradient peaks — Sobel-like gradient magnitude projected
         onto the axis. Strong edges at divider boundaries create peaks.
      2. Brightness extremes — dark lines (mean < threshold) or light
         lines (mean > threshold) across the full axis.
      3. Variance valleys — divider lines have low color variance
         compared to content-rich cell interiors.
      4. Adaptive threshold — uses a wider brightness band, accepting
         any row/col whose mean deviates significantly from the image
         median.

    Args:
        arr: 1-D array of per-pixel values (e.g. axis mean brightness).
        count: Number of cells along this axis (cols or rows).
        total_px: Total pixels along this axis (width or height).
        tolerance: How far a detected divider can be from the expected
                   grid position, as a fraction of cell size.
    """
    import numpy as np

    spacing = total_px / count
    expected = [round(i * spacing) for i in range(1, count)]

    def _match_to_grid(centers: list[int]) -> list[int] | None:
        """Match detected centers to expected grid positions."""
        if len(centers) < count - 1:
            return None
        matched = []
        used = set()
        for ep in expected:
            candidates = [(abs(c - ep), c) for c in centers if c not in used]
            if not candidates:
                return None
            dist, best = min(candidates)
            if dist > spacing * tolerance:
                return None
            matched.append(best)
            used.add(best)
        return sorted(matched)

    def _group_and_center(candidates: list[int], gap: int = 8) -> list[int]:
        """Group adjacent pixel indices and return group centers."""
        if not candidates:
            return []
        groups, current = [], [candidates[0]]
        for c in candidates[1:]:
            if c - current[-1] <= gap:
                current.append(c)
            else:
                groups.append(current)
                current = [c]
        groups.append(current)
        return sorted([int(np.mean(g)) for g in groups])

    # Strategy 1: Edge gradient peaks
    # Compute gradient magnitude along the axis
    grad = np.abs(np.diff(arr.astype(float)))
    # Smooth with a small kernel to reduce noise
    kernel_size = max(3, int(total_px * 0.002))
    kernel = np.ones(kernel_size) / kernel_size
    grad_smooth = np.convolve(grad, kernel, mode="same")
    # Find peaks above a dynamic threshold
    grad_thresh = np.percentile(grad_smooth, 90)
    grad_candidates = [i for i, v in enumerate(grad_smooth) if v > grad_thresh]
    centers = _group_and_center(grad_candidates)
    result = _match_to_grid(centers)
    if result:
        return result

    # Strategy 2: Brightness extremes (dark or light lines)
    for thresh_fn in [lambda v: v < 50, lambda v: v > 220,
                      lambda v: v < 80, lambda v: v > 200]:
        candidates = [i for i, v in enumerate(arr) if thresh_fn(v)]
        centers = _group_and_center(candidates)
        result = _match_to_grid(centers)
        if result:
            return result

    # Strategy 3: Variance valleys (divider lines have uniform color)
    # This is passed as a separate array by the caller when available
    # Skip here — handled via the variance array path in split_grid

    # Strategy 4: Adaptive threshold — deviation from median
    median = float(np.median(arr))
    std = float(np.std(arr))
    if std > 5:  # Only if there's meaningful variation
        dev_threshold = max(std * 1.5, 20)
        candidates = [i for i, v in enumerate(arr)
                      if abs(v - median) > dev_threshold]
        centers = _group_and_center(candidates)
        result = _match_to_grid(centers)
        if result:
            return result

    return None


def split_grid(image_path: Path, grid: str, output_dir: Path,
               frame_ids: list[str] | None = None) -> list[Path]:
    """Split a grid composite into individual cell images.

    Uses multi-strategy divider detection:
      1. Edge gradient peaks (handles blurred or imperfect lines)
      2. Brightness extremes with progressive thresholds
      3. Color variance valleys (uniform-color dividers vs busy content)
      4. Adaptive deviation from image median
      5. Equal division fallback with content-aware inset

    Args:
        image_path: Path to the composite grid image.
        grid: Grid layout string (e.g. "3x3").
        output_dir: Directory for output cell images.
        frame_ids: Optional list of frame IDs (left-to-right, top-to-bottom).
                   When provided, cells are named {frame_id}.png instead of
                   frame_NNN.png.
    """
    import numpy as np

    spec = GRID_SPECS[grid]
    cols, rows = spec["cols"], spec["rows"]
    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    arr = np.array(img)

    # Project to per-column and per-row mean brightness
    col_means = arr.mean(axis=(0, 2))  # shape: (w,) — mean brightness per column
    row_means = arr.mean(axis=(1, 2))  # shape: (h,) — mean brightness per row

    # Also compute per-column/row color variance (dividers are uniform)
    col_var = arr.astype(float).var(axis=(0, 2))
    row_var = arr.astype(float).var(axis=(0, 2)) if rows > 1 else col_var
    # Recompute row_var correctly along axis 0
    row_var = arr.astype(float).var(axis=(1, 2))

    # Try multi-strategy detection on brightness
    v_divs = _find_dividers_multi(col_means, cols, w)
    h_divs = _find_dividers_multi(row_means, rows, h)

    # If brightness failed, try variance (divider lines have low variance)
    if v_divs is None:
        inv_var = col_var.max() - col_var  # Invert so low-variance = high signal
        v_divs = _find_dividers_multi(inv_var, cols, w)
    if h_divs is None:
        inv_var = row_var.max() - row_var
        h_divs = _find_dividers_multi(inv_var, rows, h)

    method = "detected"
    if v_divs is None or h_divs is None:
        method = "equal_division"

    x_bounds = ([0] + v_divs + [w]) if v_divs else [round(i * w / cols) for i in range(cols + 1)]
    y_bounds = ([0] + h_divs + [h]) if h_divs else [round(i * h / rows) for i in range(rows + 1)]

    # Adaptive margin: if dividers were detected, measure actual line width
    # by checking how many pixels around the divider are part of the line
    if method == "detected":
        def _measure_line_width(arr_1d, div_pos: int, search_radius: int = 20) -> int:
            """Measure the width of a divider line at a detected position."""
            median_val = float(np.median(arr_1d))
            div_val = float(arr_1d[div_pos])
            # Line pixels deviate from median in the same direction as the divider center
            is_dark_line = div_val < median_val
            half_widths = []
            for direction in [-1, 1]:
                dist = 0
                for d in range(1, search_radius + 1):
                    pos = div_pos + d * direction
                    if pos < 0 or pos >= len(arr_1d):
                        break
                    val = float(arr_1d[pos])
                    if is_dark_line and val > median_val * 0.6:
                        break
                    if not is_dark_line and val < median_val + (255 - median_val) * 0.4:
                        break
                    dist = d
                half_widths.append(dist)
            return sum(half_widths) + 1  # +1 for the center pixel

        # Measure across all detected dividers and use the max
        line_widths = []
        if v_divs:
            for d in v_divs:
                line_widths.append(_measure_line_width(col_means, d))
        if h_divs:
            for d in h_divs:
                line_widths.append(_measure_line_width(row_means, d))
        max_line_w = max(line_widths) if line_widths else spec["line_w"]
        margin = max_line_w // 2 + 1
    else:
        margin = spec["line_w"] // 2 + 1

    _log("Split", f"{w}x{h} → {cols}x{rows}, method={method}, margin={margin}px"
         f"{', v_divs=' + str(v_divs) if v_divs else ''}"
         f"{', h_divs=' + str(h_divs) if h_divs else ''}")

    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    idx = 0

    for r in range(rows):
        for c in range(cols):
            if frame_ids and idx < len(frame_ids):
                filename = f"{frame_ids[idx]}.png"
            else:
                filename = f"frame_{idx:03d}.png"
            left = x_bounds[c] + (margin if c > 0 else 0)
            upper = y_bounds[r] + (margin if r > 0 else 0)
            right = x_bounds[c + 1] - (margin if c < cols - 1 else 0)
            lower = y_bounds[r + 1] - (margin if r < rows - 1 else 0)
            cell = img.crop((left, upper, right, lower))
            out = output_dir / filename
            cell.save(out)
            paths.append(out)
            idx += 1

    return paths


# ---------------------------------------------------------------------------
# Core generate function
# ---------------------------------------------------------------------------

async def generate(grid: str, output_dir: Path,
                   refs: list[str] | None = None,
                   cell_prompts: list[str] | None = None,
                   scene: str = "",
                   frame_ids: list[str] | None = None,
                   style_prefix: str = "") -> dict:
    """Generate a grid storyboard image and split into individual cells.

    Args:
        grid: Grid layout string ("2x2", "3x3", or "4x4")
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
            row = i // spec["cols"] + 1
            col = i // spec["cols"]  # not needed for label
            numbered.append(f"[Cell {i + 1}] {cp}")
        cell_block = "\n".join(numbered)
    else:
        cell_block = scene

    prompt = PROMPT_TEMPLATE.format(
        cols=spec["cols"],
        rows=spec["rows"],
        global_style=(f"Global visual style for all cells: {shared_style}. " if shared_style else ""),
        cell_prompts=cell_block,
    )

    image_input = [_to_data_uri(guide)]
    if refs:
        for r in refs:
            p = Path(r)
            if p.exists():
                image_input.append(_to_data_uri(p))

    pred_input: dict[str, Any] = {
        "prompt": prompt,
        "aspect_ratio": "16:9",
        "resolution": "4K",
        "output_format": "png",
        "image_input": image_input,
    }

    t0 = time.monotonic()

    max_retries = 3
    async with httpx.AsyncClient(timeout=None) as client:
        last_error = None
        for model in IMAGE_MODEL_CHAIN:
            adapted = _adapt_input(model, pred_input)
            succeeded = False
            last_model_error = ""
            for attempt in range(1, max_retries + 1):
                _log("Gen", f"Trying {model}..." + (f" (attempt {attempt})" if attempt > 1 else ""))
                try:
                    data = await _replicate_predict(client, model, adapted)
                except httpx.HTTPStatusError as exc:
                    _log("Gen", f"{model} -> HTTP {exc.response.status_code}")
                    last_error = str(exc)
                    last_model_error = str(exc)
                    if exc.response.status_code in (502, 503, 429) and attempt < max_retries:
                        await asyncio.sleep(5 * attempt)
                        continue
                    break

                pid = data.get("id", "")
                if data.get("status") != "succeeded":
                    data = await _poll_prediction(client, pid)
                if data.get("status") != "succeeded":
                    err = data.get("error", "unknown")
                    _log("Gen", f"{model} -> failed: {err}")
                    last_error = err
                    last_model_error = str(err)
                    if _is_retryable_error(str(err)) and attempt < max_retries:
                        await asyncio.sleep(5 * attempt)
                        continue
                    break
                succeeded = True
                break
            if not succeeded and _can_use_pro_capacity_rescue(model, adapted, last_model_error):
                rescue_input = _build_pro_capacity_rescue_input(adapted)
                _log("Gen", f"{model} transient 4K failure -> retrying with 2K + allow_fallback_model=true")
                try:
                    data = await _replicate_predict(client, model, rescue_input)
                    pid = data.get("id", "")
                    if data.get("status") != "succeeded":
                        data = await _poll_prediction(client, pid)
                    if data.get("status") == "succeeded":
                        succeeded = True
                        _log("Gen", f"{model} rescue path succeeded")
                    else:
                        err = data.get("error", "unknown")
                        _log("Gen", f"{model} rescue path failed: {err}")
                        last_error = err
                except httpx.HTTPStatusError as exc:
                    _log("Gen", f"{model} rescue HTTP {exc.response.status_code}")
                    last_error = str(exc)
            if not succeeded:
                continue

            url = data.get("output")
            if isinstance(url, list):
                url = url[0]
            await _download(client, url, composite)

            elapsed = round(time.monotonic() - t0, 1)
            img = Image.open(composite)
            _log("Gen", f"Done — {model} — {img.size[0]}x{img.size[1]} — {elapsed}s")

            frames = split_grid(composite, grid, output_dir / "frames",
                               frame_ids=frame_ids)
            cell = Image.open(frames[0])

            result = {
                "composite": str(composite),
                "frames": [str(f) for f in frames],
                "grid": grid,
                "model": model,
                "output_size": f"{img.size[0]}x{img.size[1]}",
                "cell_size": f"{cell.size[0]}x{cell.size[1]}",
                "elapsed_s": elapsed,
            }

            with open(output_dir / "result.json", "w") as f:
                json.dump(result, f, indent=2)

            return result

        raise RuntimeError(f"All models failed. Last: {last_error}")
