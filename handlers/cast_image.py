"""
Cast Image Handler — Generate cast member reference composites.

Model: prunaai/p-image (sub-1s, always outputs JPG)
Post-process: IF live-action style -> prunaai/p-image-upscale
    with enhance_realism=true, enhance_details=true, target 2 MP, output PNG.
Aspect: 1:1 | No reference images (root asset)
Output: cast/composites/{cast_id}_ref.png

@AI_STATUS:COMPLETE
@AI_DEPENDS: handlers/base.py:BaseHandler
@AI_DEPENDS: handlers/models.py:CastImageInput,CastImageOutput,is_live_action
@AI_WARN: p-image ALWAYS outputs JPG regardless of any output_format param.
    The upscaler converts to PNG when upscaling. For non-live-action styles
    the raw JPG is saved (extension will be .png per spec but content is JPG).
    If strict format matters, add a PIL conversion step.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from .base import BaseHandler, classify_replicate_error
from .models import (
    MODEL_ROUTES,
    RESOLUTION_SPECS,
    CastImageInput,
    CastImageOutput,
    is_live_action,
)

logger = logging.getLogger("handlers.cast_image")


class CastImageHandler(BaseHandler):
    """
    Generate full-body cast member reference composites.

    Two-step pipeline:
    1. Generate base image with prunaai/p-image at 1:1
    2. If media_style is live-action, upscale with prunaai/p-image-upscale
       to 2 megapixels with enhance_realism + enhance_details
    """

    handler_name = "cast_image"

    async def generate(self, inp: CastImageInput) -> CastImageOutput:
        t0 = time.monotonic()
        route = MODEL_ROUTES[self.handler_name]
        spec = RESOLUTION_SPECS[self.handler_name]

        # Build output path
        out_dir = inp.output_dir / "cast" / "composites"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"{inp.cast_id}_ref.png"

        # ── Step 1: Generate with p-image at 1:1 ──────────────

        pred_input: dict = {
            "prompt": inp.prompt,
            "aspect_ratio": spec.aspect_ratio,  # "1:1"
        }
        if inp.seed is not None:
            pred_input["seed"] = inp.seed
        request_headers = self._build_request_headers(
            run_id=inp.run_id or "",
            phase=inp.phase,
            asset_id=inp.cast_id,
        )

        try:
            prediction, model_used = await self._run_model_chain(
                self.handler_name,
                pred_input,
                extra_headers=request_headers,
            )
        except Exception as exc:
            return CastImageOutput(
                success=False,
                cast_id=inp.cast_id,
                error=str(exc),
                elapsed_s=time.monotonic() - t0,
            )

        if prediction.get("status") != "succeeded":
            error_msg = prediction.get("error", "Generation failed")
            return CastImageOutput(
                success=False,
                cast_id=inp.cast_id,
                model_used=model_used,
                error=error_msg,
                error_detail=classify_replicate_error(error_msg),
                elapsed_s=time.monotonic() - t0,
            )

        # Download the base image (JPG from p-image)
        output_url = self.extract_output_url(prediction)
        if not output_url:
            return CastImageOutput(
                success=False,
                cast_id=inp.cast_id,
                model_used=model_used,
                error="No output URL in prediction response",
                elapsed_s=time.monotonic() - t0,
            )

        await self.download_output(output_url, output_path)
        logger.info("Base image generated for cast %s via %s", inp.cast_id, model_used)

        # ── Step 2: Upscale if live-action ─────────────────────

        upscaled = False
        upscale_model: str | None = None

        if is_live_action(inp.media_style) and route.upscale:
            upscale_model = route.upscale
            logger.info(
                "Live-action style '%s' detected — upscaling %s to 2 MP",
                inp.media_style,
                inp.cast_id,
            )

            try:
                image_uri = await self.upload_to_replicate(output_path)
                upscale_input: dict = {
                    "image": image_uri,
                    "mode": "target",
                    "target_megapixels": spec.target_megapixels or 2,
                    "enhance_realism": True,
                    "enhance_details": True,
                    "output_format": "png",
                }

                up_pred = await self._replicate_predict(
                    upscale_model,
                    upscale_input,
                    extra_headers=request_headers,
                )
                up_pred = await self._resolve_prediction(
                    up_pred,
                    extra_headers=request_headers,
                )

                if up_pred.get("status") == "succeeded":
                    up_url = self.extract_output_url(up_pred)
                    if up_url:
                        await self.download_output(up_url, output_path)
                        upscaled = True
                        logger.info("Upscale complete for %s", inp.cast_id)
                else:
                    logger.warning(
                        "Upscale failed for %s — keeping base image: %s",
                        inp.cast_id,
                        up_pred.get("error", "unknown"),
                    )
            except Exception:
                logger.exception(
                    "Upscale error for %s — keeping base image", inp.cast_id
                )

        return CastImageOutput(
            success=True,
            cast_id=inp.cast_id,
            image_path=output_path,
            upscaled=upscaled,
            model_used=model_used,
            upscale_model_used=upscale_model if upscaled else None,
            elapsed_s=time.monotonic() - t0,
        )

    def _make_error_output(self, inp: CastImageInput, exc: Exception) -> CastImageOutput:
        """Return a typed error output for batch failure isolation."""
        return CastImageOutput(
            success=False,
            cast_id=getattr(inp, "cast_id", ""),
            error=f"{type(exc).__name__}: {exc}",
        )
