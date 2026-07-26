"""Account audit event ORM model."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, Uuid, event
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from app.domain.entities import AccountAuditEvent
from app.domain.enums import AccountAuditEventType
from app.models.base import Base, UUIDPrimaryKeyMixin


class AccountAuditEventModel(UUIDPrimaryKeyMixin, Base):
    """Append-only history for security-sensitive account changes."""

    __tablename__ = "account_audit_events"
    __table_args__ = (
        Index("ix_account_audit_events_subject_occurred", "subject_user_id", "occurred_at"),
        Index("ix_account_audit_events_actor_occurred", "actor_user_id", "occurred_at"),
        Index("ix_account_audit_events_event_type", "event_type"),
    )

    subject_user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="RESTRICT"),
    )
    actor_user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="RESTRICT"),
    )
    event_type: Mapped[AccountAuditEventType] = mapped_column(
        Enum(AccountAuditEventType, native_enum=False, length=64, validate_strings=True),
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON)

    def to_domain(self) -> AccountAuditEvent:
        """Convert the ORM row to a domain entity."""
        return AccountAuditEvent(
            id=self.id,
            subject_user_id=self.subject_user_id,
            actor_user_id=self.actor_user_id,
            event_type=self.event_type,
            occurred_at=self.occurred_at,
            metadata=self.metadata_json,
        )


def _reject_account_audit_mutation(
    mapper: Mapper[AccountAuditEventModel],
    connection: Any,
    target: AccountAuditEventModel,
) -> None:
    del mapper, connection, target
    raise RuntimeError("Account audit events are append-only.")


event.listen(AccountAuditEventModel, "before_update", _reject_account_audit_mutation)
event.listen(AccountAuditEventModel, "before_delete", _reject_account_audit_mutation)
