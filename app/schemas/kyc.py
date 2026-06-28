"""KYC request and response schemas."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import KycIdType, KycProvider, KycVerificationStatus


class KycSubmitRequest(BaseModel):
    """Payload for submitting a KYC identity check."""

    id_type: KycIdType
    id_number: str = Field(min_length=6, max_length=32)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    date_of_birth: date
    subject_consent: bool

    @field_validator("id_number", "first_name", "last_name", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        """Trim text inputs before validation."""
        if isinstance(value, str):
            return value.strip()
        return value


class KycVerificationResponse(BaseModel):
    """Response for a KYC verification attempt."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider: KycProvider
    provider_reference_id: str
    id_type: KycIdType
    masked_identifier: str
    status: KycVerificationStatus
    provider_status: str
    field_match_summary: dict[str, object]
    review_events: list[KycReviewEventResponse]
    rejection_reason: str | None
    consented_at: datetime
    submitted_at: datetime
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AdminKycRejectRequest(BaseModel):
    """Payload for rejecting a reviewed KYC verification."""

    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, value: object) -> object:
        """Trim reason text before validation."""
        if isinstance(value, str):
            return value.strip()
        return value


class AdminKycReviewNoteRequest(BaseModel):
    """Payload for adding an internal KYC review note."""

    note: str = Field(min_length=1, max_length=1000)

    @field_validator("note", mode="before")
    @classmethod
    def strip_note(cls, value: object) -> object:
        """Trim note text before validation."""
        if isinstance(value, str):
            return value.strip()
        return value


class KycReviewEventResponse(BaseModel):
    """Structured admin review history for a KYC verification."""

    event_type: str
    reviewer_user_id: UUID
    created_at: datetime
    decision: str | None = None
    reason: str | None = None
    note: str | None = None
