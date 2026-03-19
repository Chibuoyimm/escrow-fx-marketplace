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
    VERIFIED = "verified"
    REJECTED = "rejected"


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

