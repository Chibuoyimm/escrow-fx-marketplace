from __future__ import annotations

import asyncio
import io
import json
import logging
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.exception_handlers import register_exception_handlers
from app.api.routes import health_router
from app.infrastructure import health as health_module
from app.infrastructure.application_logging import (
    REQUEST_ID_MAX_LENGTH,
    configure_logging,
    current_request_id,
    normalize_request_id,
    request_id_context,
)
from app.infrastructure.config import Settings, settings
from app.infrastructure.metrics import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    normalize_http_method,
    observe_http_complete,
    observe_http_start,
)
from app.infrastructure.request_context import register_request_context
from app.main import app, create_application
from tests.conftest import record_field


def build_observability_app() -> FastAPI:
    application = FastAPI()
    register_request_context(application)
    register_exception_handlers(application)

    @application.get("/things/{thing_id}")
    async def get_thing(thing_id: int) -> dict[str, Any]:
        return {"thing_id": thing_id, "request_id": current_request_id()}

    application.include_router(health_router)
    return application


def test_request_id_is_preserved_only_when_header_is_safe() -> None:
    client = TestClient(build_observability_app())

    preserved = client.get("/things/42", headers={"X-Request-ID": "client.req-42"})
    generated = client.get("/things/42", headers={"X-Request-ID": "not safe"})

    assert preserved.headers["x-request-id"] == "client.req-42"
    assert preserved.json()["request_id"] == "client.req-42"
    assert generated.headers["x-request-id"] != "not safe"
    assert generated.headers["x-request-id"] == generated.json()["request_id"]
    assert len(generated.headers["x-request-id"]) == 36


def test_normalize_request_id_rejects_unbounded_and_control_input() -> None:
    maximum = "a" * REQUEST_ID_MAX_LENGTH

    assert normalize_request_id(maximum) == maximum
    for invalid in ("a" * (REQUEST_ID_MAX_LENGTH + 1), "safe\nvalue", "safe\rvalue"):
        generated = normalize_request_id(invalid)
        assert generated != invalid
        assert len(generated) == 36


def test_request_completion_log_uses_route_template_and_safe_fields(
    app_log_capture: pytest.LogCaptureFixture,
) -> None:
    client = TestClient(build_observability_app())

    with app_log_capture.at_level("INFO"):
        client.get("/things/8675309", headers={"X-Request-ID": "trace-1"})

    completed = [
        record
        for record in app_log_capture.records
        if getattr(record, "event", None) == "request_completed"
    ]
    assert len(completed) == 1
    assert record_field(completed[0], "route") == "/things/{thing_id}"
    assert record_field(completed[0], "status") == 200
    assert record_field(completed[0], "request_id") == "trace-1"
    assert "8675309" not in json.dumps(completed[0].__dict__, default=str)


def test_exception_logs_are_correlated_without_exception_details(
    app_log_capture: pytest.LogCaptureFixture,
) -> None:
    application = FastAPI()
    register_request_context(application)
    register_exception_handlers(application)

    @application.get("/broken")
    async def broken() -> None:
        raise RuntimeError("database password=secret")

    client = TestClient(application, raise_server_exceptions=False)
    with app_log_capture.at_level("ERROR"):
        response = client.get("/broken", headers={"X-Request-ID": "trace-error"})

    assert response.status_code == 500
    errors = [
        record
        for record in app_log_capture.records
        if getattr(record, "event", None) == "unhandled_request_error"
    ]
    assert len(errors) == 1
    assert record_field(errors[0], "request_id") == "trace-error"
    assert record_field(errors[0], "exception_type") == "RuntimeError"
    assert "database password" not in json.dumps(errors[0].__dict__, default=str)


@pytest.mark.parametrize("log_format", ["json", "text"])
def test_application_log_output_contains_only_safe_fields(
    monkeypatch: pytest.MonkeyPatch,
    log_format: str,
) -> None:
    monkeypatch.setattr(settings, "log_format", log_format)
    configure_logging()
    app_logger = logging.getLogger("app")
    handler = next(
        item for item in app_logger.handlers if getattr(item, "_escrow_application_handler", False)
    )
    assert isinstance(handler, logging.StreamHandler)
    output = io.StringIO()
    previous_stream = handler.setStream(output)
    token = request_id_context.set("trace-output")
    try:
        app_logger.info(
            "raw secret should be omitted",
            extra={
                "event": "request_completed",
                "method": "GET",
                "route": "/things/{thing_id}",
                "status": 200,
                "status_class": "2xx",
                "duration_ms": 1.5,
                "email": "private@example.com",
                "secret": "do-not-render",
            },
        )
    finally:
        request_id_context.reset(token)
        handler.setStream(previous_stream)

    rendered = output.getvalue()
    assert "raw secret" not in rendered
    assert "private@example.com" not in rendered
    assert "do-not-render" not in rendered
    if log_format == "json":
        payload = json.loads(rendered)
        assert set(payload) == {
            "duration_ms",
            "event",
            "level",
            "logger",
            "method",
            "request_id",
            "route",
            "status",
            "status_class",
            "timestamp",
        }
        assert payload["request_id"] == "trace-output"
    else:
        assert "INFO request_completed" in rendered
        assert "request_id=trace-output" in rendered


def test_application_logging_isolated_from_root_and_third_party_handlers() -> None:
    root = logging.getLogger()
    root_output = io.StringIO()
    unrelated_handler = logging.StreamHandler(root_output)
    root.addHandler(unrelated_handler)
    third_party = logging.getLogger("httpx")
    access_logger = logging.getLogger("uvicorn.access")
    previous_level = third_party.level
    previous_propagate = third_party.propagate
    previous_disabled = third_party.disabled
    previous_access_disabled = access_logger.disabled
    access_output = io.StringIO()
    access_handler = logging.StreamHandler(access_output)
    access_logger.addHandler(access_handler)
    third_party_output = io.StringIO()
    third_party_handler = logging.StreamHandler(third_party_output)
    third_party.addHandler(third_party_handler)
    third_party.setLevel(logging.WARNING)
    third_party.propagate = True
    third_party.disabled = False
    try:
        configure_logging()
        logging.getLogger("app").warning("application secret", extra={"event": "log"})
        access_logger.warning('127.0.0.1 - "GET /health?secret=value HTTP/1.1" 200')
        third_party.warning("third-party message")
        assert "application secret" not in root_output.getvalue()
        assert access_output.getvalue() == ""
        assert "third-party message" in root_output.getvalue()
        assert "third-party message" in third_party_output.getvalue()
        assert '{"event":' not in root_output.getvalue()
    finally:
        third_party.setLevel(previous_level)
        third_party.propagate = previous_propagate
        third_party.disabled = previous_disabled
        access_logger.disabled = previous_access_disabled
        access_logger.removeHandler(access_handler)
        access_handler.close()
        third_party.removeHandler(third_party_handler)
        third_party_handler.close()
        root.removeHandler(unrelated_handler)
        unrelated_handler.close()


def test_repeated_logging_configuration_keeps_one_owned_app_handler() -> None:
    root = logging.getLogger()
    root_handlers = list(root.handlers)
    configure_logging()
    first = [
        handler
        for handler in logging.getLogger("app").handlers
        if getattr(handler, "_escrow_application_handler", False)
    ]
    configure_logging()
    second = [
        handler
        for handler in logging.getLogger("app").handlers
        if getattr(handler, "_escrow_application_handler", False)
    ]

    assert root.handlers == root_handlers
    assert len(first) == len(second) == 1
    assert first[0] is second[0]


def test_metrics_endpoint_is_exposed_once_and_uses_low_cardinality_labels() -> None:
    client = TestClient(app)
    first = client.get("/api/v1/health")
    second = client.get("/api/v1/health")
    unmatched = client.get("/not-registered").status_code
    metrics = client.get(settings.metrics_path)

    assert first.status_code == second.status_code == 200
    assert unmatched == 404
    assert metrics.status_code == 200
    assert metrics.headers["content-type"] == CONTENT_TYPE_LATEST
    assert "http_requests_total" in metrics.text
    assert "/api/v1/health" in metrics.text
    assert 'route="unmatched"' in metrics.text
    assert 'request_id="' not in metrics.text
    assert "8675309" not in metrics.text
    assert sum(getattr(route, "path", None) == settings.metrics_path for route in app.routes) == 1


def test_metrics_scrape_does_not_instrument_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "metrics_path", "/internal/prometheus")
    fresh_app = create_application()
    client = TestClient(fresh_app)
    route_labels = {
        "method": "GET",
        "route": settings.metrics_path,
        "status_class": "2xx",
    }
    before_counter = float(REGISTRY.get_sample_value("http_requests_total", route_labels) or 0)
    before_gauge = float(
        REGISTRY.get_sample_value("http_requests_in_progress", {"method": "GET"}) or 0
    )

    response = client.get(settings.metrics_path, headers={"X-Request-ID": "metrics-trace"})

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "metrics-trace"
    assert float(REGISTRY.get_sample_value("http_requests_total", route_labels) or 0) == (
        before_counter
    )
    assert (
        float(REGISTRY.get_sample_value("http_requests_in_progress", {"method": "GET"}) or 0)
        == before_gauge
    )
    assert f'route="{settings.metrics_path}"' not in response.text


def test_metrics_endpoint_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "metrics_enabled", False)
    disabled_app = create_application()

    response = TestClient(disabled_app).get(settings.metrics_path)

    assert response.status_code == 404


def test_app_factories_do_not_duplicate_middleware_or_metric_routes() -> None:
    first = create_application()
    second = create_application()

    assert len(first.user_middleware) == 1
    assert len(second.user_middleware) == 1
    assert sum(getattr(route, "path", None) == settings.metrics_path for route in first.routes) == 1
    assert (
        sum(getattr(route, "path", None) == settings.metrics_path for route in second.routes) == 1
    )


def test_http_methods_are_normalized_to_a_bounded_metric_label() -> None:
    assert normalize_http_method("get") == "GET"
    assert normalize_http_method("X-ATTACKER-METHOD") == "OTHER"

    before = float(
        REGISTRY.get_sample_value(
            "http_requests_total",
            {"method": "OTHER", "route": "/method-test", "status_class": "2xx"},
        )
        or 0
    )
    observe_http_start("X-ATTACKER-METHOD")
    observe_http_complete(
        method="X-ATTACKER-METHOD",
        route="/method-test",
        status_code=200,
        duration_seconds=0.001,
    )
    after = float(
        REGISTRY.get_sample_value(
            "http_requests_total",
            {"method": "OTHER", "route": "/method-test", "status_class": "2xx"},
        )
        or 0
    )
    assert after == before + 1


def test_http_in_progress_gauge_returns_to_baseline_after_failure() -> None:
    before = float(REGISTRY.get_sample_value("http_requests_in_progress", {"method": "GET"}) or 0)

    observe_http_start("GET")
    observe_http_complete(
        method="GET",
        route="/failure-test",
        status_code=500,
        duration_seconds=0.001,
    )

    assert REGISTRY.get_sample_value("http_requests_in_progress", {"method": "GET"}) == before


@pytest.mark.parametrize(
    "path",
    [
        "/metrics/{format}",
        "/metrics with-space",
        "/metrics\n",
        "/metrics//internal",
        "/metrics?format=text",
        "/metrics#fragment",
        "/" + "m" * 128,
    ],
)
def test_metrics_path_rejects_dynamic_or_malformed_values(path: str) -> None:
    with pytest.raises(ValidationError):
        Settings(metrics_path=path)


def test_liveness_does_not_require_database() -> None:
    client = TestClient(app)

    response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_readiness_success_and_failure_are_sanitized() -> None:
    app.dependency_overrides[health_module.database_readiness_check] = lambda: True
    try:
        client = TestClient(app)
        assert client.get("/api/v1/health/ready").json() == {"status": "ready"}

        app.dependency_overrides[health_module.database_readiness_check] = lambda: False
        response = client.get("/api/v1/health/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready"}
        assert "database" not in response.text.lower()
    finally:
        app.dependency_overrides.pop(health_module.database_readiness_check, None)


class _FakeSession:
    def __init__(
        self,
        *,
        delay: float = 0,
        enter_delay: float = 0,
        error: Exception | None = None,
    ) -> None:
        self._delay = delay
        self._enter_delay = enter_delay
        self._error = error

    async def __aenter__(self) -> _FakeSession:
        if self._enter_delay:
            await asyncio.sleep(self._enter_delay)
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def execute(self, query: object) -> None:
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error


class _FakeSessionFactory:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    def __call__(self) -> _FakeSession:
        return self._session


@pytest.mark.anyio
async def test_database_readiness_handles_dependency_failure_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        health_module,
        "AsyncSessionFactory",
        _FakeSessionFactory(_FakeSession(error=RuntimeError("secret connection details"))),
    )
    assert await health_module.database_readiness_check() is False

    monkeypatch.setattr(settings, "readiness_timeout_seconds", 0.001)
    monkeypatch.setattr(
        health_module,
        "AsyncSessionFactory",
        _FakeSessionFactory(_FakeSession(delay=0.05)),
    )
    assert await health_module.database_readiness_check() is False

    monkeypatch.setattr(
        health_module,
        "AsyncSessionFactory",
        _FakeSessionFactory(_FakeSession(enter_delay=0.05)),
    )
    assert await health_module.database_readiness_check() is False

    class _CancelledSession(_FakeSession):
        async def execute(self, query: object) -> None:
            raise asyncio.CancelledError

    monkeypatch.setattr(
        health_module,
        "AsyncSessionFactory",
        _FakeSessionFactory(_CancelledSession()),
    )
    with pytest.raises(asyncio.CancelledError):
        await health_module.database_readiness_check()
