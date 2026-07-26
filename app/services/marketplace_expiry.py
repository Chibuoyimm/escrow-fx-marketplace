"""Marketplace expiry service layer."""

from __future__ import annotations

from dataclasses import dataclass

from app.services._shared import UnitOfWorkFactory, build_uow, utc_now
from app.services.outbox import OutboxEventPublisher


@dataclass(frozen=True, slots=True)
class MarketplaceExpiryResult:
    """Summary of marketplace rows expired by a maintenance pass."""

    expired_requests: int
    expired_offers: int
    reopened_requests: int
    cancelled_trades: int


class MarketplaceExpiryService:
    """Application service for time-based marketplace state transitions."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory | None = None,
        outbox_publisher: OutboxEventPublisher | None = None,
    ) -> None:
        self._uow_factory = uow_factory or build_uow
        self._outbox = outbox_publisher or OutboxEventPublisher()

    async def expire_due_items(self) -> MarketplaceExpiryResult:
        """Expire requests/offers and cancel unfunded locked trades due by now."""
        current_time = utc_now()

        async with self._uow_factory() as uow:
            expired_request_rows = await uow.exchange_requests.expire_due(current_time)
            expired_offer_rows = await uow.exchange_offers.expire_due(current_time)
            reopened_request_rows = (
                await uow.exchange_requests.reopen_pending_without_active_offers(current_time)
            )
            cancelled_trade_rows = await uow.trade_contracts.cancel_due_unfunded(current_time)
            expired_requests = len(expired_request_rows)
            expired_offers = len(expired_offer_rows)
            reopened_requests = len(reopened_request_rows)
            cancelled_trades = len(cancelled_trade_rows)

            for exchange_request in expired_request_rows:
                await self._outbox.exchange_request_expired(
                    uow,
                    request_id=exchange_request.id,
                    creator_user_id=exchange_request.creator_user_id,
                )

            for offer in expired_offer_rows:
                await self._outbox.exchange_offer_expired(
                    uow,
                    offer_id=offer.id,
                    request_id=offer.request_id,
                    offer_user_id=offer.offer_user_id,
                )

            for exchange_request in reopened_request_rows:
                await self._outbox.exchange_request_reopened(
                    uow,
                    request_id=exchange_request.id,
                    creator_user_id=exchange_request.creator_user_id,
                )

            for trade in cancelled_trade_rows:
                for recipient_user_id in (trade.requester_user_id, trade.counterparty_user_id):
                    await self._outbox.trade_contract_cancelled(
                        uow,
                        trade_contract_id=trade.id,
                        request_id=trade.request_id,
                        recipient_user_id=recipient_user_id,
                        reason="funding_deadline_expired",
                    )

            await self._outbox.marketplace_expiry_completed(
                uow,
                expired_requests=expired_requests,
                expired_offers=expired_offers,
                reopened_requests=reopened_requests,
                cancelled_trades=cancelled_trades,
            )
            await uow.commit()

        return MarketplaceExpiryResult(
            expired_requests=len(expired_request_rows),
            expired_offers=len(expired_offer_rows),
            reopened_requests=len(reopened_request_rows),
            cancelled_trades=len(cancelled_trade_rows),
        )


def get_marketplace_expiry_service() -> MarketplaceExpiryService:
    """Build the default marketplace expiry service."""
    return MarketplaceExpiryService()
