from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import (
    CurrencyStatus,
    ExchangeOfferStatus,
    ExchangeRequestStatus,
    TradeContractStatus,
)
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from app.services.marketplace_expiry import MarketplaceExpiryService
from tests.conftest import (
    build_currency,
    build_exchange_offer,
    build_exchange_request,
    build_trade_contract,
    build_user,
)

pytestmark = pytest.mark.anyio


async def seed_expiry_scenario(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, UUID]:
    now = datetime.now(UTC)

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        creator = await uow.users.add(build_user(email="expiry-creator@example.com"))
        offerer = await uow.users.add(build_user(email="expiry-offerer@example.com"))
        from_currency = await uow.currencies.add(
            build_currency(code="USD", status=CurrencyStatus.ACTIVE)
        )
        to_currency = await uow.currencies.add(
            build_currency(code="NGN", status=CurrencyStatus.ACTIVE)
        )

        expired_request = await uow.exchange_requests.add(
            build_exchange_request(
                creator_user_id=creator.id,
                from_currency_id=from_currency.id,
                to_currency_id=to_currency.id,
                status=ExchangeRequestStatus.REQUEST_OPEN,
                expires_at=now - timedelta(minutes=1),
            )
        )
        offer_on_expired_request = await uow.exchange_offers.add(
            build_exchange_offer(
                request_id=expired_request.id,
                offer_user_id=offerer.id,
                status=ExchangeOfferStatus.ACTIVE,
                expires_at=now + timedelta(minutes=30),
            )
        )

        pending_request = await uow.exchange_requests.add(
            build_exchange_request(
                creator_user_id=creator.id,
                from_currency_id=from_currency.id,
                to_currency_id=to_currency.id,
                status=ExchangeRequestStatus.OFFER_PENDING,
                expires_at=now + timedelta(minutes=30),
            )
        )
        expired_offer = await uow.exchange_offers.add(
            build_exchange_offer(
                request_id=pending_request.id,
                offer_user_id=offerer.id,
                status=ExchangeOfferStatus.ACTIVE,
                expires_at=now - timedelta(minutes=1),
            )
        )

        locked_request = await uow.exchange_requests.add(
            build_exchange_request(
                creator_user_id=creator.id,
                from_currency_id=from_currency.id,
                to_currency_id=to_currency.id,
                status=ExchangeRequestStatus.TERMS_LOCKED,
                expires_at=now + timedelta(minutes=30),
            )
        )
        accepted_offer = await uow.exchange_offers.add(
            build_exchange_offer(
                request_id=locked_request.id,
                offer_user_id=offerer.id,
                status=ExchangeOfferStatus.ACCEPTED,
                expires_at=now + timedelta(minutes=30),
            )
        )
        due_trade = await uow.trade_contracts.add(
            build_trade_contract(
                request_id=locked_request.id,
                accepted_offer_id=accepted_offer.id,
                status=TradeContractStatus.TERMS_LOCKED,
                funding_deadline_at=now - timedelta(minutes=1),
            )
        )

        await uow.commit()

    return {
        "expired_request_id": expired_request.id,
        "offer_on_expired_request_id": offer_on_expired_request.id,
        "pending_request_id": pending_request.id,
        "expired_offer_id": expired_offer.id,
        "due_trade_id": due_trade.id,
    }


async def test_marketplace_expiry_transitions_due_items(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await seed_expiry_scenario(session_factory)
    service = MarketplaceExpiryService(uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory))

    result = await service.expire_due_items()

    assert result.expired_requests == 1
    assert result.expired_offers == 2
    assert result.reopened_requests == 1
    assert result.cancelled_trades == 1

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        expired_request = await uow.exchange_requests.get(seeded["expired_request_id"])
        offer_on_expired_request = await uow.exchange_offers.get(
            seeded["offer_on_expired_request_id"]
        )
        pending_request = await uow.exchange_requests.get(seeded["pending_request_id"])
        expired_offer = await uow.exchange_offers.get(seeded["expired_offer_id"])
        due_trade = await uow.trade_contracts.get(seeded["due_trade_id"])

    assert expired_request.status is ExchangeRequestStatus.EXPIRED
    assert offer_on_expired_request.status is ExchangeOfferStatus.EXPIRED
    assert pending_request.status is ExchangeRequestStatus.REQUEST_OPEN
    assert expired_offer.status is ExchangeOfferStatus.EXPIRED
    assert due_trade.status is TradeContractStatus.CANCELLED


async def test_marketplace_expiry_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_expiry_scenario(session_factory)
    service = MarketplaceExpiryService(uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory))

    await service.expire_due_items()
    result = await service.expire_due_items()

    assert result.expired_requests == 0
    assert result.expired_offers == 0
    assert result.reopened_requests == 0
    assert result.cancelled_trades == 0
