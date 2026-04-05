"""Shared helpers for service-layer modules."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from app.infrastructure.database.base import utc_now as _db_utc_now
from app.infrastructure.database.session import AsyncSessionFactory
from app.infrastructure.database.unit_of_work import (
    AbstractUnitOfWork,
    SqlAlchemyUnitOfWork,
)

__all__ = ["UnitOfWorkFactory", "as_utc", "build_uow", "utc_now"]

UnitOfWorkFactory = Callable[[], AbstractUnitOfWork]


def utc_now() -> datetime:
    """Return the current UTC time."""
    return _db_utc_now()


def as_utc(value: datetime) -> datetime:
    """Normalize datetimes returned by different DB backends."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def build_uow() -> AbstractUnitOfWork:
    """Build the default unit of work."""
    return SqlAlchemyUnitOfWork(AsyncSessionFactory)
