"""Helpers for recording outbox events."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from app.domain.entities import OutboxEvent
from app.domain.enums import OutboxEventStatus
from app.services._shared import utc_now


def build_outbox_event(
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: UUID,
    recipient_user_id: UUID | None,
    payload: dict[str, Any],
) -> OutboxEvent:
    """Build a pending outbox event."""
    current_time = utc_now()
    return OutboxEvent(
        id=uuid4(),
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        recipient_user_id=recipient_user_id,
        payload=payload,
        status=OutboxEventStatus.PENDING,
        attempt_count=0,
        next_attempt_at=current_time,
        last_error=None,
        created_at=current_time,
        updated_at=current_time,
    )
