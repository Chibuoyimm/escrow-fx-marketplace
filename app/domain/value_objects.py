"""Domain value objects."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.exceptions import InvariantViolationError


def _normalize_decimal(value: Decimal) -> Decimal:
    normalized = value.normalize()
    if not normalized.is_finite():
        raise InvariantViolationError("Decimal values must be finite.")
    return normalized


@dataclass(frozen=True, slots=True)
class Money:
    """A monetary amount in a specific currency."""

    amount: Decimal
    currency_code: str

    def __post_init__(self) -> None:
        normalized = _normalize_decimal(self.amount)
        if normalized < Decimal("0"):
            raise InvariantViolationError("Money amounts cannot be negative.")
        object.__setattr__(self, "amount", normalized)
        object.__setattr__(self, "currency_code", self.currency_code.upper())


@dataclass(frozen=True, slots=True)
class Rate:
    """An exchange rate value."""

    value: Decimal

    def __post_init__(self) -> None:
        normalized = _normalize_decimal(self.value)
        if normalized <= Decimal("0"):
            raise InvariantViolationError("Rates must be greater than zero.")
        object.__setattr__(self, "value", normalized)

