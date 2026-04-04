"""Exchange offer ORM model."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.entities import ExchangeOffer, ExchangeOfferDetails
from app.domain.enums import ExchangeOfferStatus
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.user import UserModel

if TYPE_CHECKING:
    from app.models.exchange_request import ExchangeRequestModel


class ExchangeOfferModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Counterparty offer row."""

    __tablename__ = "exchange_offers"

    request_id: Mapped[UUID] = mapped_column(ForeignKey("exchange_requests.id", ondelete="CASCADE"))
    offer_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    offered_rate: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    status: Mapped[ExchangeOfferStatus] = mapped_column(
        Enum(ExchangeOfferStatus, native_enum=False, validate_strings=True)
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    request: Mapped[ExchangeRequestModel] = relationship(back_populates="offers")
    offer_user: Mapped[UserModel] = relationship()

    def to_domain(self) -> ExchangeOffer:
        """Convert the ORM row to a domain entity."""
        return ExchangeOffer(
            id=self.id,
            request_id=self.request_id,
            offer_user_id=self.offer_user_id,
            offered_rate=self.offered_rate,
            status=self.status,
            expires_at=self.expires_at,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    def to_details(self) -> ExchangeOfferDetails:
        """Convert the ORM row to a read model."""
        return ExchangeOfferDetails(
            id=self.id,
            request_id=self.request_id,
            offer_user_id=self.offer_user_id,
            offered_rate=self.offered_rate,
            status=self.status,
            expires_at=self.expires_at,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
