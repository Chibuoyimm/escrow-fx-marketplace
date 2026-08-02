"""Exchange offer service layer."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.domain.entities import ExchangeOffer, ExchangeOfferDetails, ExchangeRequest, User
from app.domain.enums import ExchangeOfferStatus, ExchangeRequestStatus, KycStatus, UserStatus
from app.domain.exceptions import (
    AuthorizationError,
    ConflictError,
    InvariantViolationError,
    NotFoundError,
    PreconditionFailedError,
)
from app.domain.lifecycle import offer_is_active, request_can_accept_offers
from app.domain.value_objects import Rate
from app.infrastructure.database.unit_of_work import AbstractUnitOfWork
from app.infrastructure.idempotency import (
    IdempotencyReplay,
    IdempotencyRequest,
    claim_idempotency,
    complete_idempotency,
)
from app.infrastructure.pagination import (
    decode_cursor,
    encode_next_cursor,
    normalize_date_range,
    validate_range,
)
from app.services._shared import (
    UnitOfWorkFactory,
    as_utc,
    build_uow,
    format_decimal,
    lock_users_in_order,
    utc_now,
)
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

    @staticmethod
    async def _lock_existing_offer_context(
        uow: AbstractUnitOfWork,
        offer_id: UUID,
    ) -> tuple[dict[UUID, User], ExchangeRequest, ExchangeOffer]:
        initial_offer = await uow.exchange_offers.get(offer_id)
        initial_request = await uow.exchange_requests.get(initial_offer.request_id)
        users = await lock_users_in_order(
            uow,
            (initial_request.creator_user_id, initial_offer.offer_user_id),
        )
        exchange_request = await uow.exchange_requests.get_for_update(initial_request.id)
        offer = await uow.exchange_offers.get_for_update(offer_id)
        if (
            exchange_request.creator_user_id != initial_request.creator_user_id
            or offer.request_id != initial_request.id
            or offer.offer_user_id != initial_offer.offer_user_id
        ):
            raise ConflictError("Exchange offer relationships changed; retry the operation.")
        return users, exchange_request, offer

    async def create_offer(
        self,
        *,
        request_id: UUID,
        offer_user_id: UUID,
        offered_rate: Decimal,
        idempotency: IdempotencyRequest | None = None,
    ) -> ExchangeOfferDetails | IdempotencyReplay:
        """Create a counterparty offer on a board-visible exchange request."""
        rate = Rate(value=offered_rate)
        current_time = utc_now()

        async with self._uow_factory() as uow:
            claim = await claim_idempotency(uow, idempotency, now=current_time)
            if isinstance(claim, IdempotencyReplay):
                return claim
            initial_request = await uow.exchange_requests.get(request_id)
            if initial_request.creator_user_id == offer_user_id:
                raise InvariantViolationError("You cannot offer on your own exchange request.")

            users = await lock_users_in_order(
                uow,
                (offer_user_id, initial_request.creator_user_id),
            )
            exchange_request = await uow.exchange_requests.get_for_update(request_id)
            if exchange_request.creator_user_id != initial_request.creator_user_id:
                raise ConflictError("Exchange request participants changed; retry the operation.")

            user = users[offer_user_id]
            if user.status is not UserStatus.ACTIVE:
                raise AuthorizationError("Only active users can create exchange offers.")
            if user.kyc_status is not KycStatus.VERIFIED:
                raise PreconditionFailedError("Verified KYC is required to create exchange offers.")

            request_creator = users[exchange_request.creator_user_id]
            if request_creator.status is not UserStatus.ACTIVE:
                raise PreconditionFailedError(
                    "The request creator is not active, so this request cannot receive offers."
                )
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
                offered_rate=format_decimal(created.offered_rate),
            )
            offers = await uow.exchange_offers.list_details_for_request(request_id)
            response = next(offer for offer in offers if offer.id == created.id)
            await complete_idempotency(
                uow,
                claim,
                response_status_code=201,
                response=response,
                now=current_time,
            )
            await uow.commit()
            return response

    async def list_offers_for_request_page(
        self,
        *,
        request_id: UUID,
        requester_user_id: UUID,
        cursor: str | None = None,
        limit: int = 50,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[list[ExchangeOfferDetails], str | None]:
        """List request offers for their creator with cursor pagination."""
        created_from, created_to = normalize_date_range(created_from, created_to)
        async with self._uow_factory() as uow:
            exchange_request = await uow.exchange_requests.get(request_id)
            if exchange_request.creator_user_id != requester_user_id:
                raise AuthorizationError(
                    "Only the request creator can view offers for this exchange request."
                )
            items, next_position = await uow.exchange_offers.list_details_for_request_page(
                request_id,
                cursor=decode_cursor(cursor),
                limit=limit,
                created_from=created_from,
                created_to=created_to,
            )
            return items, encode_next_cursor(next_position)

    async def list_my_offers_page(
        self,
        *,
        offer_user_id: UUID,
        cursor: str | None = None,
        limit: int = 50,
        statuses: tuple[ExchangeOfferStatus, ...] | None = None,
        min_offered_rate: Decimal | None = None,
        max_offered_rate: Decimal | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[list[ExchangeOfferDetails], str | None]:
        """List offers owned by the authenticated user with pagination."""
        validate_range(min_offered_rate, max_offered_rate, "offered rate")
        created_from, created_to = normalize_date_range(created_from, created_to)
        async with self._uow_factory() as uow:
            items, next_position = await uow.exchange_offers.list_details_for_user_page(
                offer_user_id,
                cursor=decode_cursor(cursor),
                limit=limit,
                statuses=statuses,
                min_offered_rate=min_offered_rate,
                max_offered_rate=max_offered_rate,
                created_from=created_from,
                created_to=created_to,
            )
            return items, encode_next_cursor(next_position)

    async def get_visible_offer(
        self, *, offer_id: UUID, viewer_user_id: UUID
    ) -> ExchangeOfferDetails:
        """Fetch an offer for its owner or the request creator."""
        async with self._uow_factory() as uow:
            return await uow.exchange_offers.get_visible_details(offer_id, viewer_user_id)

    async def update_offer(
        self,
        *,
        offer_id: UUID,
        offer_user_id: UUID,
        offered_rate: Decimal,
    ) -> ExchangeOfferDetails:
        """Update an active offer before the request is locked."""
        current_time = utc_now()
        rate = Rate(value=offered_rate)
        async with self._uow_factory() as uow:
            users, exchange_request, offer = await self._lock_existing_offer_context(uow, offer_id)
            if offer.offer_user_id != offer_user_id:
                raise NotFoundError(f"Exchange offer '{offer_id}' was not found.")
            user = users[offer.offer_user_id]
            if user.status is not UserStatus.ACTIVE:
                raise AuthorizationError("Only active users can edit exchange offers.")
            if user.kyc_status is not KycStatus.VERIFIED:
                raise PreconditionFailedError("Verified KYC is required to edit exchange offers.")
            if not offer_is_active(offer.status):
                raise InvariantViolationError("This offer can no longer be edited.")
            if as_utc(offer.expires_at) <= current_time:
                raise InvariantViolationError("This offer has expired.")
            if not request_can_accept_offers(exchange_request.status):
                raise InvariantViolationError("This offer can no longer be edited.")
            if as_utc(exchange_request.expires_at) <= current_time:
                raise InvariantViolationError("This exchange request has expired.")
            if exchange_request.min_rate is not None and rate.value < exchange_request.min_rate:
                raise InvariantViolationError(
                    "Offered rate cannot be lower than the request minimum rate."
                )
            if rate.value == offer.offered_rate:
                return await uow.exchange_offers.get_visible_details(offer_id, offer_user_id)
            updated = replace(offer, offered_rate=rate.value, updated_at=current_time)
            await uow.exchange_offers.update(updated)
            await self._outbox.exchange_offer_updated(
                uow,
                offer_id=offer.id,
                request_id=exchange_request.id,
                offer_user_id=offer_user_id,
                recipient_user_id=exchange_request.creator_user_id,
                offered_rate=format_decimal(rate.value),
            )
            await uow.commit()
            return await uow.exchange_offers.get_visible_details(offer_id, offer_user_id)

    async def withdraw_offer(
        self,
        *,
        offer_id: UUID,
        offer_user_id: UUID,
        idempotency: IdempotencyRequest | None = None,
    ) -> ExchangeOfferDetails | IdempotencyReplay:
        """Withdraw an active offer owned by the authenticated user."""
        current_time = utc_now()

        async with self._uow_factory() as uow:
            claim = await claim_idempotency(uow, idempotency, now=current_time)
            if isinstance(claim, IdempotencyReplay):
                return claim
            _, exchange_request, offer = await self._lock_existing_offer_context(uow, offer_id)
            if offer.offer_user_id != offer_user_id:
                raise NotFoundError(f"Exchange offer '{offer_id}' was not found.")
            if not offer_is_active(offer.status):
                raise InvariantViolationError("This offer can no longer be withdrawn.")
            if as_utc(offer.expires_at) <= current_time:
                raise InvariantViolationError("This offer has expired.")

            if exchange_request.status not in {
                ExchangeRequestStatus.REQUEST_OPEN,
                ExchangeRequestStatus.OFFER_PENDING,
            }:
                raise InvariantViolationError("This offer can no longer be withdrawn.")

            await uow.exchange_offers.update(
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
            response = await uow.exchange_offers.get_visible_details(offer_id, offer_user_id)
            await complete_idempotency(
                uow,
                claim,
                response_status_code=200,
                response=response,
                now=current_time,
            )
            await uow.commit()
            return response

    async def reject_offer(
        self,
        *,
        offer_id: UUID,
        requester_user_id: UUID,
        idempotency: IdempotencyRequest | None = None,
    ) -> ExchangeOfferDetails | IdempotencyReplay:
        """Reject an active offer as the request creator."""
        current_time = utc_now()

        async with self._uow_factory() as uow:
            claim = await claim_idempotency(uow, idempotency, now=current_time)
            if isinstance(claim, IdempotencyReplay):
                return claim
            _, exchange_request, offer = await self._lock_existing_offer_context(uow, offer_id)
            if not offer_is_active(offer.status):
                raise InvariantViolationError("This offer can no longer be rejected.")
            if as_utc(offer.expires_at) <= current_time:
                raise InvariantViolationError("This offer has expired.")

            if exchange_request.creator_user_id != requester_user_id:
                raise AuthorizationError("Only the request creator can reject this offer.")
            if exchange_request.status not in {
                ExchangeRequestStatus.REQUEST_OPEN,
                ExchangeRequestStatus.OFFER_PENDING,
            }:
                raise InvariantViolationError("This offer can no longer be rejected.")
            if as_utc(exchange_request.expires_at) <= current_time:
                raise InvariantViolationError("This exchange request has expired.")

            await uow.exchange_offers.update(
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
            response = await uow.exchange_offers.get_visible_details(offer_id, requester_user_id)
            await complete_idempotency(
                uow,
                claim,
                response_status_code=200,
                response=response,
                now=current_time,
            )
            await uow.commit()
            return response


def get_exchange_offer_service() -> ExchangeOfferService:
    """Build the default exchange offer service."""
    return ExchangeOfferService()
