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
    "google/nano-banana-pro",
    "google/nano-banana-2",
    "google/nano-banana",
]

# ---------------------------------------------------------------------------
# Grid specifications — 1:1 square (4096x4096 guide)
# ---------------------------------------------------------------------------

CANVAS_SIZE = 4096

GRID_SPECS = {
    "2x2": {"cols": 2, "rows": 2, "line_w": 32},
    "3x3": {"cols": 3, "rows": 3, "line_w": 28},
    "4x4": {"cols": 4, "rows": 4, "line_w": 24},
}

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = (
    "Create an image divided into a precise {cols}x{rows} grid "
    "following the attached reference layout exactly. "
    "Clean straight white dividing lines between cells. Equal cell sizes. "
    "No text, no labels, no watermarks, no numbers. "
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
    size = CANVAS_SIZE

    img = Image.new("RGB", (size, size), "black")
    draw = ImageDraw.Draw(img)

    cell_w = size / cols
    cell_h = size / rows

    for c in range(1, cols):
        x = round(c * cell_w)
        draw.rectangle([x - line_w // 2, 0, x + line_w // 2, size], fill="white")

    for r in range(1, rows):
        y = round(r * cell_h)
        draw.rectangle([0, y - line_w // 2, size, y + line_w // 2], fill="white")

    draw.rectangle([0, 0, size - 1, size - 1], outline="white", width=line_w)

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
        timeout=300,
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
    inp = dict(base)
    if model == "google/nano-banana-pro":
        inp.pop("google_search", None)
        inp.pop("image_search", None)
        inp.setdefault("safety_tolerance", 4)
    elif model == "google/nano-banana":
        allowed = {"prompt", "image_input", "aspect_ratio", "output_format"}
        inp = {k: v for k, v in inp.items() if k in allowed}
    return inp


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

def split_grid(image_path: Path, grid: str, output_dir: Path) -> list[Path]:
    import numpy as np

    spec = GRID_SPECS[grid]
    cols, rows = spec["cols"], spec["rows"]
    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    arr = np.array(img)

    def find_dividers(axis_means, count, total_px):
        for thresh_fn in [lambda v: v < 30, lambda v: v > 240]:
            candidates = [i for i, v in enumerate(axis_means) if thresh_fn(v)]
            if len(candidates) < count - 1:
                continue
            groups, current = [], [candidates[0]]
            for c in candidates[1:]:
                if c - current[-1] <= 5:
                    current.append(c)
                else:
                    groups.append(current)
                    current = [c]
            groups.append(current)
            spacing = total_px / count
            centers = sorted([int(np.mean(g)) for g in groups])
            best = []
            for ep in [round(i * spacing) for i in range(1, count)]:
                closest = min(centers, key=lambda d: abs(d - ep))
                if abs(closest - ep) < spacing * 0.15:
                    best.append(closest)
            if len(best) == count - 1:
                return best
        return None

    v_divs = find_dividers(arr.mean(axis=(0, 2)), cols, w)
    h_divs = find_dividers(arr.mean(axis=(1, 2)), rows, h)

    x_bounds = ([0] + v_divs + [w]) if v_divs else [round(i * w / cols) for i in range(cols + 1)]
    y_bounds = ([0] + h_divs + [h]) if h_divs else [round(i * h / rows) for i in range(rows + 1)]

    output_dir.mkdir(parents=True, exist_ok=True)
    margin = spec["line_w"] // 2 + 2
    paths = []
    idx = 0

    for r in range(rows):
        for c in range(cols):
            left = x_bounds[c] + (margin if c > 0 else 0)
            upper = y_bounds[r] + (margin if r > 0 else 0)
            right = x_bounds[c + 1] - (margin if c < cols - 1 else 0)
            lower = y_bounds[r + 1] - (margin if r < rows - 1 else 0)
            cell = img.crop((left, upper, right, lower))
            out = output_dir / f"frame_{idx:03d}.png"
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
                   scene: str = "") -> dict:
    """Generate a grid storyboard image and split into individual cells.

    Args:
        grid: Grid layout string ("2x2", "3x3", or "4x4")
        output_dir: Directory for output files
        refs: Optional list of reference image paths
        cell_prompts: List of per-cell prompt strings (one per frame).
                      Stacked into the template as numbered cells.
        scene: Legacy fallback — only used if cell_prompts is empty.

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
    if cell_prompts:
        numbered = []
        for i, cp in enumerate(cell_prompts):
            row = i // spec["cols"] + 1
            col = i // spec["cols"]  # not needed for label
            numbered.append(f"[Cell {i + 1}] {cp}")
        cell_block = "\n".join(numbered)
    else:
        cell_block = scene

    prompt = PROMPT_TEMPLATE.format(
        cols=spec["cols"], rows=spec["rows"], cell_prompts=cell_block
    )

    image_input = [_to_data_uri(guide)]
    if refs:
        for r in refs:
            p = Path(r)
            if p.exists():
                image_input.append(_to_data_uri(p))

    pred_input: dict[str, Any] = {
        "prompt": prompt,
        "aspect_ratio": "1:1",
        "output_format": "png",
        "image_input": image_input,
    }

    t0 = time.monotonic()

    async with httpx.AsyncClient() as client:
        last_error = None
        for model in IMAGE_MODEL_CHAIN:
            adapted = _adapt_input(model, pred_input)
            _log("Gen", f"Trying {model}...")
            try:
                data = await _replicate_predict(client, model, adapted)
            except httpx.HTTPStatusError as exc:
                _log("Gen", f"{model} -> HTTP {exc.response.status_code}")
                last_error = str(exc)
                continue

            pid = data.get("id", "")
            if data.get("status") != "succeeded":
                data = await _poll_prediction(client, pid)
            if data.get("status") != "succeeded":
                _log("Gen", f"{model} -> failed")
                last_error = data.get("error", "unknown")
                continue

            url = data.get("output")
            if isinstance(url, list):
                url = url[0]
            await _download(client, url, composite)

            elapsed = round(time.monotonic() - t0, 1)
            img = Image.open(composite)
            _log("Gen", f"Done — {model} — {img.size[0]}x{img.size[1]} — {elapsed}s")

            frames = split_grid(composite, grid, output_dir / "frames")
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
