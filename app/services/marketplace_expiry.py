"""Marketplace expiry service layer."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.services._shared import UnitOfWorkFactory, build_uow, utc_now
from app.services.outbox import build_outbox_event

SYSTEM_AGGREGATE_ID = UUID("00000000-0000-0000-0000-000000000000")


@dataclass(frozen=True, slots=True)
class MarketplaceExpiryResult:
    """Summary of marketplace rows expired by a maintenance pass."""

    expired_requests: int
    expired_offers: int
    reopened_requests: int
    cancelled_trades: int


class MarketplaceExpiryService:
    """Application service for time-based marketplace state transitions."""

    def __init__(self, uow_factory: UnitOfWorkFactory | None = None) -> None:
        self._uow_factory = uow_factory or build_uow

    async def expire_due_items(self) -> MarketplaceExpiryResult:
        """Expire requests/offers and cancel unfunded locked trades due by now."""
        current_time = utc_now()

        async with self._uow_factory() as uow:
            requests_to_expire = await uow.exchange_requests.list_due_for_expiry(current_time)
            expired_requests = await uow.exchange_requests.expire_due(current_time)

            offers_to_expire = await uow.exchange_offers.list_due_for_expiry(current_time)
            expired_offers = await uow.exchange_offers.expire_due(current_time)

            requests_to_reopen = await uow.exchange_requests.list_pending_without_active_offers()
            reopened_requests = await uow.exchange_requests.reopen_pending_without_active_offers(
                current_time
            )

            trades_to_cancel = await uow.trade_contracts.list_due_unfunded_details(current_time)
            cancelled_trades = await uow.trade_contracts.cancel_due_unfunded(current_time)

            for exchange_request in requests_to_expire:
                await uow.outbox_events.add(
                    build_outbox_event(
                        event_type="exchange_request.expired",
                        aggregate_type="exchange_request",
                        aggregate_id=exchange_request.id,
                        recipient_user_id=exchange_request.creator_user_id,
                        payload={"request_id": str(exchange_request.id)},
                    )
                )

            for offer in offers_to_expire:
                await uow.outbox_events.add(
                    build_outbox_event(
                        event_type="exchange_offer.expired",
                        aggregate_type="exchange_offer",
                        aggregate_id=offer.id,
                        recipient_user_id=offer.offer_user_id,
                        payload={
                            "offer_id": str(offer.id),
                            "request_id": str(offer.request_id),
                        },
                    )
                )

            for exchange_request in requests_to_reopen:
                await uow.outbox_events.add(
                    build_outbox_event(
                        event_type="exchange_request.reopened",
                        aggregate_type="exchange_request",
                        aggregate_id=exchange_request.id,
                        recipient_user_id=exchange_request.creator_user_id,
                        payload={"request_id": str(exchange_request.id)},
                    )
                )

            for trade in trades_to_cancel:
                for recipient_user_id in (trade.requester_user_id, trade.counterparty_user_id):
                    await uow.outbox_events.add(
                        build_outbox_event(
                            event_type="trade_contract.cancelled",
                            aggregate_type="trade_contract",
                            aggregate_id=trade.id,
                            recipient_user_id=recipient_user_id,
                            payload={
                                "trade_contract_id": str(trade.id),
                                "request_id": str(trade.request_id),
                                "reason": "funding_deadline_expired",
                            },
                        )
                    )

            await uow.outbox_events.add(
                build_outbox_event(
                    event_type="marketplace_expiry.completed",
                    aggregate_type="marketplace_expiry",
                    aggregate_id=SYSTEM_AGGREGATE_ID,
                    recipient_user_id=None,
                    payload={
                        "expired_requests": expired_requests,
                        "expired_offers": expired_offers,
                        "reopened_requests": reopened_requests,
                        "cancelled_trades": cancelled_trades,
                    },
                )
            )
            await uow.commit()

        return MarketplaceExpiryResult(
            expired_requests=expired_requests,
            expired_offers=expired_offers,
            reopened_requests=reopened_requests,
            cancelled_trades=cancelled_trades,
        )


def get_marketplace_expiry_service() -> MarketplaceExpiryService:
    """Build the default marketplace expiry service."""
    return MarketplaceExpiryService()
