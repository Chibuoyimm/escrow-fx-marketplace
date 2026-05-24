"""Domain enums used by the foundation slice."""

from enum import StrEnum


class UserRole(StrEnum):
    """Supported user roles."""

    CUSTOMER = "customer"
    ADMIN = "admin"
    OPERATIONS = "operations"


class UserStatus(StrEnum):
    """Lifecycle states for users."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    INACTIVE = "inactive"


class KycStatus(StrEnum):
    """KYC lifecycle states."""

    PENDING = "pending"
    REQUIRES_REVIEW = "requires_review"
    VERIFIED = "verified"
    REJECTED = "rejected"


class KycProvider(StrEnum):
    """Supported KYC verification providers."""

    LOCAL = "local"
    YOUVERIFY = "youverify"


class KycIdType(StrEnum):
    """Supported identity document or number types for KYC."""

    BVN = "bvn"
    NIN = "nin"
    VNIN = "vnin"


class KycVerificationStatus(StrEnum):
    """Lifecycle states for a provider-backed KYC verification attempt."""

    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"
    REQUIRES_REVIEW = "requires_review"


class RiskLevel(StrEnum):
    """Risk classifications."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CurrencyStatus(StrEnum):
    """Lifecycle states for currencies."""

    ACTIVE = "active"
    INACTIVE = "inactive"


class CorridorStatus(StrEnum):
    """Lifecycle states for corridors."""

    ACTIVE = "active"
    INACTIVE = "inactive"


class RailStatus(StrEnum):
    """Lifecycle states for corridor rails."""

    ACTIVE = "active"
    INACTIVE = "inactive"


class FlowType(StrEnum):
    """Rail flow types."""

    FUNDING = "funding"
    PAYOUT = "payout"


class ExchangeRequestStatus(StrEnum):
    """Lifecycle states for exchange requests."""

    REQUEST_OPEN = "request_open"
    OFFER_PENDING = "offer_pending"
    TERMS_LOCKED = "terms_locked"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ExchangeOfferStatus(StrEnum):
    """Lifecycle states for exchange offers."""

    ACTIVE = "active"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    WITHDRAWN = "withdrawn"


class TradeContractStatus(StrEnum):
    """Lifecycle states for trade contracts."""

    TERMS_LOCKED = "terms_locked"
    AWAITING_DUAL_FUNDING = "awaiting_dual_funding"
    ONE_LEG_FUNDED = "one_leg_funded"
    DUAL_FUNDED = "dual_funded"
    RELEASING = "releasing"
    SETTLED = "settled"
    EXPIRED_REFUNDING = "expired_refunding"
    CANCELLED = "cancelled"
    DISPUTED = "disputed"


class OutboxEventStatus(StrEnum):
    """Delivery lifecycle states for outbox events."""

    PENDING = "pending"
    PROCESSING = "processing"
    DELIVERED = "delivered"
    FAILED = "failed"
    DEAD = "dead"
