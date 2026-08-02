"""Mutation idempotency ORM model."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.entities import IdempotencyRecord
from app.domain.enums import IdempotencyRecordStatus
from app.models.base import Base, UUIDPrimaryKeyMixin


class IdempotencyRecordModel(UUIDPrimaryKeyMixin, Base):
    """Replay record for an authenticated non-repeatable mutation."""

    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "principal_user_id",
            "operation_scope",
            "key_hash",
            name="uq_idempotency_records_principal_scope_key",
        ),
        Index("ix_idempotency_records_expires_at", "expires_at"),
    )

    principal_user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="RESTRICT"),
    )
    operation_scope: Mapped[str] = mapped_column(String(200))
    key_hash: Mapped[str] = mapped_column(String(64))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[IdempotencyRecordStatus] = mapped_column(
        Enum(IdempotencyRecordStatus, native_enum=False, length=16, validate_strings=True)
    )
    response_status_code: Mapped[int | None] = mapped_column(nullable=True)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def to_domain(self) -> IdempotencyRecord:
        """Convert the ORM row to a domain entity."""
        return IdempotencyRecord(
            id=self.id,
            principal_user_id=self.principal_user_id,
            operation_scope=self.operation_scope,
            key_hash=self.key_hash,
            request_fingerprint=self.request_fingerprint,
            status=self.status,
            response_status_code=self.response_status_code,
            response_body=self.response_body,
            created_at=self.created_at,
            updated_at=self.updated_at,
            expires_at=self.expires_at,
            completed_at=self.completed_at,
        )
