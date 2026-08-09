"""Centralized exception handling for the API layer."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from app.domain.exceptions import AppError, ErrorCode
from app.infrastructure.application_logging import current_request_id
from app.infrastructure.exceptions import InfrastructureError
from app.schemas.problem import ProblemDetails

ExceptionHandler = Callable[[Request, Exception], Response | Awaitable[Response]]
logger = logging.getLogger(__name__)


def _request_instance(request: Request) -> str:
    return str(request.url.path)


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None) or current_request_id()


def _response(problem: ProblemDetails) -> JSONResponse:
    response = JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(exclude_none=True),
        media_type="application/problem+json",
    )
    if problem.request_id is not None:
        response.headers["X-Request-ID"] = problem.request_id
    return response


async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    """Serialize expected application errors as problem details."""
    problem = ProblemDetails(
        title=exc.title,
        status=exc.status_code,
        detail=exc.detail,
        instance=_request_instance(request),
        error_code=exc.error_code,
        request_id=_request_id(request),
    )
    response = _response(problem)
    if exc.headers is not None:
        response.headers.update(exc.headers)
    return response


async def handle_infrastructure_error(request: Request, exc: InfrastructureError) -> JSONResponse:
    """Serialize infrastructure failures without leaking internals."""
    logger.warning(
        "infrastructure_error",
        extra={
            "event": "infrastructure_error",
            "error_code": exc.error_code,
            "request_id": _request_id(request),
        },
    )
    problem = ProblemDetails(
        title=exc.title,
        status=exc.status_code,
        detail="The service could not complete the request.",
        instance=_request_instance(request),
        error_code=exc.error_code,
        request_id=_request_id(request),
    )
    return _response(problem)


async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Serialize request validation errors."""
    problem = ProblemDetails(
        title="Validation Error",
        status=422,
        detail="Request validation failed.",
        instance=_request_instance(request),
        error_code=ErrorCode.VALIDATION_ERROR,
        request_id=_request_id(request),
        errors=exc.errors(),
    )
    return _response(problem)


async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Normalize framework HTTP exceptions into the API error format."""
    detail = str(exc.detail) if exc.status_code < 500 else "An unexpected error occurred."
    problem = ProblemDetails(
        title="HTTP Error",
        status=exc.status_code,
        detail=detail,
        instance=_request_instance(request),
        error_code=ErrorCode.INTERNAL_ERROR
        if exc.status_code >= 500
        else ErrorCode.PRECONDITION_FAILED,
        request_id=_request_id(request),
    )
    return _response(problem)


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Return a sanitized response for unhandled exceptions."""
    logger.error(
        "unhandled_request_error",
        extra={
            "event": "unhandled_request_error",
            "exception_type": type(exc).__name__,
            "request_id": _request_id(request),
        },
    )
    problem = ProblemDetails(
        title="Internal Server Error",
        status=500,
        detail="An unexpected error occurred.",
        instance=_request_instance(request),
        error_code=ErrorCode.INTERNAL_ERROR,
        request_id=_request_id(request),
    )
    return _response(problem)


def register_exception_handlers(application: FastAPI) -> None:
    """Register all API exception handlers."""
    application.add_exception_handler(AppError, cast(ExceptionHandler, handle_app_error))
    application.add_exception_handler(
        InfrastructureError,
        cast(ExceptionHandler, handle_infrastructure_error),
    )
    application.add_exception_handler(
        RequestValidationError,
        cast(ExceptionHandler, handle_validation_error),
    )
    application.add_exception_handler(
        StarletteHTTPException,
        cast(ExceptionHandler, handle_http_exception),
    )
    application.add_exception_handler(Exception, cast(ExceptionHandler, handle_unexpected_error))
