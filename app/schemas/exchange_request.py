"""Schemas for exchange request APIs."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import ExchangeRequestStatus


class CreateExchangeRequestRequest(BaseModel):
    """Payload for creating an exchange request."""

    from_currency_code: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    to_currency_code: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    from_amount: Decimal = Field(gt=0)
    preferred_rate: Decimal = Field(gt=0)
    min_rate: Decimal | None = Field(default=None, gt=0)

    @field_validator("from_currency_code", "to_currency_code", mode="before")
    @classmethod
    def normalize_currency_code(cls, value: object) -> object:
        """Normalize currency codes before validation."""
        if isinstance(value, str):
            return value.strip().upper()
        return value


class ExchangeRequestResponse(BaseModel):
    """Exchange request response payload."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    creator_user_id: UUID
    from_currency_code: str
    to_currency_code: str
    from_amount: Decimal
    preferred_rate: Decimal
    min_rate: Decimal | None
    status: ExchangeRequestStatus
    expires_at: datetime
    created_at: datetime
    updated_at: datetime
