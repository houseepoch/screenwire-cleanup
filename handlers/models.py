"""
I/O contracts for all media generation handlers.

Defines typed input/output Pydantic models, model routing table,
resolution specs, and live-action detection for the handler layer.

@AI_STATUS:COMPLETE
@AI_REASONING: Pydantic models for type-safe handler interfaces consistent
    with the rest of the codebase (graph/schema.py). Each handler pair
    (Input/Output) defines the exact contract. Common bases reduce
    duplication and enforce consistency across all 5 handlers.
    BatchResult added for generate_batch() return type — aggregates
    per-item results with total/succeeded/failed counters.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Model Routing ──────────────────────────────────────────────────


class ModelRoute(BaseModel):
    """Defines which Replicate models a handler uses and its fallback chain."""

    primary: str
    fallback: list[str] = Field(default_factory=list)
    upscale: Optional[str] = None


MODEL_ROUTES: dict[str, ModelRoute] = {
    "cast_image": ModelRoute(
        primary="prunaai/p-image",
        upscale="prunaai/p-image-upscale",
    ),
    "location_grid": ModelRoute(
        primary="google/nano-banana-pro",
        # No fallback — direct to Pro per spec
    ),
    "storyboard": ModelRoute(
        primary="google/nano-banana-2",
        fallback=["google/nano-banana-pro"],
    ),
    "frame": ModelRoute(
        primary="google/nano-banana-2",
        fallback=["google/nano-banana-pro"],
    ),
    "video_clip": ModelRoute(
        primary="xai/grok-imagine-video",
    ),
}


# ── Resolution & Aspect Enforcement ───────────────────────────────


class ResolutionSpec(BaseModel):
    """Resolution and aspect constraints enforced per handler."""

    aspect_ratio: Optional[str] = None
    resolution: Optional[str] = None  # "1K", "2K", "4K", "720p"
    target_megapixels: Optional[float] = None


RESOLUTION_SPECS: dict[str, ResolutionSpec] = {
    "cast_image": ResolutionSpec(aspect_ratio="1:1", target_megapixels=2.0),
    "location_grid": ResolutionSpec(aspect_ratio="16:9", resolution="2K"),
    "storyboard": ResolutionSpec(aspect_ratio="1:1", resolution="2K"),
    "frame": ResolutionSpec(aspect_ratio="16:9", resolution="4K"),
    "video_clip": ResolutionSpec(resolution="720p"),
}


# ── Live-Action Detection ─────────────────────────────────────────

LIVE_ACTION_STYLES: frozenset[str] = frozenset(
    {
        "live_retro_grain",
        "chiaroscuro_live",
        "live_soft_light",
        "live_clear",
    }
)


def is_live_action(media_style: str) -> bool:
    """Check if a media style requires live-action upscaling post-process."""
    return media_style in LIVE_ACTION_STYLES


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Base I/O
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class HandlerInput(BaseModel):
    """Common fields shared by all image-generation handler inputs."""

    seed: Optional[int] = None
    output_format: str = "png"
    run_id: Optional[str] = None
    phase: str = ""
    sensitive_context: bool = False


class HandlerOutput(BaseModel):
    """Common fields shared by all handler outputs."""

    success: bool
    model_used: str = ""
    elapsed_s: float = 0.0
    error: Optional[str] = None
    error_detail: Optional[dict[str, Any]] = None
    run_id: Optional[str] = None
    phase: str = ""


class BatchResult(BaseModel):
    """Aggregate result for a batch of handler operations.

    Wraps a list of handler-specific outputs with summary counters.
    Used by ``BaseHandler.generate_batch()`` and batch server endpoints.
    """

    results: list[Any] = Field(default_factory=list)
    total: int = 0
    succeeded: int = 0
    failed: int = 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. Cast Image
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class CastImageInput(HandlerInput):
    """
    Input for cast member reference image generation.

    Model: prunaai/p-image (outputs JPG natively).
    Post-process: live-action styles -> prunaai/p-image-upscale to 2 MP.
    Aspect: 1:1.
    Content: Full-body cast member, head-to-toes, front AND back views.
    """

    cast_id: str
    prompt: str  # Full assembled prompt including media-style prefix
    media_style: str  # e.g. "live_clear" — triggers upscale when live-action
    output_dir: Path  # Handler writes to {output_dir}/cast/composites/


class CastImageOutput(HandlerOutput):
    """Output from cast image generation."""

    cast_id: str = ""
    image_path: Optional[Path] = None  # cast/composites/{cast_id}_ref.png
    upscaled: bool = False
    upscale_model_used: Optional[str] = None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. Location Grid
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class LocationGridInput(HandlerInput):
    """
    Input for location reference image generation.

    Model: google/nano-banana-pro (direct, NO fallback chain).
    Aspect: 16:9 | Resolution: 2K (2048x1152 equivalent).
    Grid: 2x2 directional views (NORTH, EAST, WEST, SOUTH).
    """

    location_id: str
    prompt: str  # Base location description (injected into grid prompt)
    template_type: str = "exterior"  # Preset template key (see location_grid.py)
    media_style: str = ""  # Optional style line injected into prompt
    output_dir: Path  # Handler writes to {output_dir}/locations/


class LocationGridOutput(HandlerOutput):
    """Output from location grid generation."""

    location_id: str = ""
    image_path: Optional[Path] = None
    resolution: str = ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  3. Storyboard
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class StoryboardInput(HandlerInput):
    """
    Input for storyboard composite generation with cell extraction.

    Model chain: google/nano-banana-2 -> fallback google/nano-banana-pro.
    Aspect: 1:1 | Resolution: 2K.
    Reference images: ALL present cast composites, prop images, location
    images for frames in the storyboard — sent as image_input[].
    """

    grid_id: str
    prompt: str
    reference_images: list[Path] = Field(default_factory=list)
    layout: str = "2x2"  # "2x2", "3x3", etc.
    frame_ids: list[str] = Field(default_factory=list)  # For cell naming
    output_dir: Path


class StoryboardOutput(HandlerOutput):
    """Output from storyboard generation."""

    grid_id: str = ""
    composite_path: Optional[Path] = None
    cell_paths: list[Path] = Field(default_factory=list)
    resolution: str = ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  4. Frame
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class FrameInput(HandlerInput):
    """
    Input for composed frame image generation at 4K.

    Model chain: google/nano-banana-2 -> fallback google/nano-banana-pro.
    Aspect: 16:9 | Resolution: 4K.
    Reference images: ALL cast/prop/location reference images relevant
    to the frame, plus the storyboard image as composition reference.
    """

    frame_id: str
    prompt: str
    reference_images: list[Path] = Field(default_factory=list)
    storyboard_image: Optional[Path] = None  # Composition reference
    output_dir: Path


class FrameOutput(HandlerOutput):
    """Output from frame generation."""

    frame_id: str = ""
    image_path: Optional[Path] = None  # frames/composed/{frame_id}_gen.png
    resolution: str = ""
    downshifted: bool = False  # True if 4K->2K capacity rescue was used
    refs_used: Optional[list[str]] = None  # Relative paths of all refs sent (storyboard first)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  5. Video Clip
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class VideoClipInput(BaseModel):
    """
    Input for video clip generation from a composed frame.

    Model: xai/grok-imagine-video (via Replicate).
    Resolution: 720p | Duration: suggested_duration clamped to 2-15s.
    Prompt: dialogue text prefixed before the motion/action prompt.
    Audio: native generation — Grok synthesises dialogue + ambient.
    """

    frame_id: str
    dialogue_text: str  # Prefixed before motion prompt
    motion_prompt: str  # Motion/action description
    frame_image_path: Path  # Composed frame image to animate
    suggested_duration: int = 5  # Seconds, clamped 2-15
    output_dir: Path
    run_id: Optional[str] = None
    phase: str = ""


class VideoClipOutput(HandlerOutput):
    """Output from video clip generation."""

    frame_id: str = ""
    video_path: Optional[Path] = None  # video/clips/{frame_id}.mp4
    duration: int = 0  # Actual duration used (after clamping)
    resolution: str = "720p"
