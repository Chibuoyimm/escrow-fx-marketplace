"""Exchange offer service layer."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from uuid import UUID, uuid4

from app.domain.entities import ExchangeOffer, ExchangeOfferDetails
from app.domain.enums import ExchangeOfferStatus, ExchangeRequestStatus, KycStatus, UserStatus
from app.domain.exceptions import (
    AuthorizationError,
    ConflictError,
    InvariantViolationError,
    NotFoundError,
    PreconditionFailedError,
)
from app.domain.value_objects import Rate
from app.services._shared import UnitOfWorkFactory, as_utc, build_uow, utc_now
from app.services.outbox import OutboxEventPublisher


class ExchangeOfferService:
    """Application service for marketplace offers."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory | None = None,
        outbox_publisher: OutboxEventPublisher | None = None,
    ) -> None:
        self._uow_factory = uow_factory or build_uow
        self._outbox = outbox_publisher or OutboxEventPublisher()

    @staticmethod
    def _request_status_after_active_offer_change(
        exchange_request_status: ExchangeRequestStatus,
        offers: list[ExchangeOffer],
    ) -> ExchangeRequestStatus:
        if exchange_request_status is not ExchangeRequestStatus.OFFER_PENDING:
            return exchange_request_status
        if any(offer.status is ExchangeOfferStatus.ACTIVE for offer in offers):
            return ExchangeRequestStatus.OFFER_PENDING
        return ExchangeRequestStatus.REQUEST_OPEN

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

            if as_utc(exchange_request.expires_at) <= current_time:
                raise InvariantViolationError("This exchange request has expired.")

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

            await self._outbox.exchange_offer_created(
                uow,
                offer_id=created.id,
                request_id=exchange_request.id,
                offer_user_id=offer_user_id,
                recipient_user_id=exchange_request.creator_user_id,
                offered_rate=str(created.offered_rate),
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

    async def withdraw_offer(
        self,
        *,
        offer_id: UUID,
        offer_user_id: UUID,
    ) -> ExchangeOffer:
        """Withdraw an active offer owned by the authenticated user."""
        current_time = utc_now()

        async with self._uow_factory() as uow:
            offer = await uow.exchange_offers.get(offer_id)
            if offer.offer_user_id != offer_user_id:
                raise NotFoundError(f"Exchange offer '{offer_id}' was not found.")
            if offer.status is not ExchangeOfferStatus.ACTIVE:
                raise InvariantViolationError("This offer can no longer be withdrawn.")
            if as_utc(offer.expires_at) <= current_time:
                raise InvariantViolationError("This offer has expired.")

            exchange_request = await uow.exchange_requests.get(offer.request_id)
            if exchange_request.status not in {
                ExchangeRequestStatus.REQUEST_OPEN,
                ExchangeRequestStatus.OFFER_PENDING,
            }:
                raise InvariantViolationError("This offer can no longer be withdrawn.")

            updated_offer = await uow.exchange_offers.update(
                replace(
                    offer,
                    status=ExchangeOfferStatus.WITHDRAWN,
                    updated_at=current_time,
                )
            )

            offers = await uow.exchange_offers.list_for_request(exchange_request.id)
            request_status = self._request_status_after_active_offer_change(
                exchange_request.status,
                offers,
            )
            if request_status is not exchange_request.status:
                await uow.exchange_requests.update(
                    replace(
                        exchange_request,
                        status=request_status,
                        updated_at=current_time,
                    )
                )

            await self._outbox.exchange_offer_withdrawn(
                uow,
                offer_id=offer.id,
                request_id=exchange_request.id,
                offer_user_id=offer_user_id,
                recipient_user_id=exchange_request.creator_user_id,
            )
            await uow.commit()
            return updated_offer

    async def reject_offer(
        self,
        *,
        offer_id: UUID,
        requester_user_id: UUID,
    ) -> ExchangeOffer:
        """Reject an active offer as the request creator."""
        current_time = utc_now()

        async with self._uow_factory() as uow:
            offer = await uow.exchange_offers.get(offer_id)
            if offer.status is not ExchangeOfferStatus.ACTIVE:
                raise InvariantViolationError("This offer can no longer be rejected.")
            if as_utc(offer.expires_at) <= current_time:
                raise InvariantViolationError("This offer has expired.")

            exchange_request = await uow.exchange_requests.get(offer.request_id)
            if exchange_request.creator_user_id != requester_user_id:
                raise AuthorizationError("Only the request creator can reject this offer.")
            if exchange_request.status not in {
                ExchangeRequestStatus.REQUEST_OPEN,
                ExchangeRequestStatus.OFFER_PENDING,
            }:
                raise InvariantViolationError("This offer can no longer be rejected.")
            if as_utc(exchange_request.expires_at) <= current_time:
                raise InvariantViolationError("This exchange request has expired.")

            updated_offer = await uow.exchange_offers.update(
                replace(
                    offer,
                    status=ExchangeOfferStatus.REJECTED,
                    updated_at=current_time,
                )
            )

            offers = await uow.exchange_offers.list_for_request(exchange_request.id)
            request_status = self._request_status_after_active_offer_change(
                exchange_request.status,
                offers,
            )
            if request_status is not exchange_request.status:
                await uow.exchange_requests.update(
                    replace(
                        exchange_request,
                        status=request_status,
                        updated_at=current_time,
                    )
                )

            await self._outbox.exchange_offer_rejected(
                uow,
                offer_id=offer.id,
                request_id=exchange_request.id,
                recipient_user_id=offer.offer_user_id,
                requester_user_id=requester_user_id,
            )
            await uow.commit()
            return updated_offer


def get_exchange_offer_service() -> ExchangeOfferService:
    """Build the default exchange offer service."""
    return ExchangeOfferService()
