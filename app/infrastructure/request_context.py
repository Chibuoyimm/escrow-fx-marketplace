"""Request correlation, completion logging, and HTTP metrics."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from time import perf_counter

from fastapi import FastAPI, Request
from starlette.responses import Response

from app.infrastructure.application_logging import (
    current_request_id,
    normalize_request_id,
    request_id_context,
)
from app.infrastructure.config import settings
from app.infrastructure.metrics import (
    normalize_http_method,
    observe_http_complete,
    observe_http_start,
)

logger = logging.getLogger(__name__)


def register_request_context(application: FastAPI) -> None:
    """Attach safe correlation and one completion observation to each request."""

    @application.middleware("http")
    async def add_request_id(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = normalize_request_id(request.headers.get("X-Request-ID"))
        request.state.request_id = request_id
        token = request_id_context.set(request_id)
        started_at = perf_counter()
        method = normalize_http_method(request.method)
        instrument_metrics = request.url.path != settings.metrics_path
        if instrument_metrics:
            observe_http_start(method)
        response: Response | None = None
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except asyncio.CancelledError:
            status_code = 499
            raise
        finally:
            route = _normalized_route(request)
            duration_seconds = perf_counter() - started_at
            if instrument_metrics:
                observe_http_complete(
                    method=method,
                    route=route,
                    status_code=status_code,
                    duration_seconds=duration_seconds,
                )
            logger.info(
                "request_completed",
                extra={
                    "event": "request_completed",
                    "method": method,
                    "route": route,
                    "status": status_code,
                    "status_class": f"{status_code // 100}xx",
                    "duration_ms": round(duration_seconds * 1000, 3),
                },
            )
            if response is not None:
                response.headers["X-Request-ID"] = request_id
                response.headers.update(getattr(request.state, "rate_limit_headers", {}))
            request_id_context.reset(token)


def _normalized_route(request: Request) -> str:
    """Return the registered route template, never the raw request path."""
    route = request.scope.get("route")
    route_template = getattr(route, "path", None)
    return route_template if isinstance(route_template, str) else "unmatched"


__all__ = ["current_request_id", "normalize_request_id", "register_request_context"]
