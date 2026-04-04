"""Exchange request ORM model."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.entities import ExchangeRequest, ExchangeRequestDetails
from app.domain.enums import ExchangeRequestStatus
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.currency import CurrencyModel
from app.models.exchange_offer import ExchangeOfferModel
from app.models.user import UserModel

if TYPE_CHECKING:
    from app.models.trade_contract import TradeContractModel


class ExchangeRequestModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """User-created exchange request row."""

    __tablename__ = "exchange_requests"

    creator_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    from_currency_id: Mapped[UUID] = mapped_column(ForeignKey("currencies.id", ondelete="RESTRICT"))
    to_currency_id: Mapped[UUID] = mapped_column(ForeignKey("currencies.id", ondelete="RESTRICT"))
    from_amount: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    preferred_rate: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    min_rate: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    status: Mapped[ExchangeRequestStatus] = mapped_column(
        Enum(ExchangeRequestStatus, native_enum=False, validate_strings=True)
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    creator: Mapped[UserModel] = relationship()
    from_currency: Mapped[CurrencyModel] = relationship(foreign_keys=[from_currency_id])
    to_currency: Mapped[CurrencyModel] = relationship(foreign_keys=[to_currency_id])
    offers: Mapped[list[ExchangeOfferModel]] = relationship(
        back_populates="request",
        cascade="all, delete-orphan",
    )
    trade_contract: Mapped[TradeContractModel | None] = relationship(
        back_populates="request",
        uselist=False,
    )

    def to_domain(self) -> ExchangeRequest:
        """Convert the ORM row to a domain entity."""
        return ExchangeRequest(
            id=self.id,
            creator_user_id=self.creator_user_id,
            from_currency_id=self.from_currency_id,
            to_currency_id=self.to_currency_id,
            from_amount=self.from_amount,
            preferred_rate=self.preferred_rate,
            min_rate=self.min_rate,
            status=self.status,
            expires_at=self.expires_at,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    def to_details(self) -> ExchangeRequestDetails:
        """Convert the ORM row and loaded relationships to a read model."""
        return ExchangeRequestDetails(
            id=self.id,
            creator_user_id=self.creator_user_id,
            from_currency_code=self.from_currency.code,
            to_currency_code=self.to_currency.code,
            from_amount=self.from_amount,
            preferred_rate=self.preferred_rate,
            min_rate=self.min_rate,
            status=self.status,
            expires_at=self.expires_at,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
