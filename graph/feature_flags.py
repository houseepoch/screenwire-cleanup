from __future__ import annotations

import os


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# Storyboard guidance is currently bypassed so the pipeline can proceed
# directly from structured prompt assembly to final frame generation.
ENABLE_STORYBOARD_GUIDANCE = _env_flag(
    "SCREENWIRE_ENABLE_STORYBOARD_GUIDANCE",
    False,
)
