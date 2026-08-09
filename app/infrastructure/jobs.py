"""Shared execution lifecycle for scheduled commands."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from time import perf_counter
from typing import Any

logger = logging.getLogger(__name__)


async def run_observed_job[ResultT](
    job_name: str,
    operation: Callable[[], Awaitable[ResultT]],
) -> ResultT:
    """Run one job with shared safe logs and failure propagation."""
    started_at = perf_counter()
    outcome = "failed"
    exception_type: str | None = None
    logger.info("job_started", extra={"event": "job_started", "job_name": job_name})
    try:
        result = await operation()
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    except Exception as exc:  # noqa: BLE001 - log only a safe exception type, then propagate.
        exception_type = type(exc).__name__
        raise
    else:
        outcome = "success"
        return result
    finally:
        event = {
            "success": "job_completed",
            "cancelled": "job_cancelled",
            "failed": "job_failed",
        }[outcome]
        extra: dict[str, Any] = {
            "event": event,
            "job_name": job_name,
            "outcome": outcome,
            "duration_ms": round((perf_counter() - started_at) * 1000, 3),
        }
        if exception_type is not None:
            extra["exception_type"] = exception_type
        logger.log(
            logging.WARNING
            if outcome == "cancelled"
            else logging.ERROR
            if outcome == "failed"
            else logging.INFO,
            event,
            extra=extra,
        )


def run_cli(command: Coroutine[Any, Any, object]) -> None:
    """Run a scheduled command without exposing raw failure details on stderr."""
    from app.infrastructure.application_logging import configure_logging

    configure_logging()
    try:
        asyncio.run(command)
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception:
        raise SystemExit(1) from None
