"""Centralized safe logging and request correlation support."""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.infrastructure.config import settings

REQUEST_ID_MAX_LENGTH = 128
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)

_SAFE_FIELDS = (
    "request_id",
    "method",
    "route",
    "status",
    "status_class",
    "duration_ms",
    "job_name",
    "outcome",
    "error_code",
    "exception_type",
    "policy",
)
_SAFE_EVENTS = frozenset(
    {
        "infrastructure_error",
        "job_cancelled",
        "job_completed",
        "job_failed",
        "job_started",
        "notification_dispatched",
        "readiness_check_failed",
        "request_completed",
        "unhandled_request_error",
    }
)
_APP_LOGGER_NAME = "app"
_UVICORN_ACCESS_LOGGER_NAME = "uvicorn.access"
_OWNED_HANDLER_ATTRIBUTE = "_escrow_application_handler"


def normalize_request_id(value: str | None) -> str:
    """Accept only bounded header-safe request IDs and generate the rest."""
    if (
        value is not None
        and len(value) <= REQUEST_ID_MAX_LENGTH
        and _REQUEST_ID_PATTERN.fullmatch(value)
    ):
        return value
    return str(uuid4())


def current_request_id() -> str | None:
    """Return the request ID associated with the current async context."""
    return request_id_context.get()


class _ContextFilter(logging.Filter):
    """Attach correlation without allowing arbitrary log fields through."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = current_request_id()
        return True


def _safe_fields(record: logging.LogRecord) -> dict[str, Any]:
    return {
        field: getattr(record, field)
        for field in _SAFE_FIELDS
        if getattr(record, field, None) is not None
    }


def _safe_event(record: logging.LogRecord) -> str:
    event = getattr(record, "event", "log")
    return event if isinstance(event, str) and event in _SAFE_EVENTS else "log"


class _JsonFormatter(logging.Formatter):
    """Render only the bounded, explicitly approved application fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": _safe_event(record),
        }
        payload.update(_safe_fields(record))
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


class _TextFormatter(logging.Formatter):
    """Render the same safe fields in a readable local-development format."""

    def format(self, record: logging.LogRecord) -> str:
        fields = " ".join(f"{key}={value}" for key, value in _safe_fields(record).items())
        event = _safe_event(record)
        return f"{record.levelname} {event}" + (f" {fields}" if fields else "")


def configure_logging() -> None:
    """Configure one safe application handler at process startup."""
    # The request middleware is the canonical access log. Uvicorn's default
    # access record includes client IPs and raw targets, including queries.
    logging.getLogger(_UVICORN_ACCESS_LOGGER_NAME).disabled = True

    application_logger = logging.getLogger(_APP_LOGGER_NAME)
    level = settings.log_level.upper()
    application_logger.setLevel(level)
    application_logger.propagate = False
    application_logger.disabled = False

    # Only the application hierarchy is ours to normalize. Root and vendor
    # loggers must retain their own handlers and formatting.
    for name, candidate in logging.root.manager.loggerDict.items():
        if name.startswith(f"{_APP_LOGGER_NAME}.") and isinstance(candidate, logging.Logger):
            candidate.disabled = False
            candidate.setLevel(logging.NOTSET)
            candidate.propagate = True
            candidate.handlers.clear()

    formatter: logging.Formatter = (
        _TextFormatter() if settings.log_format == "text" else _JsonFormatter()
    )

    owned_handlers = [
        handler
        for handler in application_logger.handlers
        if getattr(handler, _OWNED_HANDLER_ATTRIBUTE, False)
    ]
    handler = owned_handlers[0] if owned_handlers else None
    for duplicate in owned_handlers[1:]:
        application_logger.removeHandler(duplicate)
        duplicate.close()

    if handler is None:
        handler = logging.StreamHandler(sys.stderr)
        setattr(handler, _OWNED_HANDLER_ATTRIBUTE, True)
        handler.addFilter(_ContextFilter())
        application_logger.addHandler(handler)

    # The app logger owns its output boundary. This removes stale app-local
    # handlers without touching root or third-party logger configuration.
    for existing in list(application_logger.handlers):
        if existing is not handler:
            application_logger.removeHandler(existing)
            existing.close()

    if not any(isinstance(item, _ContextFilter) for item in handler.filters):
        handler.addFilter(_ContextFilter())
    handler.setLevel(level)
    handler.setFormatter(formatter)
