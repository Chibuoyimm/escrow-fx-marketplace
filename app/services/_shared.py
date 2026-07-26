"""Shared helpers for service-layer modules."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from app.infrastructure.database.base import utc_now as _db_utc_now
from app.infrastructure.database.unit_of_work import (
    AbstractUnitOfWork,
    SqlAlchemyUnitOfWork,
)

__all__ = [
    "UnitOfWorkFactory",
    "as_utc",
    "build_uow",
    "format_decimal",
    "format_display_datetime",
    "utc_now",
]

UnitOfWorkFactory = Callable[[], AbstractUnitOfWork]


def utc_now() -> datetime:
    """Return the current UTC time."""
    return _db_utc_now()


def as_utc(value: datetime) -> datetime:
    """Normalize datetimes returned by different DB backends."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def format_display_datetime(value: datetime) -> str:
    """Format a UTC timestamp for customer-facing notification payloads."""
    formatted = as_utc(value).strftime("%B %d, %Y at %I:%M %p UTC")
    return formatted.replace(" 0", " ").replace(" at 0", " at ")


def format_decimal(value: Decimal) -> str:
    """Format a decimal without exponent notation or insignificant trailing zeros."""
    formatted = format(value, "f")
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return formatted


def build_uow() -> AbstractUnitOfWork:
    """Build the default unit of work."""
    from app.infrastructure.database.session import AsyncSessionFactory

    return SqlAlchemyUnitOfWork(AsyncSessionFactory)
