from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


_CONFIGURED: set[str] = set()
_ROOT_CONFIGURED = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


class StructuredFormatter(logging.Formatter):
    def __init__(self, *, service_name: str, as_json: bool) -> None:
        super().__init__()
        self.service_name = service_name
        self.as_json = as_json

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": _utc_now(),
            "level": record.levelname,
            "service": self.service_name,
            "logger": record.name,
            "message": record.getMessage(),
        }
        event = getattr(record, "event", None)
        if event:
            payload["event"] = event
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict) and fields:
            payload["fields"] = _json_safe(fields)
        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        project_id = getattr(record, "project_id", None)
        if project_id:
            payload["project_id"] = project_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        if self.as_json:
            return json.dumps(payload, ensure_ascii=True)

        field_suffix = ""
        if "fields" in payload:
            field_suffix = f" fields={json.dumps(payload['fields'], ensure_ascii=True, sort_keys=True)}"
        request_suffix = f" request_id={payload['request_id']}" if "request_id" in payload else ""
        project_suffix = f" project_id={payload['project_id']}" if "project_id" in payload else ""
        event_suffix = f" event={payload['event']}" if "event" in payload else ""
        line = (
            f"{payload['ts']} level={payload['level']} service={self.service_name} "
            f"logger={payload['logger']}{event_suffix}{request_suffix}{project_suffix} "
            f"message={payload['message']}{field_suffix}"
        )
        if "exception" in payload:
            return f"{line}\n{payload['exception']}"
        return line


def configure_logging(service_name: str) -> logging.Logger:
    global _ROOT_CONFIGURED
    root = logging.getLogger()
    if service_name in _CONFIGURED:
        return logging.getLogger(service_name)

    if _ROOT_CONFIGURED:
        _CONFIGURED.add(service_name)
        return logging.getLogger(service_name)

    level_name = str(os.getenv("SCREENWIRE_LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    root.setLevel(level)

    as_json = str(os.getenv("SCREENWIRE_LOG_JSON") or "1").strip().lower() not in {"0", "false", "no", "off"}
    formatter = StructuredFormatter(service_name=service_name, as_json=as_json)

    root.handlers.clear()

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    log_dir_value = str(os.getenv("SCREENWIRE_LOG_DIR") or "").strip()
    if log_dir_value:
        log_dir = Path(log_dir_value).expanduser().resolve()
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / f"{service_name}.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    logging.captureWarnings(True)
    _ROOT_CONFIGURED = True
    _CONFIGURED.add(service_name)
    logger = logging.getLogger(service_name)
    logger.propagate = True
    return logger


def log_event(logger: logging.Logger, level: int, event: str, /, **fields: Any) -> None:
    logger.log(level, event, extra={"event": event, "fields": fields})
