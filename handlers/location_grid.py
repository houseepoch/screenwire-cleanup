"""
Location Grid Handler — Generate 2x2 directional location reference images.

Model: google/nano-banana-pro (direct, NO fallback chain per spec).
Aspect: 16:9 | Resolution: 2K (2048x1152 equivalent).
Grid: 2x2 — NORTH (front), EAST (right), WEST (left), SOUTH (back).
Input: Location definition + preset template.
Output: Single composite grid image.

@AI_STATUS:COMPLETE
@AI_DEPENDS: handlers/base.py:BaseHandler,adapt_input_for_model
@AI_DEPENDS: handlers/models.py:LocationGridInput,LocationGridOutput
@AI_WARN: Cell extraction assumes the generated image is a uniform grid.
@AI_REASONING: No fallback chain — user spec says "direct to pro".
    We call nano-banana-pro directly via _replicate_predict instead
    of _run_model_chain. Tenacity retry still handles transient HTTP
    errors (429/500/503) at the call level.
    Template system injects directional panel descriptions into the
    prompt so the model generates a consistent 2x2 grid.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .base import BaseHandler, adapt_input_for_model, classify_replicate_error
from .models import (
    MODEL_ROUTES,
    RESOLUTION_SPECS,
    LocationGridInput,
    LocationGridOutput,
)

logger = logging.getLogger("handlers.location_grid")

_DIRECTION_PANEL_ORDER: dict[str, tuple[int, int]] = {
    "north": (0, 0),
    "east": (1, 0),
    "west": (0, 1),
    "south": (1, 1),
}


def extract_directional_location_variants(grid_path: Path, location_id: str) -> dict[str, Path]:
    """Extract north/east/west/south crops from a generated 2x2 location grid.

    The location grid includes labels and a title banner near the panel edges.
    We crop inward on all sides so the downstream frame pipeline gets clean
    direction-specific environmental refs instead of the full labeled grid.
    """
    variants_dir = grid_path.parent.parent / "variants"
    variants_dir.mkdir(parents=True, exist_ok=True)

    out: dict[str, Path] = {}
    with Image.open(grid_path) as grid:
        width, height = grid.size
        half_w = width // 2
        half_h = height // 2
        pad_x = max(24, half_w // 12)
        pad_y = max(24, half_h // 10)
        top_row_extra = max(pad_y, half_h // 8)

        for direction, (col, row) in _DIRECTION_PANEL_ORDER.items():
            left = col * half_w
            top = row * half_h
            right = left + half_w
            bottom = top + half_h

            inner_left = left + pad_x
            inner_right = right - pad_x
            inner_top = top + (top_row_extra if row == 0 else pad_y)
            inner_bottom = bottom - pad_y

            crop = grid.crop((inner_left, inner_top, inner_right, inner_bottom))
            out_path = variants_dir / f"{location_id}_{direction}.png"
            crop.save(out_path)
            out[direction] = out_path

    return out


# ── Panel Descriptor ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PanelDesc:
    """One panel in the 2x2 directional grid."""

    direction: str       # e.g. "NORTH"
    position: str        # e.g. "Top-left"
    view_desc: str       # e.g. "front-facing wide establishing shot"
    label_corner: str    # e.g. "bottom-right inner corner"


# ── Preset Templates ─────────────────────────────────────────────

PRESET_TEMPLATES: dict[str, list[PanelDesc]] = {
    "exterior": [
        PanelDesc(
            direction="NORTH",
            position="Top-left",
            view_desc="front-facing wide establishing shot",
            label_corner="bottom-right inner corner",
        ),
        PanelDesc(
            direction="EAST",
            position="Top-right",
            view_desc="right-side wide shot",
            label_corner="bottom-left inner corner",
        ),
        PanelDesc(
            direction="WEST",
            position="Bottom-left",
            view_desc="left-side wide shot",
            label_corner="top-right inner corner",
        ),
        PanelDesc(
            direction="SOUTH",
            position="Bottom-right",
            view_desc="rear/back wide shot",
            label_corner="top-left inner corner",
        ),
    ],
    "interior": [
        PanelDesc(
            direction="NORTH",
            position="Top-left",
            view_desc="interior view looking toward the north wall — full room visible, showing furnishings, architectural details, and ambient lighting from this vantage point",
            label_corner="bottom-right inner corner",
        ),
        PanelDesc(
            direction="EAST",
            position="Top-right",
            view_desc="interior view looking toward the east wall — full room visible, showing furnishings, architectural details, and ambient lighting from this vantage point",
            label_corner="bottom-left inner corner",
        ),
        PanelDesc(
            direction="WEST",
            position="Bottom-left",
            view_desc="interior view looking toward the west wall — full room visible, showing furnishings, architectural details, and ambient lighting from this vantage point",
            label_corner="top-right inner corner",
        ),
        PanelDesc(
            direction="SOUTH",
            position="Bottom-right",
            view_desc="interior view looking toward the south wall — full room visible, showing furnishings, architectural details, and ambient lighting from this vantage point",
            label_corner="top-left inner corner",
        ),
    ],
}


# ── Prompt Builder ────────────────────────────────────────────────


def build_grid_prompt(
    base_prompt: str,
    template_type: str = "exterior",
    media_style: str = "",
) -> str:
    """
    Assemble the full 2x2 grid prompt from a base location description
    and a preset template.

    Layout::

        ┌──────────┬──────────┐
        │  NORTH   │  EAST    │
        ├──────────┼──────────┤
        │  WEST    │  SOUTH   │
        └──────────┴──────────┘

    Raises:
        ValueError: If *template_type* is not a registered preset.
    """
    panels = PRESET_TEMPLATES.get(template_type)
    if panels is None:
        available = ", ".join(sorted(PRESET_TEMPLATES))
        raise ValueError(
            f"Unknown location grid template '{template_type}'. "
            f"Available: {available}"
        )

    panel_lines: list[str] = []
    for p in panels:
        panel_lines.append(
            f"{p.position} panel: {p.direction} view — {p.view_desc}. "
            f"Label '{p.direction}' at {p.label_corner}."
        )

    parts = [
        f"Generate a 2x2 grid image showing four directional views of {base_prompt}.",
        "",
        *panel_lines,
        "",
        (
            "Directional label layout is fixed and must be followed exactly: "
            "NORTH = top-left panel, label in the bottom-right inner corner; "
            "EAST = top-right panel, label in the bottom-left inner corner; "
            "WEST = bottom-left panel, label in the top-right inner corner; "
            "SOUTH = bottom-right panel, label in the top-left inner corner. "
            "Do not swap panel positions or move the labels."
        ),
        "",
    ]
    if media_style:
        parts.append(media_style)
    parts.append(
        "All four panels show the same location with consistent lighting, "
        "time of day, and architectural details."
    )

    return "\n".join(parts)


# ── Handler ───────────────────────────────────────────────────────


class LocationGridHandler(BaseHandler):
    """Generate 2x2 directional grid via nano-banana-pro (16:9, 2K)."""

    handler_name = "location_grid"

    async def generate(self, inp: LocationGridInput) -> LocationGridOutput:
        t0 = time.monotonic()
        route = MODEL_ROUTES[self.handler_name]
        spec = RESOLUTION_SPECS[self.handler_name]
        model = route.primary  # google/nano-banana-pro

        # ── Output path ───────────────────────────────────────
        out_dir = inp.output_dir / "locations"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"{inp.location_id}_grid.{inp.output_format}"

        # ── Assemble grid prompt from template ────────────────
        try:
            grid_prompt = build_grid_prompt(
                base_prompt=inp.prompt,
                template_type=inp.template_type,
                media_style=inp.media_style,
            )
        except ValueError as exc:
            return LocationGridOutput(
                success=False,
                location_id=inp.location_id,
                error=str(exc),
                elapsed_s=time.monotonic() - t0,
            )

        logger.debug(
            "Location grid prompt for %s [%s]:\n%s",
            inp.location_id,
            inp.template_type,
            grid_prompt,
        )

        # ── Build prediction input — direct to Pro, no chain ──
        pred_input: dict = {
            "prompt": grid_prompt,
            "aspect_ratio": spec.aspect_ratio,      # "16:9"
            "resolution": spec.resolution,           # "2K"
            "output_format": inp.output_format,
            "safety_filter_level": "block_only_high",
        }
        if inp.seed is not None:
            pred_input["seed"] = inp.seed

        adapted = adapt_input_for_model(model, pred_input)
        request_headers = self._build_request_headers(
            run_id=inp.run_id or "",
            phase=inp.phase,
            asset_id=inp.location_id,
        )

        # ── Call Replicate ────────────────────────────────────
        try:
            prediction = await self._replicate_predict(
                model,
                adapted,
                extra_headers=request_headers,
            )
            prediction = await self._resolve_prediction(
                prediction,
                extra_headers=request_headers,
            )
        except Exception as exc:
            return LocationGridOutput(
                success=False,
                location_id=inp.location_id,
                error=str(exc),
                elapsed_s=time.monotonic() - t0,
            )

        if prediction.get("status") != "succeeded":
            error_msg = prediction.get("error", "Generation failed")
            logs = prediction.get("logs", "") or ""
            detail = classify_replicate_error(error_msg, logs)
            xai_rescue = await self._try_xai_image_rescue(
                pred_input,
                reference_paths=[],
                output_path=output_path,
                error_detail=detail,
                sensitive_context=inp.sensitive_context,
                extra_headers=request_headers,
            )
            if xai_rescue:
                prediction, model = xai_rescue
            else:
                return LocationGridOutput(
                    success=False,
                    location_id=inp.location_id,
                    model_used=model,
                    error=error_msg,
                    error_detail=detail,
                    elapsed_s=time.monotonic() - t0,
                )

        local_output_path = prediction.get("local_output_path")
        if local_output_path:
            output_path = Path(local_output_path)
        else:
            output_url = self.extract_output_url(prediction)
            if not output_url:
                return LocationGridOutput(
                    success=False,
                    location_id=inp.location_id,
                    model_used=model,
                    error="No output URL in prediction response",
                    elapsed_s=time.monotonic() - t0,
                )

            # ── Download result ───────────────────────────────────
            await self.download_output(output_url, output_path)
        extract_directional_location_variants(output_path, inp.location_id)
        logger.info(
            "Location grid generated for %s [%s] at %s %s",
            inp.location_id,
            inp.template_type,
            spec.aspect_ratio,
            spec.resolution,
        )

        return LocationGridOutput(
            success=True,
            location_id=inp.location_id,
            image_path=output_path,
            model_used=model,
            resolution=spec.resolution or "",
            elapsed_s=time.monotonic() - t0,
        )

    def _make_error_output(
        self, inp: LocationGridInput, exc: Exception,
    ) -> LocationGridOutput:
        """Return a typed error output for batch failure isolation."""
        return LocationGridOutput(
            success=False,
            location_id=getattr(inp, "location_id", ""),
            error=f"{type(exc).__name__}: {exc}",
        )
