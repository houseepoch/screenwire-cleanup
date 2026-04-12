from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RUN_ID_ENV = "SCREENWIRE_RUN_ID"
PHASE_ENV = "SCREENWIRE_PHASE"

_RUN_ID_CONTEXT: ContextVar[str] = ContextVar("screenwire_run_id", default="")
_PHASE_CONTEXT: ContextVar[str] = ContextVar("screenwire_phase", default="")


def generate_run_id() -> str:
    """Return a UUID4 hex run identifier."""
    return uuid.uuid4().hex


def iso_now() -> str:
    """Return a timezone-aware ISO-8601 timestamp."""
    return datetime.now(timezone.utc).isoformat()


def current_run_id(default: str = "") -> str:
    return _RUN_ID_CONTEXT.get() or os.getenv(RUN_ID_ENV, default)


def current_phase(default: str = "") -> str:
    return _PHASE_CONTEXT.get() or os.getenv(PHASE_ENV, default)


@contextmanager
def activate_run_context(*, run_id: str = "", phase: str = ""):
    """Temporarily bind run context to the current thread/task."""
    run_token = _RUN_ID_CONTEXT.set(run_id or current_run_id())
    phase_token = _PHASE_CONTEXT.set(phase or current_phase())
    try:
        yield
    finally:
        _RUN_ID_CONTEXT.reset(run_token)
        _PHASE_CONTEXT.reset(phase_token)


def with_run_context(
    env: dict[str, str] | None = None,
    *,
    run_id: str = "",
    phase: str = "",
) -> dict[str, str]:
    """Return an environment dict with the active run context injected."""
    merged = dict(env or {})
    resolved_run_id = run_id or current_run_id()
    resolved_phase = phase or current_phase()
    if resolved_run_id:
        merged[RUN_ID_ENV] = resolved_run_id
    if resolved_phase:
        merged[PHASE_ENV] = resolved_phase
    return merged


def build_request_headers(
    *,
    run_id: str = "",
    phase: str = "",
    frame_id: str = "",
    asset_id: str = "",
) -> dict[str, str]:
    """Build request headers for upstream API correlation."""
    headers: dict[str, str] = {}
    resolved_run_id = run_id or current_run_id()
    resolved_phase = phase or current_phase()

    if resolved_run_id:
        headers["X-ScreenWire-Run-Id"] = resolved_run_id
    if resolved_phase:
        headers["X-ScreenWire-Phase"] = resolved_phase
    if frame_id:
        headers["X-ScreenWire-Frame-Id"] = frame_id
    if asset_id:
        headers["X-ScreenWire-Asset-Id"] = asset_id
    return headers


def emit_event(
    project_dir: str | Path | None,
    *,
    event: str,
    level: str = "INFO",
    run_id: str = "",
    phase: str = "",
    frame_id: str = "",
    asset_id: str = "",
    handler: str = "",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append a structured event to logs/telemetry/events.jsonl."""
    entry: dict[str, Any] = {
        "timestamp": iso_now(),
        "level": level,
        "event": event,
        "run_id": run_id or current_run_id() or None,
        "phase": phase or current_phase() or None,
        "frame_id": frame_id or None,
        "asset_id": asset_id or None,
        "handler": handler or None,
    }
    if details:
        entry.update(details)

    if project_dir is not None:
        path = Path(project_dir) / "logs" / "telemetry" / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return entry
