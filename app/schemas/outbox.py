"""Schemas for outbox event APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.enums import OutboxEventStatus


class OutboxEventResponse(BaseModel):
    """Outbox event response payload for admin inspection."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_type: str
    aggregate_type: str
    aggregate_id: UUID
    recipient_user_id: UUID | None
    payload: dict[str, Any]
    status: OutboxEventStatus
    attempt_count: int
    next_attempt_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime
