"""Schemas for trade APIs."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.enums import TradeContractStatus


class TradeContractResponse(BaseModel):
    """Trade contract response payload."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    request_id: UUID
    accepted_offer_id: UUID
    requester_user_id: UUID
    counterparty_user_id: UUID
    from_currency_code: str
    to_currency_code: str
    agreed_rate: Decimal
    reference_rate_snapshot: Decimal | None
    from_amount: Decimal
    to_amount: Decimal
    funding_deadline_at: datetime
    status: TradeContractStatus
    created_at: datetime
    updated_at: datetime
