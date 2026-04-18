"""Domain entities for the foundation slice."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.domain.enums import (
    CorridorStatus,
    CurrencyStatus,
    ExchangeOfferStatus,
    ExchangeRequestStatus,
    FlowType,
    KycStatus,
    OutboxEventStatus,
    RailStatus,
    RiskLevel,
    TradeContractStatus,
    UserRole,
    UserStatus,
)


@dataclass(frozen=True, slots=True)
class User:
    """A platform user."""

    id: UUID
    email: str
    password_hash: str
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


@dataclass(frozen=True, slots=True)
class CorridorRailDetails:
    """A customer-facing corridor rail projection."""

    flow_type: FlowType
    priority_order: int
    provider: str
    method: str
    status: RailStatus


@dataclass(frozen=True, slots=True)
class CorridorDetails:
    """A customer-facing corridor projection."""

    id: UUID
    from_currency_code: str
    to_currency_code: str
    status: CorridorStatus
    funding_sla_minutes: int
    fee_model_name: str | None
    rails: tuple[CorridorRailDetails, ...]


@dataclass(frozen=True, slots=True)
class ExchangeRequest:
    """A user-created exchange request."""

    id: UUID
    creator_user_id: UUID
    from_currency_id: UUID
    to_currency_id: UUID
    from_amount: Decimal
    preferred_rate: Decimal
    min_rate: Decimal | None
    status: ExchangeRequestStatus
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ExchangeRequestDetails:
    """A customer-facing exchange request projection."""

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


@dataclass(frozen=True, slots=True)
class ExchangeOffer:
    """A counterparty offer made against an exchange request."""

    id: UUID
    request_id: UUID
    offer_user_id: UUID
    offered_rate: Decimal
    status: ExchangeOfferStatus
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ExchangeOfferDetails:
    """A customer-facing exchange offer projection."""

    id: UUID
    request_id: UUID
    offer_user_id: UUID
    offered_rate: Decimal
    status: ExchangeOfferStatus
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class TradeContract:
    """A locked trade created from an accepted offer."""

    id: UUID
    request_id: UUID
    accepted_offer_id: UUID
    agreed_rate: Decimal
    reference_rate_snapshot: Decimal | None
    from_amount: Decimal
    to_amount: Decimal
    funding_deadline_at: datetime
    status: TradeContractStatus
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class TradeContractDetails:
    """A participant-facing trade contract projection."""

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


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    """A durable event queued for later notification dispatch."""

    id: UUID
    event_type: str
    aggregate_type: str
    aggregate_id: UUID
    recipient_user_id: UUID | None
    payload: dict[str, Any]
    status: OutboxEventStatus
    attempt_count: int
    next_attempt_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime
