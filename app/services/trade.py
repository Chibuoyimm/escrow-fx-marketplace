"""Trade contract service layer."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from app.domain.entities import TradeContract, TradeContractDetails
from app.domain.enums import (
    CorridorStatus,
    ExchangeOfferStatus,
    ExchangeRequestStatus,
    TradeContractStatus,
    UserStatus,
)
from app.domain.exceptions import (
    AuthorizationError,
    ConflictError,
    InvariantViolationError,
    NotFoundError,
    PreconditionFailedError,
)
from app.domain.lifecycle import offer_is_active, request_can_accept_offers
from app.infrastructure.idempotency import (
    IdempotencyReplay,
    IdempotencyRequest,
    claim_idempotency,
    complete_idempotency,
)
from app.infrastructure.pagination import decode_cursor, encode_next_cursor, normalize_date_range
from app.services._shared import (
    UnitOfWorkFactory,
    as_utc,
    build_uow,
    lock_users_in_order,
    utc_now,
)
from app.services.outbox import OutboxEventPublisher


class TradeService:
    """Application service for trade locking and participant reads."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory | None = None,
        outbox_publisher: OutboxEventPublisher | None = None,
    ) -> None:
        self._uow_factory = uow_factory or build_uow
        self._outbox = outbox_publisher or OutboxEventPublisher()

    async def accept_offer(
        self,
        *,
        offer_id: UUID,
        requester_user_id: UUID,
        idempotency: IdempotencyRequest | None = None,
    ) -> TradeContractDetails | IdempotencyReplay:
        """Accept an offer and create the initial trade contract."""
        current_time = utc_now()

        async with self._uow_factory() as uow:
            claim = await claim_idempotency(uow, idempotency, now=current_time)
            if isinstance(claim, IdempotencyReplay):
                return claim
            initial_offer = await uow.exchange_offers.get(offer_id)
            initial_request = await uow.exchange_requests.get(initial_offer.request_id)
            if initial_request.creator_user_id != requester_user_id:
                raise AuthorizationError("Only the request creator can accept an offer.")
            initial_offers = await uow.exchange_offers.list_for_request(initial_request.id)
            initial_offer_owners = {
                candidate.id: candidate.offer_user_id for candidate in initial_offers
            }
            if offer_id not in initial_offer_owners:
                raise ConflictError("Exchange request offers changed; retry the operation.")
            users = await lock_users_in_order(
                uow,
                (initial_request.creator_user_id, *initial_offer_owners.values()),
            )
            exchange_request = await uow.exchange_requests.get_for_update(initial_request.id)
            if exchange_request.creator_user_id != initial_request.creator_user_id:
                raise ConflictError("Exchange request participants changed; retry the operation.")

            current_offers = await uow.exchange_offers.list_for_request(exchange_request.id)
            current_offer_owners = {
                candidate.id: candidate.offer_user_id for candidate in current_offers
            }
            if current_offer_owners != initial_offer_owners:
                raise ConflictError("Exchange request offers changed; retry the operation.")

            offers = []
            for current_offer in sorted(current_offers, key=lambda candidate: candidate.id.int):
                locked_offer = await uow.exchange_offers.get_for_update(current_offer.id)
                if (
                    locked_offer.request_id != exchange_request.id
                    or locked_offer.offer_user_id != initial_offer_owners[locked_offer.id]
                ):
                    raise ConflictError(
                        "Exchange offer relationships changed; retry the operation."
                    )
                offers.append(locked_offer)
            offer = next(candidate for candidate in offers if candidate.id == offer_id)

            if users[requester_user_id].status is not UserStatus.ACTIVE:
                raise AuthorizationError("Only active users can accept exchange offers.")
            if users[offer.offer_user_id].status is not UserStatus.ACTIVE:
                raise PreconditionFailedError(
                    "The offer owner is not active, so this offer cannot be accepted."
                )

            if not request_can_accept_offers(exchange_request.status):
                raise InvariantViolationError("This exchange request can no longer accept offers.")
            if as_utc(exchange_request.expires_at) <= current_time:
                raise InvariantViolationError("This exchange request has expired.")
            if not offer_is_active(offer.status):
                raise InvariantViolationError("This offer is no longer active.")
            if as_utc(offer.expires_at) <= current_time:
                raise InvariantViolationError("This offer has expired.")

            try:
                corridor = await uow.corridors.get_by_currency_pair(
                    exchange_request.from_currency_id,
                    exchange_request.to_currency_id,
                )
            except NotFoundError as exc:
                raise InvariantViolationError(
                    "The corridor required to lock this trade is no longer available."
                ) from exc
            if corridor.status is not CorridorStatus.ACTIVE:
                raise InvariantViolationError(
                    "The corridor required to lock this trade is no longer available."
                )

            trade_contract = await uow.trade_contracts.add(
                TradeContract(
                    id=uuid4(),
                    request_id=exchange_request.id,
                    accepted_offer_id=offer.id,
                    agreed_rate=offer.offered_rate,
                    reference_rate_snapshot=None,
                    from_amount=exchange_request.from_amount,
                    to_amount=exchange_request.from_amount * offer.offered_rate,
                    funding_deadline_at=current_time
                    + timedelta(minutes=corridor.funding_sla_minutes),
                    status=TradeContractStatus.TERMS_LOCKED,
                    created_at=current_time,
                    updated_at=current_time,
                )
            )

            await uow.exchange_requests.update(
                replace(
                    exchange_request,
                    status=ExchangeRequestStatus.TERMS_LOCKED,
                    updated_at=current_time,
                )
            )

            for existing_offer in offers:
                if existing_offer.status is not ExchangeOfferStatus.ACTIVE:
                    continue
                new_status = (
                    ExchangeOfferStatus.ACCEPTED
                    if existing_offer.id == offer.id
                    else ExchangeOfferStatus.REJECTED
                )
                await uow.exchange_offers.update(
                    replace(
                        existing_offer,
                        status=new_status,
                        updated_at=current_time,
                    )
                )
                if new_status is ExchangeOfferStatus.ACCEPTED:
                    await self._outbox.exchange_offer_accepted(
                        uow,
                        offer_id=existing_offer.id,
                        request_id=exchange_request.id,
                        offer_user_id=existing_offer.offer_user_id,
                        trade_contract_id=trade_contract.id,
                    )
                else:
                    await self._outbox.exchange_offer_rejected(
                        uow,
                        offer_id=existing_offer.id,
                        request_id=exchange_request.id,
                        recipient_user_id=existing_offer.offer_user_id,
                        reason="competing_offer_accepted",
                    )

            for recipient_user_id in (exchange_request.creator_user_id, offer.offer_user_id):
                await self._outbox.trade_contract_locked(
                    uow,
                    trade_contract_id=trade_contract.id,
                    request_id=exchange_request.id,
                    accepted_offer_id=offer.id,
                    recipient_user_id=recipient_user_id,
                )

            response = await uow.trade_contracts.get_for_participant(
                trade_contract.id,
                requester_user_id,
            )
            await complete_idempotency(
                uow,
                claim,
                response_status_code=200,
                response=response,
                now=current_time,
            )
            await uow.commit()
            return response

    async def get_trade_for_participant(
        self,
        trade_id: UUID,
        participant_user_id: UUID,
    ) -> TradeContractDetails:
        """Fetch a trade contract for one of its participants."""
        async with self._uow_factory() as uow:
            return await uow.trade_contracts.get_for_participant(trade_id, participant_user_id)

    async def list_trades_for_participant_page(
        self,
        participant_user_id: UUID,
        *,
        cursor: str | None = None,
        limit: int = 50,
        statuses: tuple[TradeContractStatus, ...] | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[list[TradeContractDetails], str | None]:
        """List participant trades using stable cursor pagination."""
        created_from, created_to = normalize_date_range(created_from, created_to)
        async with self._uow_factory() as uow:
            items, next_position = await uow.trade_contracts.list_for_participant_page(
                participant_user_id,
                cursor=decode_cursor(cursor),
                limit=limit,
                statuses=statuses,
                created_from=created_from,
                created_to=created_to,
            )
            return items, encode_next_cursor(next_position)


def get_trade_service() -> TradeService:
    """Build the default trade service."""
    return TradeService()
