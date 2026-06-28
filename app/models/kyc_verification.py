"""KYC verification ORM model."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.entities import KycVerification
from app.domain.enums import KycIdType, KycProvider, KycVerificationStatus
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class KycVerificationModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """KYC verification attempt row."""

    __tablename__ = "kyc_verifications"
    __table_args__ = (
        Index("ix_kyc_verifications_user_created", "user_id", "created_at"),
        Index("ix_kyc_verifications_provider_reference", "provider", "provider_reference_id"),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="RESTRICT"),
        index=True,
    )
    provider: Mapped[KycProvider] = mapped_column(
        Enum(KycProvider, native_enum=False, validate_strings=True)
    )
    provider_reference_id: Mapped[str] = mapped_column(String(128))
    id_type: Mapped[KycIdType] = mapped_column(
        Enum(KycIdType, native_enum=False, validate_strings=True)
    )
    masked_identifier: Mapped[str] = mapped_column(String(32))
    identifier_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[KycVerificationStatus] = mapped_column(
        Enum(KycVerificationStatus, native_enum=False, validate_strings=True),
        index=True,
    )
    provider_status: Mapped[str] = mapped_column(String(64))
    field_match_summary: Mapped[dict[str, Any]] = mapped_column(JSON)
    review_events: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def to_domain(self) -> KycVerification:
        """Convert the ORM row to a domain entity."""
        return KycVerification(
            id=self.id,
            user_id=self.user_id,
            provider=self.provider,
            provider_reference_id=self.provider_reference_id,
            id_type=self.id_type,
            masked_identifier=self.masked_identifier,
            identifier_hash=self.identifier_hash,
            status=self.status,
            provider_status=self.provider_status,
            field_match_summary=self.field_match_summary,
            review_events=self.review_events,
            rejection_reason=self.rejection_reason,
            consented_at=self.consented_at,
            submitted_at=self.submitted_at,
            completed_at=self.completed_at,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
