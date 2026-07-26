"""Cursor pagination primitives shared by API and repository layers."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.domain.exceptions import InvariantViolationError


@dataclass(frozen=True, slots=True)
class Cursor:
    """Position immediately after the last item in a descending page."""

    created_at: datetime
    item_id: UUID


def validate_range(
    minimum: Any,
    maximum: Any,
    field_name: str,
) -> None:
    """Validate an inclusive lower/upper filter range."""
    if minimum is not None and maximum is not None and minimum > maximum:
        raise InvariantViolationError(f"{field_name} minimum cannot exceed its maximum.")


def validate_date_range(start: datetime | None, end: datetime | None) -> None:
    """Validate an inclusive creation-date range."""
    if start is not None and end is not None and start > end:
        raise InvariantViolationError("created_from must be earlier than or equal to created_to.")


def normalize_datetime(value: datetime | None) -> datetime | None:
    """Normalize an API datetime bound to timezone-aware UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def normalize_date_range(
    start: datetime | None,
    end: datetime | None,
) -> tuple[datetime | None, datetime | None]:
    """Normalize inclusive date bounds before comparing or querying them."""
    normalized_start = normalize_datetime(start)
    normalized_end = normalize_datetime(end)
    validate_date_range(normalized_start, normalized_end)
    return normalized_start, normalized_end


def encode_next_cursor(cursor: Cursor | None) -> str | None:
    """Encode the next page position, if another page exists."""
    if cursor is None:
        return None
    return encode_cursor(cursor.created_at, cursor.item_id)


def encode_cursor(created_at: datetime, item_id: UUID) -> str:
    """Encode a stable timestamp/id position for a client."""
    normalized = (
        created_at.replace(tzinfo=UTC) if created_at.tzinfo is None else created_at.astimezone(UTC)
    )
    value = f"{normalized.isoformat()}|{item_id}"
    return base64.urlsafe_b64encode(value.encode("ascii")).decode("ascii").rstrip("=")


def decode_cursor(value: str | None) -> Cursor | None:
    """Decode a cursor and reject malformed positions as a domain validation error."""
    if value is None:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        raw_timestamp, raw_id = base64.urlsafe_b64decode(padded).decode("ascii").split("|", 1)
        timestamp = datetime.fromisoformat(raw_timestamp)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return Cursor(timestamp.astimezone(UTC), UUID(raw_id))
    except (ValueError, UnicodeError) as exc:
        raise InvariantViolationError("The pagination cursor is invalid.") from exc
