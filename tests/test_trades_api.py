from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import (
    CorridorStatus,
    CurrencyStatus,
    ExchangeOfferStatus,
    ExchangeRequestStatus,
    KycStatus,
    UserStatus,
)
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.security import SecurityService
from app.main import app
from app.services.auth import AuthService, get_auth_service
from app.services.exchange_offer import ExchangeOfferService, get_exchange_offer_service
from app.services.exchange_request import ExchangeRequestService, get_exchange_request_service
from app.services.trade import TradeService, get_trade_service
from tests.conftest import (
    build_corridor,
    build_currency,
    build_exchange_offer,
    build_exchange_request,
    build_trade_contract,
    build_user,
)

pytestmark = pytest.mark.anyio

PASSWORD = "ChangeMe123!"


@pytest.fixture
def auth_service(session_factory: async_sessionmaker[AsyncSession]) -> AuthService:
    return AuthService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        security=SecurityService(),
    )


@pytest.fixture
def exchange_request_service(
    session_factory: async_sessionmaker[AsyncSession],
) -> ExchangeRequestService:
    return ExchangeRequestService(uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory))


@pytest.fixture
def exchange_offer_service(
    session_factory: async_sessionmaker[AsyncSession],
) -> ExchangeOfferService:
    return ExchangeOfferService(uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory))


@pytest.fixture
def trade_service(session_factory: async_sessionmaker[AsyncSession]) -> TradeService:
    return TradeService(uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory))


@pytest.fixture
async def client(
    auth_service: AuthService,
    exchange_request_service: ExchangeRequestService,
    exchange_offer_service: ExchangeOfferService,
    trade_service: TradeService,
) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    app.dependency_overrides[get_exchange_request_service] = lambda: exchange_request_service
    app.dependency_overrides[get_exchange_offer_service] = lambda: exchange_offer_service
    app.dependency_overrides[get_trade_service] = lambda: trade_service
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as test_client:
        yield test_client
    app.dependency_overrides.clear()


async def create_user_and_token(
    session_factory: async_sessionmaker[AsyncSession],
    auth_service: AuthService,
    *,
    email: str,
    kyc_status: KycStatus = KycStatus.VERIFIED,
    status: UserStatus = UserStatus.ACTIVE,
) -> tuple[UUID, dict[str, str]]:
    security = SecurityService()
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        user = await uow.users.add(
            build_user(
                email=email,
                password_hash=security.hash_password(PASSWORD),
                kyc_status=kyc_status,
                status=status,
            )
        )
        await uow.commit()

    login = await auth_service.login_user(email=email, password=PASSWORD)
    return user.id, {"Authorization": f"Bearer {login.access_token}"}


async def login_headers(auth_service: AuthService, *, email: str) -> dict[str, str]:
    login = await auth_service.login_user(email=email, password=PASSWORD)
    return {"Authorization": f"Bearer {login.access_token}"}


async def seed_request_with_offer(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    email_prefix: str = "",
    from_code: str = "USD",
    to_code: str = "NGN",
    request_status: ExchangeRequestStatus = ExchangeRequestStatus.OFFER_PENDING,
    offer_status: ExchangeOfferStatus = ExchangeOfferStatus.ACTIVE,
    request_expires_at: datetime | None = None,
    offer_expires_at: datetime | None = None,
) -> dict[str, UUID]:
    security = SecurityService()
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        requester = await uow.users.add(
            build_user(
                email=f"{email_prefix}requester@example.com",
                password_hash=security.hash_password(PASSWORD),
            )
        )
        counterparty = await uow.users.add(
            build_user(
                email=f"{email_prefix}counterparty@example.com",
                password_hash=security.hash_password(PASSWORD),
            )
        )
        competitor = await uow.users.add(
            build_user(
                email=f"{email_prefix}competitor@example.com",
                password_hash=security.hash_password(PASSWORD),
            )
        )
        outsider = await uow.users.add(
            build_user(
                email=f"{email_prefix}outsider@example.com",
                password_hash=security.hash_password(PASSWORD),
            )
        )
        usd = await uow.currencies.add(build_currency(code=from_code, status=CurrencyStatus.ACTIVE))
        ngn = await uow.currencies.add(build_currency(code=to_code, status=CurrencyStatus.ACTIVE))
        await uow.corridors.add(
            build_corridor(
                from_currency_id=usd.id,
                to_currency_id=ngn.id,
                status=CorridorStatus.ACTIVE,
            )
        )
        exchange_request = await uow.exchange_requests.add(
            build_exchange_request(
                creator_user_id=requester.id,
                from_currency_id=usd.id,
                to_currency_id=ngn.id,
                status=request_status,
                expires_at=request_expires_at or (datetime.now(UTC) + timedelta(hours=1)),
            )
        )
        accepted_candidate = await uow.exchange_offers.add(
            build_exchange_offer(
                request_id=exchange_request.id,
                offer_user_id=counterparty.id,
                status=offer_status,
                offer_id=None,
                expires_at=offer_expires_at or (datetime.now(UTC) + timedelta(hours=1)),
            )
        )
        competing_offer = await uow.exchange_offers.add(
            build_exchange_offer(
                request_id=exchange_request.id,
                offer_user_id=competitor.id,
                offered_rate=accepted_candidate.offered_rate - 1,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await uow.commit()

    return {
        "requester_id": requester.id,
        "counterparty_id": counterparty.id,
        "competitor_id": competitor.id,
        "outsider_id": outsider.id,
        "request_id": exchange_request.id,
        "offer_id": accepted_candidate.id,
        "competing_offer_id": competing_offer.id,
    }


async def test_accept_offer_locks_trade_and_rejects_competing_offers(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await seed_request_with_offer(session_factory)
    requester_headers = await login_headers(auth_service, email="requester@example.com")

    response = await client.post(
        f"/api/v1/offers/{seeded['offer_id']}/accept",
        headers=requester_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == str(seeded["request_id"])
    assert body["accepted_offer_id"] == str(seeded["offer_id"])
    assert body["status"] == "terms_locked"

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        exchange_request = await uow.exchange_requests.get(seeded["request_id"])
        accepted_offer = await uow.exchange_offers.get(seeded["offer_id"])
        competing_offer = await uow.exchange_offers.get(seeded["competing_offer_id"])
        locked_events = await uow.outbox_events.list_admin(event_type="trade_contract.locked")
        accepted_events = await uow.outbox_events.list_admin(event_type="exchange_offer.accepted")
        rejected_events = await uow.outbox_events.list_admin(event_type="exchange_offer.rejected")

        assert exchange_request.status is ExchangeRequestStatus.TERMS_LOCKED
        assert accepted_offer.status is ExchangeOfferStatus.ACCEPTED
        assert competing_offer.status is ExchangeOfferStatus.REJECTED
        assert {event.recipient_user_id for event in locked_events} == {
            seeded["requester_id"],
            seeded["counterparty_id"],
        }
        assert [event.aggregate_id for event in accepted_events] == [seeded["offer_id"]]
        assert [event.aggregate_id for event in rejected_events] == [
            seeded["competing_offer_id"]
        ]


async def test_accept_offer_requires_request_creator(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await seed_request_with_offer(session_factory)
    counterparty_headers = await login_headers(auth_service, email="counterparty@example.com")

    response = await client.post(
        f"/api/v1/offers/{seeded['offer_id']}/accept",
        headers=counterparty_headers,
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "authorization_error"


async def test_accept_offer_rejects_non_active_or_expired_state(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    expired = await seed_request_with_offer(
        session_factory,
        email_prefix="expired-",
        request_expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    rejected_offer = await seed_request_with_offer(
        session_factory,
        email_prefix="rejected-",
        from_code="CAD",
        to_code="GBP",
        offer_status=ExchangeOfferStatus.REJECTED,
    )
    expired_requester_headers = await login_headers(
        auth_service, email="expired-requester@example.com"
    )
    rejected_requester_headers = await login_headers(
        auth_service,
        email="rejected-requester@example.com",
    )

    expired_response = await client.post(
        f"/api/v1/offers/{expired['offer_id']}/accept",
        headers=expired_requester_headers,
    )
    rejected_response = await client.post(
        f"/api/v1/offers/{rejected_offer['offer_id']}/accept",
        headers=rejected_requester_headers,
    )

    assert expired_response.status_code == 422
    assert rejected_response.status_code == 422
    assert expired_response.json()["error_code"] == "invariant_violation"
    assert rejected_response.json()["error_code"] == "invariant_violation"


async def test_get_trade_is_visible_only_to_participants(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await seed_request_with_offer(session_factory)
    requester_headers = await login_headers(auth_service, email="requester@example.com")
    counterparty_headers = await login_headers(auth_service, email="counterparty@example.com")
    outsider_headers = await login_headers(auth_service, email="outsider@example.com")

    accept_response = await client.post(
        f"/api/v1/offers/{seeded['offer_id']}/accept",
        headers=requester_headers,
    )
    trade_id = accept_response.json()["id"]

    requester_response = await client.get(
        f"/api/v1/trades/{trade_id}",
        headers=requester_headers,
    )
    counterparty_response = await client.get(
        f"/api/v1/trades/{trade_id}",
        headers=counterparty_headers,
    )
    outsider_response = await client.get(
        f"/api/v1/trades/{trade_id}",
        headers=outsider_headers,
    )

    assert requester_response.status_code == 200
    assert counterparty_response.status_code == 200
    assert outsider_response.status_code == 404
    assert outsider_response.json()["error_code"] == "not_found"


async def test_list_trades_returns_participant_trades_ordered_newest_first(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    requester_id, requester_headers = await create_user_and_token(
        session_factory,
        auth_service,
        email="trade-list-requester@example.com",
    )
    now = datetime.now(UTC)
    security = SecurityService()
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        first_counterparty = await uow.users.add(
            build_user(
                email="trade-list-first-counterparty@example.com",
                password_hash=security.hash_password(PASSWORD),
            )
        )
        second_counterparty = await uow.users.add(
            build_user(
                email="trade-list-second-counterparty@example.com",
                password_hash=security.hash_password(PASSWORD),
            )
        )
        outsider = await uow.users.add(
            build_user(
                email="trade-list-outsider@example.com",
                password_hash=security.hash_password(PASSWORD),
            )
        )
        usd = await uow.currencies.add(build_currency(code="USD", status=CurrencyStatus.ACTIVE))
        ngn = await uow.currencies.add(build_currency(code="NGN", status=CurrencyStatus.ACTIVE))
        gbp = await uow.currencies.add(build_currency(code="GBP", status=CurrencyStatus.ACTIVE))
        eur = await uow.currencies.add(build_currency(code="EUR", status=CurrencyStatus.ACTIVE))

        older_request = await uow.exchange_requests.add(
            build_exchange_request(
                creator_user_id=requester_id,
                from_currency_id=usd.id,
                to_currency_id=ngn.id,
                status=ExchangeRequestStatus.TERMS_LOCKED,
                created_at=now - timedelta(minutes=20),
            )
        )
        older_offer = await uow.exchange_offers.add(
            build_exchange_offer(
                request_id=older_request.id,
                offer_user_id=first_counterparty.id,
                status=ExchangeOfferStatus.ACCEPTED,
                created_at=now - timedelta(minutes=19),
            )
        )
        older_trade = await uow.trade_contracts.add(
            build_trade_contract(
                request_id=older_request.id,
                accepted_offer_id=older_offer.id,
                created_at=now - timedelta(minutes=10),
            )
        )

        newer_request = await uow.exchange_requests.add(
            build_exchange_request(
                creator_user_id=requester_id,
                from_currency_id=gbp.id,
                to_currency_id=eur.id,
                status=ExchangeRequestStatus.TERMS_LOCKED,
                created_at=now - timedelta(minutes=5),
            )
        )
        newer_offer = await uow.exchange_offers.add(
            build_exchange_offer(
                request_id=newer_request.id,
                offer_user_id=second_counterparty.id,
                status=ExchangeOfferStatus.ACCEPTED,
                created_at=now - timedelta(minutes=4),
            )
        )
        newer_trade = await uow.trade_contracts.add(
            build_trade_contract(
                request_id=newer_request.id,
                accepted_offer_id=newer_offer.id,
                created_at=now - timedelta(minutes=3),
            )
        )

        outsider_request = await uow.exchange_requests.add(
            build_exchange_request(
                creator_user_id=outsider.id,
                from_currency_id=usd.id,
                to_currency_id=ngn.id,
                status=ExchangeRequestStatus.TERMS_LOCKED,
            )
        )
        outsider_offer = await uow.exchange_offers.add(
            build_exchange_offer(
                request_id=outsider_request.id,
                offer_user_id=first_counterparty.id,
                status=ExchangeOfferStatus.ACCEPTED,
            )
        )
        outsider_trade = await uow.trade_contracts.add(
            build_trade_contract(
                request_id=outsider_request.id,
                accepted_offer_id=outsider_offer.id,
            )
        )
        await uow.commit()

    requester_response = await client.get("/api/v1/trades", headers=requester_headers)

    assert requester_response.status_code == 200
    assert [trade["id"] for trade in requester_response.json()] == [
        str(newer_trade.id),
        str(older_trade.id),
    ]
    assert str(outsider_trade.id) not in {trade["id"] for trade in requester_response.json()}


async def test_list_trades_requires_authentication(client: AsyncClient) -> None:
    response = await client.get("/api/v1/trades")

    assert response.status_code == 401
    assert response.json()["error_code"] == "authentication_error"
