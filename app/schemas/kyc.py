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
    rejection_reason: str | None
    consented_at: datetime
    submitted_at: datetime
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
