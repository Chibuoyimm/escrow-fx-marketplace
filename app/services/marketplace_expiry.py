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
            expired_requests = await uow.exchange_requests.expire_due(current_time)
            expired_offers = await uow.exchange_offers.expire_due(current_time)
            reopened_requests = await uow.exchange_requests.reopen_pending_without_active_offers(
                current_time
            )
            cancelled_trades = await uow.trade_contracts.cancel_due_unfunded(current_time)
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
