"""
Media generation handler registry.

Drop-in replacement for inline generation logic in server.py.
Each handler encapsulates model selection, API calls, fallback chains,
and output handling behind a single ``handler.generate(input)`` call.

Usage::

    from handlers import get_handler, CastImageInput

    async with get_handler("cast_image", replicate_token="r8_...") as h:
        result = await h.generate(CastImageInput(
            cast_id="cast-001",
            prompt="Full-body portrait...",
            media_style="live_clear",
            output_dir=Path("projects/my-project"),
        ))
        if result.success:
            print(result.image_path)

@AI_STATUS:COMPLETE
"""

from .base import BaseHandler
from .cast_image import CastImageHandler
from .frame import FrameHandler
from .location_grid import LocationGridHandler
from .models import (
    MODEL_ROUTES,
    RESOLUTION_SPECS,
    BatchResult,
    CastImageInput,
    CastImageOutput,
    FrameInput,
    FrameOutput,
    HandlerInput,
    HandlerOutput,
    LocationGridInput,
    LocationGridOutput,
    ModelRoute,
    ResolutionSpec,
    StoryboardInput,
    StoryboardOutput,
    VideoClipInput,
    VideoClipOutput,
    is_live_action,
)
from .storyboard import StoryboardHandler
from .video_clip import VideoClipHandler

# ── Handler Registry ───────────────────────────────────────────────

HANDLER_REGISTRY: dict[str, type[BaseHandler]] = {
    "cast_image": CastImageHandler,
    "location_grid": LocationGridHandler,
    "storyboard": StoryboardHandler,
    "frame": FrameHandler,
    "video_clip": VideoClipHandler,
}


def get_handler(
    name: str,
    *,
    replicate_token: str,
    xai_key: str = "",
    http_client: object | None = None,
) -> BaseHandler:
    """
    Factory: instantiate a handler by name.

    Args:
        name: One of ``"cast_image"``, ``"location_grid"``, ``"storyboard"``,
              ``"frame"``, ``"video_clip"``.
        replicate_token: Replicate API bearer token.
        xai_key: xAI API key (needed for video refinement flows).
        http_client: Optional shared ``httpx.AsyncClient``. If ``None``,
                     the handler creates and owns its own client.

    Returns:
        Configured handler instance. Supports ``async with`` for
        automatic client cleanup.

    Raises:
        ValueError: If *name* is not a registered handler.
    """
    cls = HANDLER_REGISTRY.get(name)
    if cls is None:
        available = ", ".join(sorted(HANDLER_REGISTRY))
        raise ValueError(f"Unknown handler '{name}'. Available: {available}")
    return cls(
        replicate_token=replicate_token,
        xai_key=xai_key,
        http_client=http_client,
    )


__all__ = [
    # Base
    "BaseHandler",
    "get_handler",
    "HANDLER_REGISTRY",
    # Handlers
    "CastImageHandler",
    "LocationGridHandler",
    "StoryboardHandler",
    "FrameHandler",
    "VideoClipHandler",
    # I/O Models
    "BatchResult",
    "HandlerInput",
    "HandlerOutput",
    "CastImageInput",
    "CastImageOutput",
    "LocationGridInput",
    "LocationGridOutput",
    "StoryboardInput",
    "StoryboardOutput",
    "FrameInput",
    "FrameOutput",
    "VideoClipInput",
    "VideoClipOutput",
    # Config
    "ModelRoute",
    "ResolutionSpec",
    "MODEL_ROUTES",
    "RESOLUTION_SPECS",
    "is_live_action",
]
