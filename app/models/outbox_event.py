"""Outbox event ORM model."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.entities import OutboxEvent
from app.domain.enums import OutboxEventStatus
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class OutboxEventModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Durable event row for notification dispatch."""

    __tablename__ = "outbox_events"

    event_type: Mapped[str] = mapped_column(String(128), index=True)
    aggregate_type: Mapped[str] = mapped_column(String(64), index=True)
    aggregate_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    recipient_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[OutboxEventStatus] = mapped_column(
        Enum(OutboxEventStatus, native_enum=False, validate_strings=True),
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    def to_domain(self) -> OutboxEvent:
        """Convert the ORM row to a domain entity."""
        return OutboxEvent(
            id=self.id,
            event_type=self.event_type,
            aggregate_type=self.aggregate_type,
            aggregate_id=self.aggregate_id,
            recipient_user_id=self.recipient_user_id,
            payload=self.payload,
            status=self.status,
            attempt_count=self.attempt_count,
            next_attempt_at=self.next_attempt_at,
            last_error=self.last_error,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
