"""Shared xAI transport layer for ScreenWire."""

from .xai_client import (
    DEFAULT_MULTI_AGENT_MODEL,
    DEFAULT_REASONING_MODEL,
    DEFAULT_STAGE1_REASONING_MODEL,
    SyncXAIClient,
    XAIClient,
    build_prompt_cache_key,
    is_multi_agent_model,
    resolve_model_alias,
)

__all__ = [
    "DEFAULT_MULTI_AGENT_MODEL",
    "DEFAULT_REASONING_MODEL",
    "DEFAULT_STAGE1_REASONING_MODEL",
    "SyncXAIClient",
    "XAIClient",
    "build_prompt_cache_key",
    "is_multi_agent_model",
    "resolve_model_alias",
]
