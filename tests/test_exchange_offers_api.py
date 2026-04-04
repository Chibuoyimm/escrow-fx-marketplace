from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import (
    CorridorStatus,
    CurrencyStatus,
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
from tests.conftest import (
    build_corridor,
    build_currency,
    build_exchange_offer,
    build_exchange_request,
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
async def client(
    auth_service: AuthService,
    exchange_request_service: ExchangeRequestService,
    exchange_offer_service: ExchangeOfferService,
) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    app.dependency_overrides[get_exchange_request_service] = lambda: exchange_request_service
    app.dependency_overrides[get_exchange_offer_service] = lambda: exchange_offer_service
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


async def seed_marketplace_request(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    request_status: ExchangeRequestStatus = ExchangeRequestStatus.REQUEST_OPEN,
    expires_at: datetime | None = None,
    creator_email: str = "creator@example.com",
    from_code: str = "USD",
    to_code: str = "NGN",
) -> dict[str, UUID]:
    security = SecurityService()
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        creator = await uow.users.add(
            build_user(
                email=creator_email,
                password_hash=security.hash_password(PASSWORD),
            )
        )
        from_currency = await uow.currencies.add(
            build_currency(code=from_code, status=CurrencyStatus.ACTIVE)
        )
        to_currency = await uow.currencies.add(
            build_currency(code=to_code, status=CurrencyStatus.ACTIVE)
        )
        await uow.corridors.add(
            build_corridor(
                from_currency_id=from_currency.id,
                to_currency_id=to_currency.id,
                status=CorridorStatus.ACTIVE,
            )
        )
        exchange_request = await uow.exchange_requests.add(
            build_exchange_request(
                creator_user_id=creator.id,
                from_currency_id=from_currency.id,
                to_currency_id=to_currency.id,
                status=request_status,
                expires_at=expires_at or (datetime.now(UTC) + timedelta(hours=1)),
            )
        )
        await uow.commit()

    return {"creator_id": creator.id, "request_id": exchange_request.id}


async def test_create_exchange_offer_succeeds_and_promotes_request(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await seed_marketplace_request(session_factory)
    offer_user_id, headers = await create_user_and_token(
        session_factory,
        auth_service,
        email="offerer@example.com",
    )

    response = await client.post(
        f"/api/v1/exchange-requests/{seeded['request_id']}/offers",
        headers=headers,
        json={"offered_rate": "1490.00"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["offer_user_id"] == str(offer_user_id)
    assert body["request_id"] == str(seeded["request_id"])
    assert body["status"] == "active"

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        exchange_request = await uow.exchange_requests.get(seeded["request_id"])
        assert exchange_request.status is ExchangeRequestStatus.OFFER_PENDING


async def test_create_exchange_offer_requires_authentication(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await seed_marketplace_request(session_factory)

    response = await client.post(
        f"/api/v1/exchange-requests/{seeded['request_id']}/offers",
        json={"offered_rate": "1490.00"},
    )

    assert response.status_code == 401
    assert response.json()["error_code"] == "authentication_error"


async def test_create_exchange_offer_rejects_unverified_kyc_user(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await seed_marketplace_request(session_factory)
    _, headers = await create_user_and_token(
        session_factory,
        auth_service,
        email="pending-kyc@example.com",
        kyc_status=KycStatus.PENDING,
    )

    response = await client.post(
        f"/api/v1/exchange-requests/{seeded['request_id']}/offers",
        headers=headers,
        json={"offered_rate": "1490.00"},
    )

    assert response.status_code == 412
    assert response.json()["error_code"] == "precondition_failed"


async def test_create_exchange_offer_rejects_own_request_and_duplicate_offer(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await seed_marketplace_request(session_factory, creator_email="same@example.com")
    own_headers = await login_headers(auth_service, email="same@example.com")
    _, offer_headers = await create_user_and_token(
        session_factory,
        auth_service,
        email="counterparty@example.com",
    )

    own_response = await client.post(
        f"/api/v1/exchange-requests/{seeded['request_id']}/offers",
        headers=own_headers,
        json={"offered_rate": "1490.00"},
    )
    first_offer = await client.post(
        f"/api/v1/exchange-requests/{seeded['request_id']}/offers",
        headers=offer_headers,
        json={"offered_rate": "1490.00"},
    )
    duplicate_offer = await client.post(
        f"/api/v1/exchange-requests/{seeded['request_id']}/offers",
        headers=offer_headers,
        json={"offered_rate": "1485.00"},
    )

    assert own_response.status_code == 422
    assert own_response.json()["error_code"] == "invariant_violation"
    assert first_offer.status_code == 201
    assert duplicate_offer.status_code == 409
    assert duplicate_offer.json()["error_code"] == "conflict"


async def test_create_exchange_offer_rejects_hidden_or_expired_request(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    hidden = await seed_marketplace_request(
        session_factory,
        request_status=ExchangeRequestStatus.CANCELLED,
        creator_email="hidden@example.com",
    )
    expired = await seed_marketplace_request(
        session_factory,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
        creator_email="expired@example.com",
        from_code="CAD",
        to_code="GBP",
    )
    _, headers = await create_user_and_token(
        session_factory,
        auth_service,
        email="viewer@example.com",
    )

    hidden_response = await client.post(
        f"/api/v1/exchange-requests/{hidden['request_id']}/offers",
        headers=headers,
        json={"offered_rate": "1490.00"},
    )
    expired_response = await client.post(
        f"/api/v1/exchange-requests/{expired['request_id']}/offers",
        headers=headers,
        json={"offered_rate": "1490.00"},
    )

    assert hidden_response.status_code == 404
    assert expired_response.status_code == 404


async def test_create_exchange_offer_rejects_rate_below_request_minimum(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await seed_marketplace_request(session_factory)
    _, headers = await create_user_and_token(
        session_factory,
        auth_service,
        email="below-min@example.com",
    )

    response = await client.post(
        f"/api/v1/exchange-requests/{seeded['request_id']}/offers",
        headers=headers,
        json={"offered_rate": "1400.00"},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "invariant_violation"


async def test_list_exchange_request_offers_returns_request_creator_view(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await seed_marketplace_request(
        session_factory, creator_email="creator-view@example.com"
    )
    creator_headers = await login_headers(auth_service, email="creator-view@example.com")
    offer_user_id, offer_headers = await create_user_and_token(
        session_factory,
        auth_service,
        email="offer-view@example.com",
    )
    other_offer_user_id, other_headers = await create_user_and_token(
        session_factory,
        auth_service,
        email="other-view@example.com",
    )

    await client.post(
        f"/api/v1/exchange-requests/{seeded['request_id']}/offers",
        headers=offer_headers,
        json={"offered_rate": "1490.00"},
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        await uow.exchange_offers.add(
            build_exchange_offer(
                request_id=seeded["request_id"],
                offer_user_id=other_offer_user_id,
                offered_rate=Decimal("1485.00"),
            )
        )
        await uow.commit()

    creator_response = await client.get(
        f"/api/v1/exchange-requests/{seeded['request_id']}/offers",
        headers=creator_headers,
    )
    other_response = await client.get(
        f"/api/v1/exchange-requests/{seeded['request_id']}/offers",
        headers=offer_headers,
    )

    assert creator_response.status_code == 200
    assert len(creator_response.json()) == 2
    assert all(
        offer["request_id"] == str(seeded["request_id"]) for offer in creator_response.json()
    )
    assert other_response.status_code == 403
    assert other_response.json()["error_code"] == "authorization_error"
