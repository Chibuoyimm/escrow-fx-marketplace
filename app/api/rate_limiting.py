"""FastAPI dependencies for centrally configured rate-limit policies."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any
from uuid import UUID

from fastapi import Depends, Request
from pydantic import BaseModel, ValidationError

from app.api.dependencies import get_current_principal
from app.domain.auth import AuthenticatedPrincipal
from app.domain.exceptions import InvariantViolationError
from app.infrastructure.idempotency import (
    build_idempotency_fingerprint,
    hash_idempotency_key,
)
from app.infrastructure.rate_limiting import (
    RateLimitService,
    get_rate_limit_service,
    resolve_client_ip,
)

RateLimitDependency = Callable[..., Awaitable[None]]
rate_limit_service_dependency = Depends(get_rate_limit_service)
current_principal_dependency = Depends(get_current_principal)


def public_rate_limit_dependency(
    policy_name: str,
    *,
    identifier_field: str | None = None,
) -> RateLimitDependency:
    """Build a public endpoint limiter using IP plus an optional body identifier."""

    async def dependency(
        request: Request,
        service: RateLimitService = rate_limit_service_dependency,
    ) -> None:
        identities = {"ip": _client_ip(request)}
        if identifier_field is not None:
            value = await _body_value(request, identifier_field)
            if value:
                identities["account" if identifier_field == "email" else "token"] = (
                    value.strip().casefold() if identifier_field == "email" else value
                )
        decision = await service.enforce_or_raise(
            policy_name=policy_name,
            identities=identities,
        )
        _store_headers(request, decision.headers)

    return dependency


def authenticated_rate_limit_dependency(
    policy_name: str,
    *,
    idempotency_scope_template: str | None = None,
    idempotency_payload_model: type[BaseModel] | None = None,
    idempotency_payload_exclude_unset: bool = False,
    idempotency_payload_optional: bool = False,
) -> RateLimitDependency:
    """Build an authenticated limiter keyed primarily by the current user.

    When configured, the dependency performs a best-effort Pydantic preflight
    using the same model dump settings as the route's idempotency request.
    Invalid keys or invalid bodies intentionally skip replay detection but
    still consume the normal rate-limit capacity.
    """

    async def dependency(
        request: Request,
        principal: AuthenticatedPrincipal = current_principal_dependency,
        service: RateLimitService = rate_limit_service_dependency,
    ) -> None:
        identities = {"user": str(principal.user_id)}
        idempotency_key_hash: str | None = None
        idempotency_scope: str | None = None
        idempotency_request_fingerprint: str | None = None
        if idempotency_scope_template is not None:
            raw_key = request.headers.get("Idempotency-Key")
            if raw_key is not None:
                try:
                    idempotency_key_hash = hash_idempotency_key(raw_key)
                except InvariantViolationError:
                    idempotency_key_hash = None
                if idempotency_key_hash is not None:
                    idempotency_scope = _format_scope(idempotency_scope_template, request)
                    payload = await _idempotency_payload(
                        request,
                        model=idempotency_payload_model,
                        exclude_unset=idempotency_payload_exclude_unset,
                        optional=idempotency_payload_optional,
                    )
                    if payload is not None:
                        idempotency_request_fingerprint = build_idempotency_fingerprint(
                            operation_scope=idempotency_scope,
                            payload=payload,
                        )
        decision = await service.enforce_or_raise(
            policy_name=policy_name,
            identities=identities,
            principal_user_id=str(principal.user_id),
            idempotency_scope=idempotency_scope,
            idempotency_key_hash=idempotency_key_hash,
            idempotency_request_fingerprint=idempotency_request_fingerprint,
        )
        _store_headers(request, decision.headers)

    return dependency


async def _body_value(request: Request, field: str) -> str | None:
    """Read a small JSON body field without storing or logging its value."""
    try:
        body = await request.body()
        if len(body) > 64 * 1024:
            return None
        payload: Any = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get(field)
    return value if isinstance(value, str) and value else None


async def _idempotency_payload(
    request: Request,
    *,
    model: type[BaseModel] | None,
    exclude_unset: bool,
    optional: bool,
) -> dict[str, Any] | None:
    """Model a mutation body exactly as its route does, without raising early."""
    if model is None:
        return {}
    body = await request.body()
    if len(body) > 64 * 1024:
        return None
    if not body.strip():
        raw_payload: Any = {}
    else:
        try:
            raw_payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
    if raw_payload is None and optional:
        raw_payload = {}
    try:
        parsed = model.model_validate(raw_payload)
    except ValidationError:
        return None
    payload = parsed.model_dump(mode="python", exclude_unset=exclude_unset)
    return payload if isinstance(payload, dict) else None


def _format_scope(template: str, request: Request) -> str:
    """Format route scopes using the same canonical UUID text as route handlers."""
    values: dict[str, str] = {}
    for name, value in request.path_params.items():
        text = str(value)
        with suppress(ValueError):
            text = str(UUID(text))
        values[name] = text
    return template.format(**values)


def _client_ip(request: Request) -> str:
    return resolve_client_ip(
        request.client.host if request.client is not None else None,
        request.headers.get("X-Forwarded-For"),
    )


def _store_headers(request: Request, headers: dict[str, str]) -> None:
    if headers:
        request.state.rate_limit_headers = headers
