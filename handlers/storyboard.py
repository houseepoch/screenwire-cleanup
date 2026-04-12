"""
Storyboard Handler — Generate storyboard composites with cell extraction.

Model chain: google/nano-banana-2 -> fallback google/nano-banana-pro.
Aspect: 1:1 | Resolution: 2K.
Reference images: ALL present cast composites, prop images, and location
images for frames present in the storyboard — sent as image_input[].
Output: storyboard composite + individual cell extraction via PIL.

@AI_STATUS:COMPLETE
@AI_DEPENDS: handlers/base.py:BaseHandler
@AI_DEPENDS: handlers/models.py:StoryboardInput,StoryboardOutput
@AI_WARN: Cell extraction assumes the generated image is a uniform grid.
    If the model generates a non-grid composition, cells may be mis-cropped.
    The layout string (e.g. "2x2") MUST match the prompt's grid instructions.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from PIL import Image

from .base import BaseHandler, classify_replicate_error
from .models import (
    RESOLUTION_SPECS,
    StoryboardInput,
    StoryboardOutput,
)

logger = logging.getLogger("handlers.storyboard")


class StoryboardHandler(BaseHandler):
    """
    Generate storyboard grid composites and extract individual cells.

    Pipeline:
    1. Upload all reference images (cast/prop/location) as image_input[]
    2. Generate composite via nano-banana-2 (fallback: pro) at 1:1, 2K
    3. Crop individual cells from the composite based on grid layout
    """

    handler_name = "storyboard"

    async def generate(self, inp: StoryboardInput) -> StoryboardOutput:
        t0 = time.monotonic()
        spec = RESOLUTION_SPECS[self.handler_name]

        # Build output paths
        out_dir = inp.output_dir / "storyboards"
        out_dir.mkdir(parents=True, exist_ok=True)
        composite_path = out_dir / f"{inp.grid_id}_composite.{inp.output_format}"
        cells_dir = out_dir / "cells" / inp.grid_id
        cells_dir.mkdir(parents=True, exist_ok=True)

        # Upload reference images (cast composites, prop images, location images)
        image_uris: list[str] = []
        if inp.reference_images:
            existing = [p for p in inp.reference_images if p.exists()]
            if existing:
                image_uris = await self.upload_many(existing)
                logger.info(
                    "Uploaded %d/%d reference images for storyboard %s",
                    len(image_uris),
                    len(inp.reference_images),
                    inp.grid_id,
                )

        # Build prediction input
        pred_input: dict = {
            "prompt": inp.prompt,
            "aspect_ratio": spec.aspect_ratio,  # "1:1"
            "resolution": spec.resolution,  # "2K"
            "output_format": inp.output_format,
        }
        if image_uris:
            pred_input["image_input"] = image_uris
        if inp.seed is not None:
            pred_input["seed"] = inp.seed
        request_headers = self._build_request_headers(
            run_id=inp.run_id or "",
            phase=inp.phase,
            asset_id=inp.grid_id,
        )

        # Run model chain: nano-banana-2 -> nano-banana-pro
        try:
            prediction, model_used = await self._run_model_chain(
                self.handler_name,
                pred_input,
                extra_headers=request_headers,
            )
        except Exception as exc:
            return StoryboardOutput(
                success=False,
                grid_id=inp.grid_id,
                error=str(exc),
                elapsed_s=time.monotonic() - t0,
            )

        if prediction.get("status") != "succeeded":
            error_msg = prediction.get("error", "Generation failed")
            return StoryboardOutput(
                success=False,
                grid_id=inp.grid_id,
                model_used=model_used,
                error=error_msg,
                error_detail=classify_replicate_error(error_msg),
                elapsed_s=time.monotonic() - t0,
            )

        # Download composite
        output_url = self.extract_output_url(prediction)
        if not output_url:
            return StoryboardOutput(
                success=False,
                grid_id=inp.grid_id,
                model_used=model_used,
                error="No output URL in prediction response",
                elapsed_s=time.monotonic() - t0,
            )

        await self.download_output(output_url, composite_path)
        logger.info(
            "Storyboard composite generated for %s via %s", inp.grid_id, model_used
        )

        # Extract individual cells from composite
        cell_paths = self._extract_cells(
            composite_path, inp.layout, cells_dir, inp.frame_ids
        )
        logger.info(
            "Extracted %d cells from storyboard %s (layout %s)",
            len(cell_paths),
            inp.grid_id,
            inp.layout,
        )

        return StoryboardOutput(
            success=True,
            grid_id=inp.grid_id,
            composite_path=composite_path,
            cell_paths=cell_paths,
            model_used=model_used,
            resolution=spec.resolution or "",
            elapsed_s=time.monotonic() - t0,
        )

    # ── Cell Extraction ────────────────────────────────────────

    @staticmethod
    def _extract_cells(
        composite_path: Path,
        layout: str,
        output_dir: Path,
        frame_ids: list[str],
    ) -> list[Path]:
        """
        Crop individual cells from a storyboard composite image.

        Divides the image into a ``cols x rows`` grid based on the layout
        string (e.g. "2x2", "3x2", "3x3"). Each cell is saved as a
        separate PNG file. If ``frame_ids`` are provided, cells are named
        ``{frame_id}_cell.png``; otherwise ``cell_{row}_{col}.png``.
        """
        try:
            parts = layout.lower().split("x")
            cols, rows = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            logger.error("Invalid layout format '%s' — expected 'CxR'", layout)
            return []

        try:
            img = Image.open(composite_path)
        except Exception:
            logger.exception("Failed to open composite image %s", composite_path)
            return []

        w, h = img.size
        cell_w = w // cols
        cell_h = h // rows

        paths: list[Path] = []
        idx = 0
        for r in range(rows):
            for c in range(cols):
                box = (c * cell_w, r * cell_h, (c + 1) * cell_w, (r + 1) * cell_h)
                cell = img.crop(box)

                # Name by frame_id when available, else by grid position
                if idx < len(frame_ids):
                    name = f"{frame_ids[idx]}_cell.png"
                else:
                    name = f"cell_{r}_{c}.png"

                cell_path = output_dir / name
                cell.save(cell_path)
                paths.append(cell_path)
                idx += 1

        return paths

    def _make_error_output(
        self, inp: StoryboardInput, exc: Exception,
    ) -> StoryboardOutput:
        """Return a typed error output for batch failure isolation."""
        return StoryboardOutput(
            success=False,
            grid_id=getattr(inp, "grid_id", ""),
            error=f"{type(exc).__name__}: {exc}",
        )
