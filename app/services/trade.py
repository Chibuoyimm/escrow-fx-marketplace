"""Trade contract service layer."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

from app.domain.entities import TradeContract, TradeContractDetails
from app.domain.enums import (
    CorridorStatus,
    ExchangeOfferStatus,
    ExchangeRequestStatus,
    TradeContractStatus,
)
from app.domain.exceptions import AuthorizationError, InvariantViolationError, NotFoundError
from app.services._shared import UnitOfWorkFactory, as_utc, build_uow, utc_now
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
    ) -> TradeContractDetails:
        """Accept an offer and create the initial trade contract."""
        current_time = utc_now()

        async with self._uow_factory() as uow:
            offer = await uow.exchange_offers.get(offer_id)
            exchange_request = await uow.exchange_requests.get(offer.request_id)

            if exchange_request.creator_user_id != requester_user_id:
                raise AuthorizationError("Only the request creator can accept an offer.")
            if exchange_request.status not in {
                ExchangeRequestStatus.REQUEST_OPEN,
                ExchangeRequestStatus.OFFER_PENDING,
            }:
                raise InvariantViolationError("This exchange request can no longer accept offers.")
            if as_utc(exchange_request.expires_at) <= current_time:
                raise InvariantViolationError("This exchange request has expired.")
            if offer.status is not ExchangeOfferStatus.ACTIVE:
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

            offers = await uow.exchange_offers.list_for_request(exchange_request.id)
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

            await uow.commit()
            return await uow.trade_contracts.get_for_participant(
                trade_contract.id,
                requester_user_id,
            )

    async def get_trade_for_participant(
        self,
        trade_id: UUID,
        participant_user_id: UUID,
    ) -> TradeContractDetails:
        """Fetch a trade contract for one of its participants."""
        async with self._uow_factory() as uow:
            return await uow.trade_contracts.get_for_participant(trade_id, participant_user_id)

    async def list_trades_for_participant(
        self,
        participant_user_id: UUID,
    ) -> list[TradeContractDetails]:
        """List trade contracts for one of their participants."""
        async with self._uow_factory() as uow:
            return await uow.trade_contracts.list_for_participant(participant_user_id)


def get_trade_service() -> TradeService:
    """Build the default trade service."""
    return TradeService()
