"""Schemas for exchange offer APIs."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import ExchangeOfferStatus, ExchangeRequestStatus


class CreateExchangeOfferRequest(BaseModel):
    """Payload for creating an exchange offer."""

    offered_rate: Decimal = Field(gt=0)


class UpdateExchangeOfferRequest(BaseModel):
    """Offer field that remains mutable while the offer is active."""

    offered_rate: Decimal = Field(gt=0)
    model_config = ConfigDict(extra="forbid")


class ExchangeOfferResponse(BaseModel):
    """Exchange offer response payload."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    request_id: UUID
    offer_user_id: UUID
    request_status: ExchangeRequestStatus
    from_currency_code: str
    to_currency_code: str
    request_from_amount: Decimal
    request_preferred_rate: Decimal
    request_expires_at: datetime
    offered_rate: Decimal
    status: ExchangeOfferStatus
    expires_at: datetime
    created_at: datetime
    updated_at: datetime
