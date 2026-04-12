"""
Video Clip Handler — Generate video clips from composed frames.

Model: xai/grok-imagine-video (via Replicate API).
Resolution: 720p | Duration: suggested_duration clamped to 2-15 seconds.
Prompt: dialogue text prefixed before the motion/action prompt.
Audio: Grok natively generates synchronised dialogue + ambient audio.
Output: video/clips/{frame_id}.mp4

@AI_STATUS:COMPLETE
@AI_DEPENDS: handlers/base.py:BaseHandler
@AI_DEPENDS: handlers/models.py:VideoClipInput,VideoClipOutput
@AI_WARN: Grok video supports 1-15s but pipeline clamps to 2-15s
    (matching server.py behaviour). Duration < 2s produces unreliable
    results. The prompt MUST start with dialogue text for lip-sync.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from .base import BaseHandler, classify_replicate_error
from .models import (
    MODEL_ROUTES,
    VideoClipInput,
    VideoClipOutput,
)

logger = logging.getLogger("handlers.video_clip")

# Duration constraints (matching server.py's clamping)
MIN_DURATION_S = 2
MAX_DURATION_S = 15


class VideoClipHandler(BaseHandler):
    """
    Generate video clips from composed frame images via Grok.

    Pipeline:
    1. Upload the composed frame image as a data URI
    2. Build prompt: dialogue text prefix + motion/action description
    3. Generate video at 720p with clamped duration
    4. Download the output MP4
    """

    handler_name = "video_clip"

    async def generate(self, inp: VideoClipInput) -> VideoClipOutput:
        t0 = time.monotonic()
        route = MODEL_ROUTES[self.handler_name]
        model = route.primary  # xai/grok-imagine-video

        # Build output path
        out_dir = inp.output_dir / "video" / "clips"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"{inp.frame_id}.mp4"

        # Clamp duration to safe range
        duration = max(MIN_DURATION_S, min(MAX_DURATION_S, inp.suggested_duration))

        # Validate frame image exists
        if not inp.frame_image_path.exists():
            return VideoClipOutput(
                success=False,
                frame_id=inp.frame_id,
                error=f"Frame image not found: {inp.frame_image_path}",
                elapsed_s=time.monotonic() - t0,
            )

        # Upload the composed frame
        try:
            image_uri = await self.upload_to_replicate(inp.frame_image_path)
        except Exception as exc:
            return VideoClipOutput(
                success=False,
                frame_id=inp.frame_id,
                error=f"Failed to upload frame image: {exc}",
                elapsed_s=time.monotonic() - t0,
            )

        # Build prompt: dialogue prefix + motion prompt
        # Dialogue comes first so Grok can attempt lip-sync
        prompt_parts: list[str] = []
        if inp.dialogue_text and inp.dialogue_text.strip():
            prompt_parts.append(inp.dialogue_text.strip())
        if inp.motion_prompt and inp.motion_prompt.strip():
            prompt_parts.append(inp.motion_prompt.strip())
        full_prompt = "\n\n".join(prompt_parts) or inp.motion_prompt

        # Build prediction input
        pred_input: dict = {
            "prompt": full_prompt,
            "image": image_uri,
            "duration": duration,
            "resolution": "720p",
        }
        request_headers = self._build_request_headers(
            run_id=inp.run_id or "",
            phase=inp.phase,
            frame_id=inp.frame_id,
        )

        try:
            prediction = await self._replicate_predict(
                model,
                pred_input,
                extra_headers=request_headers,
            )
            prediction = await self._resolve_prediction(
                prediction,
                extra_headers=request_headers,
            )
        except Exception as exc:
            return VideoClipOutput(
                success=False,
                frame_id=inp.frame_id,
                model_used=model,
                error=str(exc),
                duration=duration,
                elapsed_s=time.monotonic() - t0,
            )

        if prediction.get("status") != "succeeded":
            error_msg = prediction.get("error", "Video generation failed")
            return VideoClipOutput(
                success=False,
                frame_id=inp.frame_id,
                model_used=model,
                error=error_msg,
                error_detail=classify_replicate_error(error_msg),
                duration=duration,
                elapsed_s=time.monotonic() - t0,
            )

        # Download video
        output_url = self.extract_output_url(prediction)
        if not output_url:
            return VideoClipOutput(
                success=False,
                frame_id=inp.frame_id,
                model_used=model,
                error="No output URL in prediction response",
                duration=duration,
                elapsed_s=time.monotonic() - t0,
            )

        await self.download_output(output_url, output_path)
        logger.info(
            "Video clip generated for frame %s — %ds at 720p via %s",
            inp.frame_id,
            duration,
            model,
        )

        return VideoClipOutput(
            success=True,
            frame_id=inp.frame_id,
            video_path=output_path,
            model_used=model,
            duration=duration,
            resolution="720p",
            elapsed_s=time.monotonic() - t0,
        )

    def _make_error_output(self, inp: VideoClipInput, exc: Exception) -> VideoClipOutput:
        """Return a typed error output for batch failure isolation."""
        return VideoClipOutput(
            success=False,
            frame_id=getattr(inp, "frame_id", ""),
            error=f"{type(exc).__name__}: {exc}",
        )
