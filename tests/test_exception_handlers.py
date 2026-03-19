from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.exception_handlers import register_exception_handlers
from app.domain.exceptions import NotFoundError
from app.infrastructure.exceptions import InfrastructureError
from app.infrastructure.request_context import register_request_context
from app.main import app


def build_test_app() -> FastAPI:
    application = FastAPI()
    register_request_context(application)
    register_exception_handlers(application)

    @application.get("/not-found")
    async def not_found() -> None:
        raise NotFoundError("Currency 'XYZ' was not found.")

    @application.get("/validation")
    async def validation(limit: int) -> dict[str, int]:
        return {"limit": limit}

    @application.get("/unexpected")
    async def unexpected() -> None:
        raise RuntimeError("boom")

    @application.get("/infrastructure")
    async def infrastructure() -> None:
        raise InfrastructureError(title="Database Error", detail="connection refused")

    @application.get("/http")
    async def http_error() -> None:
        raise HTTPException(status_code=412, detail="precondition failed")

    return application


def test_domain_not_found_is_rendered_as_problem_details() -> None:
    client = TestClient(build_test_app())

    response = client.get("/not-found", headers={"X-Request-ID": "req-123"})

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["x-request-id"] == "req-123"
    assert response.json()["error_code"] == "not_found"
    assert response.json()["request_id"] == "req-123"


def test_validation_errors_are_rendered_as_problem_details() -> None:
    client = TestClient(build_test_app())

    response = client.get("/validation")

    assert response.status_code == 422
    assert response.json()["error_code"] == "validation_error"
    assert response.json()["errors"]


def test_infrastructure_errors_are_sanitized() -> None:
    client = TestClient(build_test_app())

    response = client.get("/infrastructure", headers={"X-Request-ID": "req-infra"})

    assert response.status_code == 503
    assert response.json()["error_code"] == "infrastructure_failure"
    assert response.json()["detail"] == "The service could not complete the request."
    assert response.json()["request_id"] == "req-infra"


def test_http_exceptions_are_normalized() -> None:
    client = TestClient(build_test_app())

    response = client.get("/http")

    assert response.status_code == 412
    assert response.json()["error_code"] == "precondition_failed"
    assert response.json()["detail"] == "precondition failed"


def test_unexpected_errors_are_sanitized() -> None:
    client = TestClient(build_test_app(), raise_server_exceptions=False)

    response = client.get("/unexpected")

    assert response.status_code == 500
    assert response.json()["error_code"] == "internal_error"
    assert response.json()["detail"] == "An unexpected error occurred."


def test_normal_responses_include_request_id_header() -> None:
    client = TestClient(app)

    response = client.get("/api/v1/health", headers={"X-Request-ID": "req-health"})

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "req-health"
