"""Schemas for reference-data responses."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.enums import CorridorStatus, CurrencyStatus, FlowType, RailStatus


class CurrencyResponse(BaseModel):
    """Public currency payload."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    minor_unit: int
    status: CurrencyStatus
    min_amount: Decimal
    max_amount: Decimal


class CorridorRailResponse(BaseModel):
    """Public corridor rail payload."""

    model_config = ConfigDict(from_attributes=True)

    flow_type: FlowType
    priority_order: int
    provider: str
    method: str
    status: RailStatus


class CorridorResponse(BaseModel):
    """Public corridor payload."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    from_currency_code: str
    to_currency_code: str
    status: CorridorStatus
    funding_sla_minutes: int
    fee_model_name: str | None
    rails: tuple[CorridorRailResponse, ...]
