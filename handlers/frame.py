"""
Frame Handler — Generate composed frame images at 4K.

Model chain: google/nano-banana-2 -> fallback google/nano-banana-pro.
Aspect: 16:9 | Resolution: 4K.
Reference images: ALL cast/prop/location reference images relevant to the
frame, PLUS the storyboard image as an image input (composition reference).
Capacity rescue: On transient 4K failure, downshift to 2K + allow_fallback.
Output: frames/composed/{frame_id}_gen.png

@AI_STATUS:COMPLETE
@AI_DEPENDS: handlers/base.py:BaseHandler
@AI_DEPENDS: handlers/models.py:FrameInput,FrameOutput
@AI_WARN: 4K generation on nano-banana-pro costs $0.30/image (2x the 2K
    price). Capacity rescue downgrades to 2K — the downshifted flag in
    FrameOutput signals this happened so callers can decide to retry later.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from .base import BaseHandler, classify_replicate_error
from .models import (
    RESOLUTION_SPECS,
    FrameInput,
    FrameOutput,
)

logger = logging.getLogger("handlers.frame")


class FrameHandler(BaseHandler):
    """
    Generate composed frame images with reference-guided generation.

    Pipeline:
    1. Upload all reference images (cast + prop + location + storyboard)
    2. Generate at 16:9 4K via nano-banana-2 (fallback: pro)
    3. On transient 4K failure: capacity rescue — 2K + allow_fallback_model
    """

    handler_name = "frame"

    async def generate(self, inp: FrameInput) -> FrameOutput:
        t0 = time.monotonic()
        spec = RESOLUTION_SPECS[self.handler_name]

        # Build output path
        out_dir = inp.output_dir / "frames" / "composed"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"{inp.frame_id}_gen.{inp.output_format}"

        # Collect reference images: storyboard cell FIRST (primary composition
        # reference), then generic refs (cast, location, props, previous frame).
        ref_paths: list[Path] = []
        if inp.storyboard_image and inp.storyboard_image.exists():
            ref_paths.append(inp.storyboard_image)
        ref_paths.extend(p for p in inp.reference_images if p.exists())

        # Upload references as data URIs
        image_uris: list[str] = []
        if ref_paths:
            image_uris = await self.upload_many(ref_paths)
            logger.info(
                "Uploaded %d reference images for frame %s (%s storyboard)",
                len(image_uris),
                inp.frame_id,
                "with" if inp.storyboard_image else "without",
            )

        # Build prediction input
        pred_input: dict = {
            "prompt": inp.prompt,
            "aspect_ratio": spec.aspect_ratio,  # "16:9"
            "resolution": spec.resolution,  # "4K"
            "output_format": inp.output_format,
        }
        if image_uris:
            pred_input["image_input"] = image_uris
        if inp.seed is not None:
            pred_input["seed"] = inp.seed
        request_headers = self._build_request_headers(
            run_id=inp.run_id or "",
            phase=inp.phase,
            frame_id=inp.frame_id,
        )

        # Run model chain: nano-banana-2 -> nano-banana-pro
        downshifted = False
        model_used = ""

        try:
            prediction, model_used = await self._run_model_chain(
                self.handler_name,
                pred_input,
                extra_headers=request_headers,
            )
        except Exception as exc:
            return FrameOutput(
                success=False,
                frame_id=inp.frame_id,
                error=str(exc),
                elapsed_s=time.monotonic() - t0,
            )

        # ── 4K capacity rescue ─────────────────────────────────
        if prediction.get("status") != "succeeded":
            error_msg = prediction.get("error", "")
            logs = prediction.get("logs", "")
            detail = classify_replicate_error(error_msg, logs)

            if detail["failure_type"] == "UPSTREAM_TRANSIENT":
                logger.info(
                    "Chain failed at 4K for %s — attempting capacity rescue (2K + fallback)",
                    inp.frame_id,
                )
                rescue = await self._try_capacity_rescue(
                    pred_input,
                    extra_headers=request_headers,
                )
                if rescue:
                    prediction, model_used = rescue
                    downshifted = True
                    logger.info(
                        "Capacity rescue succeeded for %s at 2K via %s",
                        inp.frame_id,
                        model_used,
                    )

        # ── Handle final failure ───────────────────────────────
        if prediction.get("status") != "succeeded":
            error_msg = prediction.get("error", "Generation failed")
            return FrameOutput(
                success=False,
                frame_id=inp.frame_id,
                model_used=model_used,
                error=error_msg,
                error_detail=classify_replicate_error(error_msg),
                elapsed_s=time.monotonic() - t0,
            )

        # Download output
        output_url = self.extract_output_url(prediction)
        if not output_url:
            return FrameOutput(
                success=False,
                frame_id=inp.frame_id,
                model_used=model_used,
                error="No output URL in prediction response",
                elapsed_s=time.monotonic() - t0,
            )

        await self.download_output(output_url, output_path)

        actual_res = "2K" if downshifted else (spec.resolution or "4K")
        logger.info(
            "Frame %s generated at %s %s via %s%s",
            inp.frame_id,
            spec.aspect_ratio,
            actual_res,
            model_used,
            " (downshifted)" if downshifted else "",
        )

        refs_used = [str(p) for p in ref_paths]

        return FrameOutput(
            success=True,
            frame_id=inp.frame_id,
            image_path=output_path,
            model_used=model_used,
            resolution=actual_res,
            downshifted=downshifted,
            elapsed_s=time.monotonic() - t0,
            refs_used=refs_used,
        )

    def _make_error_output(self, inp: FrameInput, exc: Exception) -> FrameOutput:
        """Return a typed error output for batch failure isolation."""
        return FrameOutput(
            success=False,
            frame_id=getattr(inp, "frame_id", ""),
            error=f"{type(exc).__name__}: {exc}",
        )
