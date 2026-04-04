"""Trade contract ORM model."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.entities import TradeContract, TradeContractDetails
from app.domain.enums import TradeContractStatus
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.exchange_offer import ExchangeOfferModel
    from app.models.exchange_request import ExchangeRequestModel


class TradeContractModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Locked trade contract row."""

    __tablename__ = "trade_contracts"

    request_id: Mapped[UUID] = mapped_column(
        ForeignKey("exchange_requests.id", ondelete="RESTRICT"),
        unique=True,
    )
    accepted_offer_id: Mapped[UUID] = mapped_column(
        ForeignKey("exchange_offers.id", ondelete="RESTRICT"),
        unique=True,
    )
    agreed_rate: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    reference_rate_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    from_amount: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    to_amount: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    funding_deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[TradeContractStatus] = mapped_column(
        Enum(TradeContractStatus, native_enum=False, validate_strings=True)
    )

    request: Mapped[ExchangeRequestModel] = relationship(back_populates="trade_contract")
    accepted_offer: Mapped[ExchangeOfferModel] = relationship(
        back_populates="accepted_trade_contract"
    )

    def to_domain(self) -> TradeContract:
        """Convert the ORM row to a domain entity."""
        return TradeContract(
            id=self.id,
            request_id=self.request_id,
            accepted_offer_id=self.accepted_offer_id,
            agreed_rate=self.agreed_rate,
            reference_rate_snapshot=self.reference_rate_snapshot,
            from_amount=self.from_amount,
            to_amount=self.to_amount,
            funding_deadline_at=self.funding_deadline_at,
            status=self.status,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    def to_details(self) -> TradeContractDetails:
        """Convert the ORM row to a participant-facing read model."""
        return TradeContractDetails(
            id=self.id,
            request_id=self.request_id,
            accepted_offer_id=self.accepted_offer_id,
            requester_user_id=self.request.creator_user_id,
            counterparty_user_id=self.accepted_offer.offer_user_id,
            from_currency_code=self.request.from_currency.code,
            to_currency_code=self.request.to_currency.code,
            agreed_rate=self.agreed_rate,
            reference_rate_snapshot=self.reference_rate_snapshot,
            from_amount=self.from_amount,
            to_amount=self.to_amount,
            funding_deadline_at=self.funding_deadline_at,
            status=self.status,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
