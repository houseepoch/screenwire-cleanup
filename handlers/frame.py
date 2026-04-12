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
import re
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .base import BaseHandler, classify_replicate_error
from .models import (
    RESOLUTION_SPECS,
    FrameInput,
    FrameOutput,
)
from .reference_pack import build_reference_pack, prompt_image_retry_threshold

logger = logging.getLogger("handlers.frame")


LOCAL_FRAME_RENDER_SIZES = {
    ("16:9", "4K"): (3840, 2160),
    ("16:9", "2K"): (2048, 1152),
}


def _local_frame_mode(prompt: str) -> str | None:
    lowered = prompt.lower()
    if "output clean pure black only" in lowered:
        return "pure_black"
    if "white loading spinner centered on pure black" in lowered:
        return "loading_wheel"
    if "authored title-card insert" in lowered:
        return "title_card"
    return None


def _frame_dimensions(aspect_ratio: str | None, resolution: str | None) -> tuple[int, int]:
    return LOCAL_FRAME_RENDER_SIZES.get((aspect_ratio or "16:9", resolution or "4K"), (3840, 2160))


def _load_title_font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size=size)
    except Exception:
        return ImageFont.load_default()


def _extract_title_text(prompt: str) -> str:
    matches = [m.strip() for m in re.findall(r"[\"']([^\"']{3,160})[\"']", prompt)]
    if matches:
        return max(matches, key=len)
    for line in prompt.splitlines():
        cleaned = line.strip().strip("-").strip()
        if not cleaned:
            continue
        if cleaned.upper() == cleaned and any(ch.isalpha() for ch in cleaned):
            return cleaned
    return "TITLE CARD"


def _render_local_special_frame(prompt: str, output_path: Path, aspect_ratio: str, resolution: str) -> str | None:
    mode = _local_frame_mode(prompt)
    if mode is None:
        return None

    width, height = _frame_dimensions(aspect_ratio, resolution)
    image = Image.new("RGB", (width, height), "black")
    draw = ImageDraw.Draw(image)
    cx, cy = width // 2, height // 2

    if mode == "loading_wheel":
        radius = min(width, height) // 10
        ring_width = max(16, width // 120)
        bbox = (cx - radius, cy - radius, cx + radius, cy + radius)
        draw.arc(bbox, start=20, end=320, fill="white", width=ring_width)
        image.save(output_path)
        return "local/loading_wheel"

    if mode == "title_card":
        title = _extract_title_text(prompt)
        font = _load_title_font(max(64, width // 18))
        glow_font = _load_title_font(max(72, width // 17))
        text_bbox = draw.multiline_textbbox((0, 0), title, font=font, spacing=12, align="center")
        text_w = text_bbox[2] - text_bbox[0]
        text_h = text_bbox[3] - text_bbox[1]
        x = (width - text_w) / 2
        y = (height - text_h) / 2
        glow_color = (255, 110, 32)
        for dx, dy in ((-6, 0), (6, 0), (0, -6), (0, 6)):
            draw.multiline_text((x + dx, y + dy), title, font=glow_font, fill=glow_color, align="center", spacing=12)
        draw.multiline_text((x, y), title, font=font, fill="white", align="center", spacing=12)
        image.save(output_path)
        return "local/title_card"

    image.save(output_path)
    return "local/pure_black"


def _should_retry_with_prompt_image(prompt: str, detail: dict[str, object]) -> bool:
    if len(prompt) < prompt_image_retry_threshold():
        return False
    return str(detail.get("failure_type", "")) in {"UPSTREAM_TRANSIENT", "MODEL_ERROR", "TIMEOUT"}


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

        local_model_used = _render_local_special_frame(
            inp.prompt,
            output_path,
            spec.aspect_ratio or "16:9",
            spec.resolution or "4K",
        )
        if local_model_used:
            return FrameOutput(
                success=True,
                frame_id=inp.frame_id,
                image_path=output_path,
                model_used=local_model_used,
                resolution=spec.resolution or "4K",
                downshifted=False,
                elapsed_s=time.monotonic() - t0,
                refs_used=[str(p) for p in inp.reference_images if p.exists()],
            )

        # Collect reference images: storyboard cell FIRST (primary composition
        # reference), then generic refs (cast, location, props, previous frame).
        ref_paths: list[Path] = []
        if inp.storyboard_image and inp.storyboard_image.exists():
            ref_paths.append(inp.storyboard_image)
        ref_paths.extend(p for p in inp.reference_images if p.exists())

        packed_refs = build_reference_pack(
            pack_dir=inp.output_dir / "frames" / "ref_packs" / inp.frame_id,
            prompt_text=inp.prompt,
            reference_images=[p for p in inp.reference_images if p.exists()],
            storyboard_image=inp.storyboard_image if inp.storyboard_image and inp.storyboard_image.exists() else None,
        )
        upload_paths: list[Path] = []
        if packed_refs.storyboard_image:
            upload_paths.append(packed_refs.storyboard_image)
        upload_paths.extend(packed_refs.reference_images)

        # Upload references as data URIs
        image_uris: list[str] = []
        if upload_paths:
            image_uris = await self.upload_many(upload_paths)
            logger.info(
                "Uploaded %d reference images for frame %s (%s storyboard)",
                len(image_uris),
                inp.frame_id,
                "with" if packed_refs.storyboard_image else "without",
            )

        # Build prediction input
        full_prompt_input: dict = {
            "prompt": inp.prompt,
            "aspect_ratio": spec.aspect_ratio,  # "16:9"
            "resolution": spec.resolution,  # "4K"
            "output_format": inp.output_format,
        }
        pred_input: dict = dict(full_prompt_input)
        if image_uris:
            pred_input["image_input"] = image_uris
        if inp.seed is not None:
            full_prompt_input["seed"] = inp.seed
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

            if _should_retry_with_prompt_image(inp.prompt, detail):
                logger.info(
                    "Retrying frame %s with prompt-sheet overflow image (%d chars -> split)",
                    inp.frame_id,
                    len(inp.prompt),
                )
                retry_pack = build_reference_pack(
                    pack_dir=inp.output_dir / "frames" / "ref_packs" / f"{inp.frame_id}_prompt_image",
                    prompt_text=inp.prompt,
                    reference_images=[p for p in inp.reference_images if p.exists()],
                    storyboard_image=inp.storyboard_image if inp.storyboard_image and inp.storyboard_image.exists() else None,
                    include_prompt_image=True,
                )
                retry_upload_paths: list[Path] = []
                if retry_pack.storyboard_image:
                    retry_upload_paths.append(retry_pack.storyboard_image)
                retry_upload_paths.extend(retry_pack.reference_images)
                retry_image_uris = await self.upload_many(retry_upload_paths) if retry_upload_paths else []
                retry_input = dict(full_prompt_input)
                retry_input["prompt"] = retry_pack.prompt_text
                if retry_image_uris:
                    retry_input["image_input"] = retry_image_uris
                prediction, model_used = await self._run_model_chain(
                    self.handler_name,
                    retry_input,
                    extra_headers=request_headers,
                )
                pred_input = retry_input
                upload_paths = retry_upload_paths
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

            if prediction.get("status") != "succeeded":
                xai_rescue = await self._try_xai_image_rescue(
                    full_prompt_input,
                    reference_paths=ref_paths,
                    output_path=output_path,
                    error_detail=detail,
                    sensitive_context=inp.sensitive_context,
                    extra_headers=request_headers,
                )
                if xai_rescue:
                    prediction, model_used = xai_rescue
                    downshifted = (spec.resolution or "").upper() == "4K"
                    logger.info(
                        "Grok Imagine rescue succeeded for %s via %s",
                        inp.frame_id,
                        model_used,
                    )

        # ── Handle final failure ───────────────────────────────
        if prediction.get("status") != "succeeded":
            error_msg = prediction.get("error", "Generation failed")
            logs = prediction.get("logs", "")
            return FrameOutput(
                success=False,
                frame_id=inp.frame_id,
                model_used=model_used,
                error=error_msg,
                error_detail=classify_replicate_error(error_msg, logs),
                elapsed_s=time.monotonic() - t0,
            )

        # Download output
        local_output_path = prediction.get("local_output_path")
        if local_output_path:
            output_path = Path(local_output_path)
        else:
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

        refs_used = [str(p) for p in upload_paths]

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
