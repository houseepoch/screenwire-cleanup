"""
Frame Prompt Refiner — Grok Vision per-frame video prompt grounding
====================================================================

Takes a rendered frame image + the deterministic video prompt assembled
from graph data and sends both to Grok's vision model (xAI API) to
produce a refined video prompt that is coherent with what is actually
visible in the frame.

The deterministic prompt carries narrative intent (dialogue, blocking,
emotional beats). The vision pass grounds that intent in the real pixel
content — fixing hallucinated positions, correcting character counts,
and tightening camera/motion cues to match the actual composition.

Flow:
  1. assemble_video_prompt() builds the graph-derived prompt (unchanged)
  2. refine_video_prompt() sends frame image + graph prompt to Grok vision
  3. The refined prompt replaces the original in the video JSON on disk
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
from typing import Any, Optional

from llm.xai_client import XAIClient, build_prompt_cache_key

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

XAI_API_KEY = os.getenv("XAI_API_KEY", "")
GROK_VISION_MODEL = "grok-4-1-fast-reasoning"

# Max tokens for the refined prompt response body.
MAX_REFINE_TOKENS = 1200


def _log(tag: str, msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"  [{ts}] [FrameRefiner:{tag}] {msg}")


# ---------------------------------------------------------------------------
# Image encoding
# ---------------------------------------------------------------------------

def _image_to_data_url(path: Path) -> str:
    """Encode a local image file as a data URL for the vision API."""
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    data = base64.b64encode(path.read_bytes()).decode()
    return f"data:{mime};base64,{data}"


def _cast_bible_context(video_prompt: dict[str, Any]) -> str:
    snapshot = video_prompt.get("cast_bible_snapshot")
    if not isinstance(snapshot, dict):
        return ""

    lines: list[str] = []
    for character in snapshot.get("characters", []):
        pose = character.get("pose") or {}
        pose_name = (pose.get("pose") or "").strip()
        if not pose_name:
            continue
        name = (character.get("name") or character.get("character_id") or "Character").strip()
        modifiers = [
            str(item).strip()
            for item in pose.get("modifiers", [])
            if str(item).strip()
        ]
        line = f"- {name}: locked pose {pose_name}"
        if modifiers:
            line += f" | modifiers: {', '.join(modifiers)}"
        lines.append(line)

    if not lines:
        return ""

    return (
        "Locked cast bible pose references for this frame:\n"
        + "\n".join(lines)
        + "\nPreserve these pose locks unless the prompt explicitly calls for a transition."
    )


# ---------------------------------------------------------------------------
# System prompt for the vision refiner
# ---------------------------------------------------------------------------

REFINER_SYSTEM_PROMPT = """\
You are a video prompt refiner for a cinematic AI pipeline. You receive:
1. A rendered storyboard frame image
2. A graph-assembled video prompt containing narrative intent

Your job: produce a REFINED video prompt that is grounded in what is \
actually visible in the frame image while preserving the narrative intent.

Rules:
- LOOK at the image carefully. Describe what you actually see, not what \
  the graph prompt claims.
- Preserve dialogue lines exactly as given — never alter spoken words.
- Preserve the audio/ambient section — it comes from the script.
- Fix character positions, counts, poses, and facing directions to match \
  the actual image.
- Tighten camera motion cues to match the actual composition (e.g. if the \
  frame shows a close-up, don't describe a wide establishing shot).
- Keep the emotional beat and dramatic purpose — just ground them in the \
  visible scene.
- Keep the prompt under 4000 characters (grok-video limit).
- Output ONLY the refined prompt text. No commentary, no markdown, no \
  explanation.
- Write in the same style as the input prompt: present-tense, cinematic \
  direction language.
"""

REFINER_CACHE_KEY = build_prompt_cache_key("frame-prompt-refiner", REFINER_SYSTEM_PROMPT)


# ---------------------------------------------------------------------------
# Core API call
# ---------------------------------------------------------------------------

async def _call_grok_vision(
    image_path: Path,
    graph_prompt: str,
    *,
    cast_bible_context: str = "",
    api_key: str = "",
    timeout_s: float | None = None,
) -> str:
    """Send frame image + graph prompt to Grok vision, return refined prompt."""
    key = api_key or XAI_API_KEY
    if not key:
        raise RuntimeError(
            "XAI_API_KEY not set — required for Grok vision frame refinement"
        )

    image_url = _image_to_data_url(image_path)

    messages = [
        {"role": "system", "content": REFINER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": image_url},
                },
                {
                    "type": "text",
                    "text": (
                        "Here is the graph-assembled video prompt for this frame:\n\n"
                        + f"{graph_prompt}\n\n"
                        + (
                            f"{cast_bible_context}\n\n"
                            if cast_bible_context
                            else ""
                        )
                        + "Refine this prompt based on what you actually see in the "
                        + "image. Output only the refined prompt."
                    ),
                },
            ],
        },
    ]

    payload = {
        "messages": messages,
    }

    client = XAIClient(api_key=key)
    return await client.generate_text(
        messages=payload["messages"],
        model=GROK_VISION_MODEL,
        task_hint="vision_prompt_refinement",
        temperature=0.3,
        max_tokens=MAX_REFINE_TOKENS,
        cache_key=REFINER_CACHE_KEY,
    )


# ---------------------------------------------------------------------------
# Single frame refinement
# ---------------------------------------------------------------------------

async def refine_video_prompt(
    video_prompt: dict,
    project_dir: Path,
    *,
    api_key: str = "",
) -> dict:
    """Refine a single video prompt dict using the frame's rendered image.

    Args:
        video_prompt: The dict returned by assemble_video_prompt()
        project_dir: Project root (frame images are relative to this)
        api_key: Optional XAI API key override

    Returns:
        Updated video_prompt dict with:
          - prompt: the vision-refined text
          - original_graph_prompt: the pre-refinement prompt (preserved)
          - refined_by: "grok-vision"
    """
    frame_id = video_prompt["frame_id"]
    image_rel = video_prompt.get("input_image_path", "")
    image_path = project_dir / image_rel

    if not image_path.exists():
        _log(frame_id, f"Frame image not found at {image_path}, skipping refinement")
        video_prompt["refined_by"] = "skipped:no_image"
        return video_prompt

    if not (api_key or XAI_API_KEY):
        _log(frame_id, "XAI_API_KEY missing, skipping refinement")
        video_prompt["refined_by"] = "skipped:no_api_key"
        return video_prompt

    graph_prompt = video_prompt["prompt"]
    cast_bible_context = _cast_bible_context(video_prompt)
    _log(frame_id, f"Refining via {GROK_VISION_MODEL} ({len(graph_prompt)} chars)")

    t0 = time.monotonic()
    try:
        refined = await _call_grok_vision(
            image_path,
            graph_prompt,
            cast_bible_context=cast_bible_context,
            api_key=api_key,
        )
    except Exception as e:
        _log(frame_id, f"Vision refinement failed: {e}")
        video_prompt["refined_by"] = f"failed:{type(e).__name__}"
        return video_prompt

    elapsed = round(time.monotonic() - t0, 1)

    video_prompt["original_graph_prompt"] = graph_prompt
    video_prompt["prompt"] = refined
    video_prompt["refined_by"] = "grok-vision"
    video_prompt["refine_model"] = GROK_VISION_MODEL
    video_prompt["refine_elapsed_s"] = elapsed

    _log(frame_id, f"Refined: {len(graph_prompt)} → {len(refined)} chars ({elapsed}s)")
    return video_prompt


# ---------------------------------------------------------------------------
# Batch refinement — all frames in a project
# ---------------------------------------------------------------------------

async def refine_all_video_prompts(
    project_dir: Path,
    *,
    api_key: str = "",
    concurrency: int = 3,
) -> dict:
    """Refine all video prompt JSONs under project_dir/video/prompts/.

    Reads each *_video.json, calls Grok vision, writes the refined version
    back. The original graph prompt is preserved in 'original_graph_prompt'.

    Args:
        project_dir: Project root directory
        api_key: Optional XAI API key override
        concurrency: Max parallel Grok vision calls (rate-limit friendly)

    Returns:
        Summary dict with counts
    """
    prompt_dir = project_dir / "video" / "prompts"
    if not prompt_dir.exists():
        return {"refined": 0, "skipped": 0, "failed": 0, "error": "no video/prompts dir"}

    json_files = sorted(prompt_dir.glob("*_video.json"))
    if not json_files:
        return {"refined": 0, "skipped": 0, "failed": 0}

    sem = asyncio.Semaphore(concurrency)
    results = {"refined": 0, "skipped": 0, "failed": 0}

    async def _process_one(json_path: Path):
        async with sem:
            data = json.loads(json_path.read_text(encoding="utf-8"))

            # Skip already-refined prompts
            if data.get("refined_by") == "grok-vision":
                results["skipped"] += 1
                return

            refined = await refine_video_prompt(
                data, project_dir, api_key=api_key
            )

            if refined.get("refined_by") == "grok-vision":
                results["refined"] += 1
            elif "failed" in refined.get("refined_by", ""):
                results["failed"] += 1
            else:
                results["skipped"] += 1

            json_path.write_text(
                json.dumps(refined, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    await asyncio.gather(*[_process_one(f) for f in json_files])

    _log("Batch", f"Done: {results['refined']} refined, "
         f"{results['skipped']} skipped, {results['failed']} failed")
    return results
