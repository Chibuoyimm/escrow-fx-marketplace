"""Currency ORM model."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Enum, Numeric, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.entities import Currency
from app.domain.enums import CurrencyStatus
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CurrencyModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Configured currency row."""

    __tablename__ = "currencies"

    code: Mapped[str] = mapped_column(String(3), unique=True)
    minor_unit: Mapped[int] = mapped_column(SmallInteger)
    status: Mapped[CurrencyStatus] = mapped_column(
        Enum(CurrencyStatus, native_enum=False, validate_strings=True)
    )
    min_amount: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    max_amount: Mapped[Decimal] = mapped_column(Numeric(24, 8))

    def to_domain(self) -> Currency:
        """Convert the ORM row to a domain entity."""
        return Currency(
            id=self.id,
            code=self.code,
            minor_unit=self.minor_unit,
            status=self.status,
            min_amount=self.min_amount,
            max_amount=self.max_amount,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
