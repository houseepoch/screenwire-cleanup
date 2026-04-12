"""Unified xAI client for Grok reasoning and multi-agent calls."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Optional

from dotenv import load_dotenv
from openai import AsyncOpenAI, OpenAI

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DEFAULT_STAGE1_REASONING_MODEL = "grok-4.20-reasoning"
DEFAULT_REASONING_MODEL = "grok-4-1-fast-reasoning"
DEFAULT_MULTI_AGENT_MODEL = "grok-4.20-multi-agent"
XAI_BASE_URL = "https://api.x.ai/v1"

_STAGE1_HINTS = (
    "creative_coordinator",
    "prose_worker",
    "director",
)
_MULTI_AGENT_HINTS = (
    "creative_coordinator",
)


def build_prompt_cache_key(namespace: str, *parts: str) -> str:
    """Build a stable prompt-cache key for repeated prompt prefixes."""
    digest = hashlib.sha256()
    digest.update(namespace.encode("utf-8"))
    for part in parts:
        digest.update(b"\0")
        digest.update((part or "").encode("utf-8"))
    return f"screenwire:{namespace}:{digest.hexdigest()[:24]}"


def _looks_stage1_task(task_hint: str) -> bool:
    lowered = (task_hint or "").lower()
    return any(token in lowered for token in _STAGE1_HINTS)


def _looks_multi_agent_task(task_hint: str) -> bool:
    lowered = (task_hint or "").lower()
    return any(token in lowered for token in _MULTI_AGENT_HINTS)


def is_multi_agent_model(model: str) -> bool:
    return "multi-agent" in (model or "").lower()


def resolve_model_alias(model: str, *, task_hint: str = "") -> str:
    """Map legacy Claude names and generic defaults to Grok aliases."""
    raw = (model or "").strip()
    lowered = raw.lower()

    if not raw:
        raw = DEFAULT_REASONING_MODEL
        lowered = raw.lower()

    if lowered.startswith("grok-"):
        if lowered == DEFAULT_REASONING_MODEL and _looks_multi_agent_task(task_hint):
            return DEFAULT_MULTI_AGENT_MODEL
        if lowered == DEFAULT_REASONING_MODEL and _looks_stage1_task(task_hint):
            return DEFAULT_STAGE1_REASONING_MODEL
        return raw

    if "haiku" in lowered:
        return DEFAULT_REASONING_MODEL

    if "opus" in lowered or "sonnet" in lowered or "claude" in lowered:
        if _looks_multi_agent_task(task_hint):
            return DEFAULT_MULTI_AGENT_MODEL
        if _looks_stage1_task(task_hint):
            return DEFAULT_STAGE1_REASONING_MODEL
        return DEFAULT_REASONING_MODEL

    if _looks_multi_agent_task(task_hint):
        return DEFAULT_MULTI_AGENT_MODEL
    if _looks_stage1_task(task_hint):
        return DEFAULT_STAGE1_REASONING_MODEL
    return DEFAULT_REASONING_MODEL


def _build_messages(
    *,
    prompt: Optional[str] = None,
    system_prompt: str = "",
    messages: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    if messages is not None:
        return messages

    built: list[dict[str, Any]] = []
    if system_prompt:
        built.append({"role": "system", "content": system_prompt})
    if prompt is not None:
        built.append({"role": "user", "content": prompt})
    return built


def _extract_chat_text(response: Any) -> str:
    content = response.choices[0].message.content
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            else:
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts).strip()
    return str(content).strip()


def _extract_response_text(response: Any) -> str:
    text = getattr(response, "output_text", None)
    if isinstance(text, str):
        return text.strip()
    return str(text or "").strip()


class _BaseXAIClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("XAI_API_KEY", "")
        if not self.api_key:
            raise ValueError("XAI_API_KEY environment variable is required")


class XAIClient(_BaseXAIClient):
    """Async xAI client for runtime modules."""

    def __init__(self, api_key: str | None = None):
        super().__init__(api_key=api_key)
        self.client = AsyncOpenAI(api_key=self.api_key, base_url=XAI_BASE_URL)

    async def generate_text(
        self,
        *,
        prompt: str | None = None,
        system_prompt: str = "",
        messages: Optional[list[dict[str, Any]]] = None,
        model: str = DEFAULT_REASONING_MODEL,
        task_hint: str = "",
        temperature: float = 0.7,
        max_tokens: int | None = 4096,
        cache_key: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_model = resolve_model_alias(model, task_hint=task_hint)
        if is_multi_agent_model(resolved_model):
            return await self.generate_multi_agent(
                prompt=prompt,
                system_prompt=system_prompt,
                input_messages=messages,
                model=resolved_model,
                cache_key=cache_key,
                **kwargs,
            )

        request: dict[str, Any] = {
            "model": resolved_model,
            "messages": _build_messages(prompt=prompt, system_prompt=system_prompt, messages=messages),
            "temperature": temperature,
            **kwargs,
        }
        if max_tokens is not None:
            request["max_tokens"] = max_tokens
        if cache_key:
            request["extra_headers"] = {"x-grok-conv-id": cache_key}
        response = await self.client.chat.completions.create(**request)
        return _extract_chat_text(response)

    async def generate_json(
        self,
        *,
        schema: dict[str, Any],
        prompt: str | None = None,
        system_prompt: str = "",
        messages: Optional[list[dict[str, Any]]] = None,
        model: str = DEFAULT_REASONING_MODEL,
        task_hint: str = "",
        temperature: float = 0.0,
        max_tokens: int | None = 4096,
        cache_key: str = "",
        schema_name: str = "screenwire_output",
        **kwargs: Any,
    ) -> dict[str, Any]:
        resolved_model = resolve_model_alias(model, task_hint=task_hint)
        if is_multi_agent_model(resolved_model):
            resolved_model = DEFAULT_REASONING_MODEL

        request: dict[str, Any] = {
            "model": resolved_model,
            "messages": _build_messages(prompt=prompt, system_prompt=system_prompt, messages=messages),
            "temperature": temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": schema,
                    "strict": True,
                },
            },
            **kwargs,
        }
        if max_tokens is not None:
            request["max_tokens"] = max_tokens
        if cache_key:
            request["extra_headers"] = {"x-grok-conv-id": cache_key}
        response = await self.client.chat.completions.create(**request)
        return json.loads(_extract_chat_text(response))

    async def generate_multi_agent(
        self,
        *,
        prompt: str | None = None,
        system_prompt: str = "",
        input_messages: Optional[list[dict[str, Any]]] = None,
        model: str = DEFAULT_MULTI_AGENT_MODEL,
        cache_key: str = "",
        previous_response_id: str | None = None,
        **kwargs: Any,
    ) -> str:
        resolved_model = resolve_model_alias(model, task_hint="")
        if not is_multi_agent_model(resolved_model):
            resolved_model = DEFAULT_MULTI_AGENT_MODEL

        if input_messages is None:
            input_messages = _build_messages(prompt=prompt, system_prompt=system_prompt, messages=None)

        request: dict[str, Any] = {
            "model": resolved_model,
            "input": input_messages,
            **kwargs,
        }
        if cache_key:
            request["prompt_cache_key"] = cache_key
        if previous_response_id:
            request["previous_response_id"] = previous_response_id
        response = await self.client.responses.create(**request)
        return _extract_response_text(response)


class SyncXAIClient(_BaseXAIClient):
    """Sync xAI client for subprocess-style agent runners."""

    def __init__(self, api_key: str | None = None):
        super().__init__(api_key=api_key)
        self.client = OpenAI(api_key=self.api_key, base_url=XAI_BASE_URL)

    def generate_text(
        self,
        *,
        prompt: str | None = None,
        system_prompt: str = "",
        messages: Optional[list[dict[str, Any]]] = None,
        model: str = DEFAULT_REASONING_MODEL,
        task_hint: str = "",
        temperature: float = 0.7,
        max_tokens: int | None = 4096,
        cache_key: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_model = resolve_model_alias(model, task_hint=task_hint)
        if is_multi_agent_model(resolved_model):
            text, _ = self.generate_multi_agent(
                prompt=prompt,
                system_prompt=system_prompt,
                input_messages=messages,
                model=resolved_model,
                cache_key=cache_key,
                **kwargs,
            )
            return text

        request: dict[str, Any] = {
            "model": resolved_model,
            "messages": _build_messages(prompt=prompt, system_prompt=system_prompt, messages=messages),
            "temperature": temperature,
            **kwargs,
        }
        if max_tokens is not None:
            request["max_tokens"] = max_tokens
        if cache_key:
            request["extra_headers"] = {"x-grok-conv-id": cache_key}
        response = self.client.chat.completions.create(**request)
        return _extract_chat_text(response)

    def generate_text_with_tools(
        self,
        *,
        prompt: str | None = None,
        system_prompt: str = "",
        messages: Optional[list[dict[str, Any]]] = None,
        tools: list[dict[str, Any]],
        tool_executor: Callable[[str, str], str],
        model: str = DEFAULT_REASONING_MODEL,
        task_hint: str = "",
        cache_key: str = "",
        max_tool_turns: int = 24,
        **kwargs: Any,
    ) -> str:
        """Run a client-side tool loop using xAI Responses API."""
        resolved_model = resolve_model_alias(model, task_hint=task_hint)
        current_input: Any = _build_messages(
            prompt=prompt,
            system_prompt=system_prompt,
            messages=messages,
        )
        previous_response_id: str | None = None

        for _ in range(max_tool_turns):
            request: dict[str, Any] = {
                "model": resolved_model,
                "input": current_input,
                "tools": tools,
                **kwargs,
            }
            if cache_key:
                request["prompt_cache_key"] = cache_key
            if previous_response_id:
                request["previous_response_id"] = previous_response_id

            response = self.client.responses.create(**request)

            tool_outputs: list[dict[str, Any]] = []
            for item in getattr(response, "output", []) or []:
                if getattr(item, "type", None) != "function_call":
                    continue
                tool_name = getattr(item, "name", "")
                arguments = getattr(item, "arguments", "") or ""
                call_id = getattr(item, "call_id", "")
                try:
                    result = tool_executor(tool_name, arguments)
                except Exception as exc:
                    result = f"TOOL_ERROR: {type(exc).__name__}: {exc}"
                tool_outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": result,
                    }
                )

            if not tool_outputs:
                return _extract_response_text(response)

            previous_response_id = getattr(response, "id", None)
            current_input = tool_outputs

        raise RuntimeError(
            f"Exceeded max_tool_turns={max_tool_turns} without a final model response"
        )

    def generate_multi_agent(
        self,
        *,
        prompt: str | None = None,
        system_prompt: str = "",
        input_messages: Optional[list[dict[str, Any]]] = None,
        model: str = DEFAULT_MULTI_AGENT_MODEL,
        cache_key: str = "",
        previous_response_id: str | None = None,
        **kwargs: Any,
    ) -> tuple[str, str | None]:
        resolved_model = resolve_model_alias(model, task_hint="")
        if not is_multi_agent_model(resolved_model):
            resolved_model = DEFAULT_MULTI_AGENT_MODEL

        if input_messages is None:
            input_messages = _build_messages(prompt=prompt, system_prompt=system_prompt, messages=None)

        request: dict[str, Any] = {
            "model": resolved_model,
            "input": input_messages,
            **kwargs,
        }
        if cache_key:
            request["prompt_cache_key"] = cache_key
        if previous_response_id:
            request["previous_response_id"] = previous_response_id
        response = self.client.responses.create(**request)
        return _extract_response_text(response), getattr(response, "id", None)

    def list_models(self) -> list[str]:
        models = self.client.models.list()
        data = getattr(models, "data", []) or []
        return sorted(
            {
                getattr(model, "id", "")
                for model in data
                if getattr(model, "id", "")
            }
        )
