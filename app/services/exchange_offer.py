"""Exchange offer service layer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.domain.entities import ExchangeOffer, ExchangeOfferDetails
from app.domain.enums import ExchangeOfferStatus, ExchangeRequestStatus, KycStatus, UserStatus
from app.domain.exceptions import (
    AuthorizationError,
    ConflictError,
    InvariantViolationError,
    PreconditionFailedError,
)
from app.domain.value_objects import Rate
from app.infrastructure.database.session import AsyncSessionFactory
from app.infrastructure.database.unit_of_work import (
    AbstractUnitOfWork,
    SqlAlchemyUnitOfWork,
)

UnitOfWorkFactory = Callable[[], AbstractUnitOfWork]


def utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)


def build_uow() -> AbstractUnitOfWork:
    """Build the default unit of work."""
    return SqlAlchemyUnitOfWork(AsyncSessionFactory)


class ExchangeOfferService:
    """Application service for marketplace offers."""

    def __init__(self, uow_factory: UnitOfWorkFactory | None = None) -> None:
        self._uow_factory = uow_factory or build_uow

    async def create_offer(
        self,
        *,
        request_id: UUID,
        offer_user_id: UUID,
        offered_rate: Decimal,
    ) -> ExchangeOfferDetails:
        """Create a counterparty offer on a board-visible exchange request."""
        rate = Rate(value=offered_rate)
        current_time = utc_now()

        async with self._uow_factory() as uow:
            user = await uow.users.get(offer_user_id)
            if user.status is not UserStatus.ACTIVE:
                raise AuthorizationError("Only active users can create exchange offers.")
            if user.kyc_status is not KycStatus.VERIFIED:
                raise PreconditionFailedError("Verified KYC is required to create exchange offers.")

            exchange_request = await uow.exchange_requests.get(request_id)
            if exchange_request.creator_user_id == offer_user_id:
                raise InvariantViolationError("You cannot offer on your own exchange request.")
            if exchange_request.min_rate is not None and rate.value < exchange_request.min_rate:
                raise InvariantViolationError(
                    "Offered rate cannot be lower than the request minimum rate."
                )

            await uow.exchange_requests.get_visible_details(request_id, offer_user_id)

            if await uow.exchange_offers.has_active_offer_for_request(request_id, offer_user_id):
                raise ConflictError("You already have an active offer on that exchange request.")

            created = await uow.exchange_offers.add(
                ExchangeOffer(
                    id=uuid4(),
                    request_id=request_id,
                    offer_user_id=offer_user_id,
                    offered_rate=rate.value,
                    status=ExchangeOfferStatus.ACTIVE,
                    expires_at=exchange_request.expires_at,
                    created_at=current_time,
                    updated_at=current_time,
                )
            )

            if exchange_request.status is ExchangeRequestStatus.REQUEST_OPEN:
                await uow.exchange_requests.update(
                    replace(
                        exchange_request,
                        status=ExchangeRequestStatus.OFFER_PENDING,
                        updated_at=current_time,
                    )
                )

            await uow.commit()
            offers = await uow.exchange_offers.list_details_for_request(request_id)
            return next(offer for offer in offers if offer.id == created.id)

    async def list_offers_for_request(
        self,
        *,
        request_id: UUID,
        requester_user_id: UUID,
    ) -> list[ExchangeOfferDetails]:
        """List offers attached to a request for the request creator."""
        async with self._uow_factory() as uow:
            exchange_request = await uow.exchange_requests.get(request_id)
            if exchange_request.creator_user_id != requester_user_id:
                raise AuthorizationError(
                    "Only the request creator can view offers for this exchange request."
                )
            return await uow.exchange_offers.list_details_for_request(request_id)


def get_exchange_offer_service() -> ExchangeOfferService:
    """Build the default exchange offer service."""
    return ExchangeOfferService()
