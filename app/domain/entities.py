"""Domain entities for the foundation slice."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.domain.enums import (
    CorridorStatus,
    CurrencyStatus,
    FlowType,
    KycStatus,
    RailStatus,
    RiskLevel,
    UserRole,
    UserStatus,
)


@dataclass(frozen=True, slots=True)
class User:
    """A platform user."""

    id: UUID
    email: str
    phone: str | None
    country: str
    role: UserRole
    status: UserStatus
    kyc_status: KycStatus
    risk_level: RiskLevel
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Currency:
    """A configured platform currency."""

    id: UUID
    code: str
    minor_unit: int
    status: CurrencyStatus
    min_amount: Decimal
    max_amount: Decimal
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Corridor:
    """A configured exchange corridor."""

    id: UUID
    from_currency_id: UUID
    to_currency_id: UUID
    status: CorridorStatus
    funding_sla_minutes: int
    fee_model_name: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class CorridorRail:
    """A routing option for a corridor."""

    id: UUID
    corridor_id: UUID
    flow_type: FlowType
    priority_order: int
    provider: str
    method: str
    status: RailStatus
    created_at: datetime
    updated_at: datetime
