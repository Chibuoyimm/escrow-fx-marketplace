"""Transport-neutral helpers for authenticated mutation idempotency."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from app.domain.entities import IdempotencyRecord
from app.domain.enums import IdempotencyRecordStatus
from app.domain.exceptions import ConflictError, InvariantViolationError
from app.infrastructure.database.unit_of_work import AbstractUnitOfWork

_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{1,128}$")


@dataclass(frozen=True, slots=True)
class IdempotencyRequest:
    """Validated idempotency material supplied by an API route."""

    principal_user_id: UUID
    operation_scope: str
    key_hash: str
    request_fingerprint: str


@dataclass(frozen=True, slots=True)
class IdempotencyReplay:
    """A response previously committed for an idempotent mutation."""

    status_code: int
    response_body: dict[str, Any]


def build_idempotency_request(
    *,
    principal_user_id: UUID,
    key: str | None,
    operation_scope: str,
    payload: Any,
) -> IdempotencyRequest | None:
    """Validate a key and create a canonical, non-sensitive request fingerprint."""
    if key is None:
        return None
    if not _KEY_PATTERN.fullmatch(key):
        raise InvariantViolationError(
            "Idempotency-Key must contain 1-128 ASCII letters, digits, '.', '_', '~', or '-'."
        )
    return IdempotencyRequest(
        principal_user_id=principal_user_id,
        operation_scope=operation_scope,
        key_hash=hash_idempotency_key(key),
        request_fingerprint=build_idempotency_fingerprint(
            operation_scope=operation_scope,
            payload=payload,
        ),
    )


def hash_idempotency_key(key: str) -> str:
    """Validate and hash an Idempotency-Key for replay preflight."""
    if not _KEY_PATTERN.fullmatch(key):
        raise InvariantViolationError(
            "Idempotency-Key must contain 1-128 ASCII letters, digits, '.', '_', '~', or '-'."
        )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def build_idempotency_fingerprint(*, operation_scope: str, payload: Any) -> str:
    """Build the canonical fingerprint shared by routes and rate-limit preflight."""
    if not 1 <= len(operation_scope) <= 200:
        raise InvariantViolationError("The idempotency operation scope is invalid.")

    canonical_payload = _canonical_json(payload)
    fingerprint_input = json.dumps(
        {"operation_scope": operation_scope, "payload": canonical_payload},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(fingerprint_input).hexdigest()


def serialize_idempotency_response(value: Any) -> dict[str, Any]:
    """Serialize a customer-facing domain projection without persisting secrets."""
    encoded = _json_safe(value)
    if not isinstance(encoded, dict):
        raise TypeError("Idempotency responses must serialize to a JSON object.")
    return encoded


async def claim_idempotency(
    uow: AbstractUnitOfWork,
    request: IdempotencyRequest | None,
    *,
    now: datetime,
) -> IdempotencyRecord | IdempotencyReplay | None:
    """Claim a key in the current mutation transaction or return its replay."""
    if request is None:
        return None
    record = await uow.idempotency_records.claim(
        principal_user_id=request.principal_user_id,
        operation_scope=request.operation_scope,
        key_hash=request.key_hash,
        request_fingerprint=request.request_fingerprint,
        now=now,
    )
    if record.status is IdempotencyRecordStatus.COMPLETED:
        if record.response_status_code is None or record.response_body is None:
            raise ConflictError("That idempotency request is incomplete; retry the request.")
        return IdempotencyReplay(record.response_status_code, record.response_body)
    return record


async def complete_idempotency(
    uow: AbstractUnitOfWork,
    claim: IdempotencyRecord | IdempotencyReplay | None,
    *,
    response_status_code: int,
    response: Any,
    now: datetime,
) -> None:
    """Store the response before the surrounding business transaction commits."""
    if not isinstance(claim, IdempotencyRecord):
        return
    await uow.idempotency_records.complete(
        record_id=claim.id,
        response_status_code=response_status_code,
        response_body=serialize_idempotency_response(response),
        now=now,
    )


def _json_safe(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is not None and value.utcoffset() == UTC.utcoffset(value):
            return value.isoformat().replace("+00:00", "Z")
        return value.isoformat()
    if isinstance(value, Enum):
        return _json_safe(value.value)
    return value


def _canonical_json(value: Any) -> Any:
    """Normalize semantically equivalent request values before hashing."""
    if is_dataclass(value) and not isinstance(value, type):
        return _canonical_json(asdict(value))
    if isinstance(value, dict):
        return {str(key): _canonical_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_json(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        formatted = format(value, "f")
        if "." in formatted:
            formatted = formatted.rstrip("0").rstrip(".")
        return formatted or "0"
    if isinstance(value, datetime):
        return _json_safe(value)
    if isinstance(value, Enum):
        return _canonical_json(value.value)
    return value
