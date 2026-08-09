"""Provider-independent Prometheus metrics."""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from app.infrastructure.config import settings

# A process-wide registry is created once and reused by every application factory.
REGISTRY = CollectorRegistry(auto_describe=True)

HTTP_REQUESTS = Counter(
    "http_requests",
    "Total HTTP requests completed.",
    ("method", "route", "status_class"),
    registry=REGISTRY,
)
HTTP_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "route"),
    registry=REGISTRY,
)
HTTP_IN_PROGRESS = Gauge(
    "http_requests_in_progress",
    "HTTP requests currently being processed.",
    ("method",),
    registry=REGISTRY,
)
RATE_LIMIT_DECISIONS = Counter(
    "rate_limit_decisions",
    "Rate-limit decisions by policy and outcome.",
    ("policy", "outcome"),
    registry=REGISTRY,
)

_STANDARD_METHODS = frozenset(
    {"CONNECT", "DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT", "TRACE"}
)


def normalize_http_method(method: str) -> str:
    """Map request methods to a bounded Prometheus/logging label set."""
    normalized = method.upper()
    return normalized if normalized in _STANDARD_METHODS else "OTHER"


def metrics_enabled() -> bool:
    """Return whether collection and exposition are enabled."""
    return settings.metrics_enabled


def observe_http_start(method: str) -> None:
    if metrics_enabled():
        HTTP_IN_PROGRESS.labels(method=normalize_http_method(method)).inc()


def observe_http_complete(
    *,
    method: str,
    route: str,
    status_code: int,
    duration_seconds: float,
) -> None:
    if not metrics_enabled():
        return
    normalized_method = normalize_http_method(method)
    HTTP_IN_PROGRESS.labels(method=normalized_method).dec()
    HTTP_REQUESTS.labels(
        method=normalized_method,
        route=route,
        status_class=f"{status_code // 100}xx",
    ).inc()
    HTTP_DURATION.labels(method=normalized_method, route=route).observe(duration_seconds)


def observe_rate_limit(policy: str, outcome: str) -> None:
    if metrics_enabled():
        RATE_LIMIT_DECISIONS.labels(policy=policy, outcome=outcome).inc()


def render_metrics() -> bytes:
    """Render the single process registry for the metrics endpoint."""
    rendered = generate_latest(REGISTRY)
    return bytes(rendered)


__all__ = [
    "CONTENT_TYPE_LATEST",
    "metrics_enabled",
    "normalize_http_method",
    "observe_http_complete",
    "observe_http_start",
    "observe_rate_limit",
    "render_metrics",
]
