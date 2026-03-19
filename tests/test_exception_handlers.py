from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.exception_handlers import register_exception_handlers
from app.domain.exceptions import NotFoundError
from app.infrastructure.request_context import register_request_context


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


def test_unexpected_errors_are_sanitized() -> None:
    client = TestClient(build_test_app(), raise_server_exceptions=False)

    response = client.get("/unexpected")

    assert response.status_code == 500
    assert response.json()["error_code"] == "internal_error"
    assert response.json()["detail"] == "An unexpected error occurred."

