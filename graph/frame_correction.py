"""
Frame Correction — Grok Vision review + nano-banana-2 edit per frame
=====================================================================

After a storyboard grid is generated and split into individual cells,
each frame is reviewed by Grok vision against its expected composition
(characters present, dialogue speaker, camera angle, props, etc.).

If the frame deviates from what the graph expects, Grok crafts a
targeted edit prompt. That edit prompt + the cell image + tagged
reference images are sent to nano-banana-2 for a corrective pass.

Flow per frame:
  1. Grok vision receives: cell image + image prompt + reference images
  2. Grok evaluates: are the expected characters visible? Correct count?
     Is the dialogue speaker prominent? Are required props present?
     Does the composition match the formula tag?
  3. If correction needed → Grok returns an edit prompt
  4. Edit prompt + cell image + refs → nano-banana-2 → corrected cell
  5. Corrected cell overwrites the original

Designed to run pipelined: each frame corrects independently as soon
as its grid cell is extracted.
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

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

XAI_API_KEY = os.getenv("XAI_API_KEY", "")
XAI_BASE_URL = "https://api.x.ai/v1"
GROK_VISION_MODEL = "grok-4-1-fast-non-reasoning"

REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN", "")

# Model fallback chain — same as grid generation / server
IMAGE_MODEL_CHAIN = [
    "google/nano-banana-2",
    "google/nano-banana-pro",
]


def _log(tag: str, msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"  [{ts}] [FrameCorrect:{tag}] {msg}")


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def _image_to_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    data = base64.b64encode(path.read_bytes()).decode()
    return f"data:{mime};base64,{data}"


def _resolve_abs(rel_path: str, project_dir: Path) -> Path:
    p = Path(rel_path)
    if p.is_absolute():
        return p
    return project_dir / p


# ---------------------------------------------------------------------------
# Grok Vision — review frame and produce edit prompt
# ---------------------------------------------------------------------------

REVIEWER_SYSTEM_PROMPT = """\
You are a frame composition reviewer for a cinematic AI storyboard pipeline.

You receive:
1. A generated storyboard frame image (the cell extracted from a grid)
2. The image generation prompt that was used to create this frame
3. Tagged reference images of the cast, location, and props expected in this frame

Your job: compare what is VISIBLE in the frame against what SHOULD be there \
according to the prompt and references, then decide if a corrective edit is needed.

CHECK these critical elements:
- CHARACTER COUNT: Are the correct number of characters visible? \
  Compare against reference images to identify who is present vs missing.
- DIALOGUE SPEAKER: If the prompt mentions dialogue, is the speaking \
  character prominently visible and positioned correctly?
- CHARACTER IDENTITY: Do visible characters match their reference images? \
  (hair color, build, clothing, distinguishing features)
- PROPS: Are required props visible when the prompt demands them?
- COMPOSITION: Does the shot type match? (close-up vs wide, angle, framing)
- POSITION: Are characters at their expected screen positions? \
  (frame_left, frame_center, frame_right)

RESPOND in exactly one of two formats:

If the frame is acceptable (minor imperfections are OK):
PASS

If correction is needed, write ONLY an edit prompt for nano-banana-2 \
(an image editing model). The edit prompt should:
- Be specific about what to FIX, not a full scene description
- Reference the attached images as visual guides for character appearance
- Focus on the most critical issue (1-2 corrections max)
- Stay under 500 characters
- Start with "EDIT:" followed by the instruction

Examples:
EDIT: Add a second character matching the dark-haired male reference at frame_right, facing the woman at frame_left. Keep all existing elements.
EDIT: Replace the distant figure with a close-up of the red-haired woman from the reference, filling the right two-thirds of frame. Maintain the city background.
EDIT: Add the signal phone prop to the character's right hand, screen glowing. Keep pose and lighting.
"""


async def _review_frame(
    cell_image_path: Path,
    image_prompt: str,
    ref_image_paths: list[Path],
) -> str | None:
    """Ask Grok vision to review a frame and return an edit prompt or None.

    Returns:
        None if frame passes review (no correction needed)
        str edit prompt if correction needed
    """
    key = XAI_API_KEY or os.getenv("XAI_API_KEY", "")
    if not key:
        raise RuntimeError("XAI_API_KEY required for frame correction")

    # Build content array: cell image first, then references, then prompt text
    content: list[dict] = [
        {
            "type": "text",
            "text": "GENERATED FRAME (to review):",
        },
        {
            "type": "image_url",
            "image_url": {"url": _image_to_data_url(cell_image_path)},
        },
    ]

    # Add tagged reference images
    for i, ref_path in enumerate(ref_image_paths):
        if ref_path.exists():
            content.append({
                "type": "text",
                "text": f"REFERENCE IMAGE {i + 1} ({ref_path.stem}):",
            })
            content.append({
                "type": "image_url",
                "image_url": {"url": _image_to_data_url(ref_path)},
            })

    content.append({
        "type": "text",
        "text": (
            f"IMAGE GENERATION PROMPT used for this frame:\n\n"
            f"{image_prompt}\n\n"
            f"Review the generated frame against the prompt and reference images. "
            f"Respond with PASS or EDIT: <instruction>."
        ),
    })

    payload = {
        "model": GROK_VISION_MODEL,
        "messages": [
            {"role": "system", "content": REVIEWER_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        "max_tokens": 300,
        "temperature": 0.2,
    }

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=None) as client:
        resp = await client.post(
            f"{XAI_BASE_URL}/chat/completions",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()

    answer = data["choices"][0]["message"]["content"].strip()

    if answer.upper().startswith("PASS"):
        return None

    # Extract edit instruction
    if answer.upper().startswith("EDIT:"):
        return answer[5:].strip()

    # If Grok returned something else, treat as edit prompt if non-empty
    if len(answer) > 10:
        return answer

    return None


# ---------------------------------------------------------------------------
# nano-banana-2 edit call
# ---------------------------------------------------------------------------

def _adapt_input_for_model(model: str, base: dict) -> dict:
    """Adapt prediction input per model schema (mirrors grid_generate)."""
    inp = dict(base)
    if model == "google/nano-banana-2":
        inp.pop("safety_filter_level", None)
        inp.pop("allow_fallback_model", None)
    elif model == "google/nano-banana-pro":
        inp.pop("google_search", None)
        inp.pop("image_search", None)
        inp.setdefault("safety_filter_level", "block_only_high")
        inp.setdefault("allow_fallback_model", True)
    return inp


async def _edit_frame_nano_banana(
    cell_image_path: Path,
    edit_prompt: str,
    ref_image_paths: list[Path],
    output_path: Path,
) -> dict:
    """Send cell image + edit prompt + references to nano-banana with fallback.

    Tries each model in IMAGE_MODEL_CHAIN. The cell image is the primary
    image_input. Reference images are appended so the model can see
    what the characters/props/location should look like.

    Returns result dict with model, elapsed, success, etc.
    """
    token = REPLICATE_API_TOKEN or os.getenv("REPLICATE_API_TOKEN", "")
    if not token:
        raise RuntimeError("REPLICATE_API_TOKEN required for frame correction")

    # Build image_input: cell first, then references (max 5 to keep payload sane)
    image_input = [_image_to_data_url(cell_image_path)]
    for ref_path in ref_image_paths[:5]:
        if ref_path.exists():
            image_input.append(_image_to_data_url(ref_path))

    base_input = {
        "prompt": edit_prompt,
        "image_input": image_input,
        "aspect_ratio": "16:9",
        "resolution": "4K",
        "output_format": "png",
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Prefer": "wait",
    }

    t0 = time.monotonic()
    last_error = "all models failed"

    async with httpx.AsyncClient(timeout=None) as client:
        for model in IMAGE_MODEL_CHAIN:
            adapted = _adapt_input_for_model(model, base_input)
            _log("edit", f"Trying {model}...")

            max_retries = 2
            for attempt in range(1, max_retries + 1):
                try:
                    resp = await client.post(
                        f"https://api.replicate.com/v1/models/{model}/predictions",
                        json={"input": adapted},
                        headers=headers,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                except httpx.HTTPStatusError as exc:
                    _log("edit", f"{model} HTTP {exc.response.status_code}")
                    last_error = f"HTTP {exc.response.status_code}"
                    if exc.response.status_code in (502, 503, 429) and attempt < max_retries:
                        await asyncio.sleep(5 * attempt)
                        continue
                    break

                pid = data.get("id", "")
                if data.get("status") != "succeeded":
                    poll_url = f"https://api.replicate.com/v1/predictions/{pid}"
                    poll_headers = {"Authorization": f"Bearer {token}"}
                    for _ in range(120):
                        await asyncio.sleep(5)
                        poll_resp = await client.get(poll_url, headers=poll_headers)
                        poll_resp.raise_for_status()
                        data = poll_resp.json()
                        if data.get("status") in ("succeeded", "failed", "canceled"):
                            break

                if data.get("status") == "succeeded":
                    output_url = data.get("output")
                    if isinstance(output_url, list):
                        output_url = output_url[0]

                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = output_path.with_suffix(".tmp.png")
                    async with client.stream("GET", output_url) as dl:
                        dl.raise_for_status()
                        with open(tmp, "wb") as f:
                            async for chunk in dl.aiter_bytes(8192):
                                f.write(chunk)
                    os.replace(tmp, output_path)

                    return {
                        "success": True,
                        "model": model,
                        "prediction_id": pid,
                        "elapsed_s": round(time.monotonic() - t0, 1),
                    }

                err = data.get("error") or data.get("status", "unknown")
                _log("edit", f"{model} prediction {pid}: status={data.get('status')}, error={err}")
                last_error = str(err)
                break  # Don't retry failed predictions, try next model

    return {
        "success": False,
        "error": last_error,
        "elapsed_s": round(time.monotonic() - t0, 1),
    }


# ---------------------------------------------------------------------------
# Single frame correction
# ---------------------------------------------------------------------------

async def correct_frame(
    frame_id: str,
    cell_image_path: Path,
    image_prompt: str,
    ref_image_paths: list[Path],
    project_dir: Path,
) -> dict:
    """Review and optionally correct a single frame.

    Args:
        frame_id: Frame identifier
        cell_image_path: Path to the extracted grid cell image
        image_prompt: The image generation prompt for this frame
        ref_image_paths: List of absolute paths to reference images
        project_dir: Project root directory

    Returns:
        dict with: frame_id, action ("pass"|"corrected"|"failed"),
        edit_prompt, review_elapsed_s, edit_elapsed_s
    """
    result = {
        "frame_id": frame_id,
        "action": "pass",
        "edit_prompt": None,
        "review_elapsed_s": 0,
        "edit_elapsed_s": 0,
    }

    if not cell_image_path.exists():
        _log(frame_id, f"Cell image not found: {cell_image_path}")
        result["action"] = "skipped:no_image"
        return result

    # Step 1: Grok vision review
    _log(frame_id, "Reviewing via Grok vision...")
    t0 = time.monotonic()
    try:
        edit_prompt = await _review_frame(
            cell_image_path, image_prompt, ref_image_paths
        )
    except Exception as e:
        _log(frame_id, f"Review failed: {e}")
        result["action"] = f"failed:review:{type(e).__name__}"
        result["review_elapsed_s"] = round(time.monotonic() - t0, 1)
        return result

    result["review_elapsed_s"] = round(time.monotonic() - t0, 1)

    if edit_prompt is None:
        _log(frame_id, f"PASS ({result['review_elapsed_s']}s)")
        return result

    _log(frame_id, f"CORRECTION NEEDED ({result['review_elapsed_s']}s): {edit_prompt[:80]}...")
    result["edit_prompt"] = edit_prompt

    # Step 2: nano-banana-2 edit
    # Save original before overwriting
    backup = cell_image_path.with_suffix(".pre_correction.png")
    if not backup.exists():
        import shutil
        shutil.copy2(cell_image_path, backup)

    t1 = time.monotonic()
    try:
        edit_result = await _edit_frame_nano_banana(
            cell_image_path, edit_prompt, ref_image_paths, cell_image_path
        )
    except Exception as e:
        _log(frame_id, f"Edit failed: {e}")
        result["action"] = f"failed:edit:{type(e).__name__}"
        result["edit_elapsed_s"] = round(time.monotonic() - t1, 1)
        return result

    result["edit_elapsed_s"] = round(time.monotonic() - t1, 1)

    if edit_result.get("success"):
        result["action"] = "corrected"
        result["edit_model"] = edit_result.get("model")
        result["edit_prediction_id"] = edit_result.get("prediction_id")
        _log(frame_id, f"Corrected ({result['edit_elapsed_s']}s)")
    else:
        result["action"] = f"failed:edit:{edit_result.get('error', 'unknown')}"
        _log(frame_id, f"Edit failed: {edit_result.get('error')}")

    return result


# ---------------------------------------------------------------------------
# Batch correction — all frames in a project
# ---------------------------------------------------------------------------

async def correct_all_frames(
    project_dir: Path,
    *,
    concurrency: int = 3,
) -> dict:
    """Review and correct all storyboard frame cells in a project.

    Loads the graph, resolves reference images for each frame,
    and pipelines review → edit for each cell.

    Args:
        project_dir: Project root directory
        concurrency: Max parallel frame corrections

    Returns:
        Summary dict with counts
    """
    from .store import GraphStore
    from .prompt_assembler import resolve_ref_images, assemble_image_prompt

    store = GraphStore(project_dir / "graph")
    graph = store.load()

    if not graph.frame_order:
        return {"passed": 0, "corrected": 0, "failed": 0, "skipped": 0}

    sem = asyncio.Semaphore(concurrency)
    stats = {"passed": 0, "corrected": 0, "failed": 0, "skipped": 0}

    async def _process_one(frame_id: str):
        async with sem:
            # Get cell image path
            from .api import get_frame_cell_image
            cell_rel = get_frame_cell_image(graph, frame_id)
            if not cell_rel:
                stats["skipped"] += 1
                return

            cell_path = project_dir / cell_rel
            if not cell_path.exists():
                stats["skipped"] += 1
                return

            # Get image prompt
            try:
                img_prompt_data = assemble_image_prompt(
                    graph, frame_id, project_dir=project_dir
                )
                image_prompt = img_prompt_data.get("prompt", "")
            except Exception:
                image_prompt = ""

            # Get reference images (absolute paths)
            ref_rels = resolve_ref_images(
                graph, frame_id, project_dir=project_dir
            )
            ref_paths = []
            for r in ref_rels:
                p = _resolve_abs(r, project_dir)
                if p.exists():
                    ref_paths.append(p)

            result = await correct_frame(
                frame_id, cell_path, image_prompt, ref_paths, project_dir
            )

            action = result["action"]
            if action == "pass":
                stats["passed"] += 1
            elif action == "corrected":
                stats["corrected"] += 1
            elif action.startswith("skipped"):
                stats["skipped"] += 1
            else:
                stats["failed"] += 1

            # Save correction report
            reports_dir = project_dir / "frames" / "correction_reports"
            reports_dir.mkdir(parents=True, exist_ok=True)
            (reports_dir / f"{frame_id}.json").write_text(
                json.dumps(result, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    await asyncio.gather(*[_process_one(fid) for fid in graph.frame_order])

    _log("Batch", f"Done: {stats['passed']} passed, {stats['corrected']} corrected, "
         f"{stats['failed']} failed, {stats['skipped']} skipped")
    return stats
