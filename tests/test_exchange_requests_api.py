from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
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
from app.infrastructure.config import settings
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


async def seed_currencies_and_corridor(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    from_status: CurrencyStatus = CurrencyStatus.ACTIVE,
    to_status: CurrencyStatus = CurrencyStatus.ACTIVE,
    corridor_status: CorridorStatus = CorridorStatus.ACTIVE,
) -> dict[str, UUID]:
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        from_currency = await uow.currencies.add(build_currency(code="USD", status=from_status))
        to_currency = await uow.currencies.add(build_currency(code="NGN", status=to_status))
        corridor = await uow.corridors.add(
            build_corridor(
                from_currency_id=from_currency.id,
                to_currency_id=to_currency.id,
                status=corridor_status,
            )
        )
        await uow.commit()

    return {
        "from_currency_id": from_currency.id,
        "to_currency_id": to_currency.id,
        "corridor_id": corridor.id,
    }


async def seed_currencies_only(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    from_status: CurrencyStatus = CurrencyStatus.ACTIVE,
    to_status: CurrencyStatus = CurrencyStatus.ACTIVE,
) -> dict[str, UUID]:
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        from_currency = await uow.currencies.add(build_currency(code="USD", status=from_status))
        to_currency = await uow.currencies.add(build_currency(code="NGN", status=to_status))
        await uow.commit()

    return {
        "from_currency_id": from_currency.id,
        "to_currency_id": to_currency.id,
    }


async def test_create_exchange_request_succeeds_for_verified_user(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_currencies_and_corridor(session_factory)
    user_id, headers = await create_user_and_token(
        session_factory,
        auth_service,
        email="verified@example.com",
    )
    before = datetime.now(UTC)

    response = await client.post(
        "/api/v1/exchange-requests",
        headers=headers,
        json={
            "from_currency_code": "usd",
            "to_currency_code": "ngn",
            "from_amount": "100.00",
            "preferred_rate": "1500.00",
            "min_rate": "1450.00",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["creator_user_id"] == str(user_id)
    assert body["from_currency_code"] == "USD"
    assert body["to_currency_code"] == "NGN"
    assert body["status"] == "request_open"
    expires_at = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))
    expected = before + timedelta(minutes=settings.exchange_request_expiry_minutes)
    if expires_at.tzinfo is None:
        expected = expected.replace(tzinfo=None)
    assert abs((expires_at - expected).total_seconds()) < 10

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        events = await uow.outbox_events.list_admin(event_type="exchange_request.created")

    assert len(events) == 1
    assert events[0].aggregate_id == UUID(body["id"])
    assert events[0].recipient_user_id == user_id
    assert events[0].payload["from_currency_code"] == "USD"


async def test_create_exchange_request_requires_authentication(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/exchange-requests",
        json={
            "from_currency_code": "USD",
            "to_currency_code": "NGN",
            "from_amount": "100.00",
            "preferred_rate": "1500.00",
        },
    )

    assert response.status_code == 401
    assert response.json()["error_code"] == "authentication_error"


async def test_create_exchange_request_rejects_unverified_kyc_user(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_currencies_and_corridor(session_factory)
    _, headers = await create_user_and_token(
        session_factory,
        auth_service,
        email="pending-kyc@example.com",
        kyc_status=KycStatus.PENDING,
    )

    response = await client.post(
        "/api/v1/exchange-requests",
        headers=headers,
        json={
            "from_currency_code": "USD",
            "to_currency_code": "NGN",
            "from_amount": "100.00",
            "preferred_rate": "1500.00",
        },
    )

    assert response.status_code == 412
    assert response.json()["error_code"] == "precondition_failed"


async def test_create_exchange_request_rejects_inactive_user(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_currencies_and_corridor(session_factory)
    user_id, headers = await create_user_and_token(
        session_factory,
        auth_service,
        email="inactive@example.com",
    )
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        user = await uow.users.get(user_id)
        await uow.users.update(
            replace(user, status=UserStatus.INACTIVE, updated_at=datetime.now(UTC))
        )
        await uow.commit()

    response = await client.post(
        "/api/v1/exchange-requests",
        headers=headers,
        json={
            "from_currency_code": "USD",
            "to_currency_code": "NGN",
            "from_amount": "100.00",
            "preferred_rate": "1500.00",
        },
    )

    assert response.status_code == 401
    assert response.json()["error_code"] == "authentication_error"


async def test_create_exchange_request_rejects_unknown_or_inactive_currency(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_currencies_and_corridor(session_factory, to_status=CurrencyStatus.INACTIVE)
    _, headers = await create_user_and_token(
        session_factory, auth_service, email="currency@example.com"
    )

    unknown_response = await client.post(
        "/api/v1/exchange-requests",
        headers=headers,
        json={
            "from_currency_code": "ZZZ",
            "to_currency_code": "NGN",
            "from_amount": "100.00",
            "preferred_rate": "1500.00",
        },
    )
    inactive_response = await client.post(
        "/api/v1/exchange-requests",
        headers=headers,
        json={
            "from_currency_code": "USD",
            "to_currency_code": "NGN",
            "from_amount": "100.00",
            "preferred_rate": "1500.00",
        },
    )

    assert unknown_response.status_code == 404
    assert inactive_response.status_code == 404


async def test_create_exchange_request_rejects_same_currency_pair(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_currencies_and_corridor(session_factory)
    _, headers = await create_user_and_token(
        session_factory, auth_service, email="same@example.com"
    )

    response = await client.post(
        "/api/v1/exchange-requests",
        headers=headers,
        json={
            "from_currency_code": "USD",
            "to_currency_code": "USD",
            "from_amount": "100.00",
            "preferred_rate": "1500.00",
        },
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "invariant_violation"


async def test_create_exchange_request_rejects_missing_or_inactive_corridor(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, headers = await create_user_and_token(
        session_factory, auth_service, email="corridor@example.com"
    )
    currencies = await seed_currencies_only(session_factory)

    missing_response = await client.post(
        "/api/v1/exchange-requests",
        headers=headers,
        json={
            "from_currency_code": "USD",
            "to_currency_code": "NGN",
            "from_amount": "100.00",
            "preferred_rate": "1500.00",
        },
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        await uow.corridors.add(
            build_corridor(
                from_currency_id=currencies["from_currency_id"],
                to_currency_id=currencies["to_currency_id"],
                status=CorridorStatus.INACTIVE,
            )
        )
        await uow.commit()

    inactive_response = await client.post(
        "/api/v1/exchange-requests",
        headers=headers,
        json={
            "from_currency_code": "USD",
            "to_currency_code": "NGN",
            "from_amount": "100.00",
            "preferred_rate": "1500.00",
        },
    )

    assert missing_response.status_code == 404
    assert inactive_response.status_code == 404


async def test_create_exchange_request_rejects_amount_outside_currency_limits(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_currencies_and_corridor(session_factory)
    _, headers = await create_user_and_token(
        session_factory, auth_service, email="amounts@example.com"
    )

    below_response = await client.post(
        "/api/v1/exchange-requests",
        headers=headers,
        json={
            "from_currency_code": "USD",
            "to_currency_code": "NGN",
            "from_amount": "0.50",
            "preferred_rate": "1500.00",
        },
    )
    above_response = await client.post(
        "/api/v1/exchange-requests",
        headers=headers,
        json={
            "from_currency_code": "USD",
            "to_currency_code": "NGN",
            "from_amount": "1000001.00",
            "preferred_rate": "1500.00",
        },
    )

    assert below_response.status_code == 422
    assert above_response.status_code == 422


async def test_create_exchange_request_rejects_invalid_rate_combinations(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_currencies_and_corridor(session_factory)
    _, headers = await create_user_and_token(
        session_factory, auth_service, email="rates@example.com"
    )

    response = await client.post(
        "/api/v1/exchange-requests",
        headers=headers,
        json={
            "from_currency_code": "USD",
            "to_currency_code": "NGN",
            "from_amount": "100.00",
            "preferred_rate": "1500.00",
            "min_rate": "1600.00",
        },
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "invariant_violation"


async def test_list_exchange_requests_returns_board_visible_requests(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    currencies = await seed_currencies_and_corridor(session_factory)
    viewer_id, headers = await create_user_and_token(
        session_factory, auth_service, email="viewer@example.com"
    )
    creator_id, _ = await create_user_and_token(
        session_factory, auth_service, email="creator@example.com"
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        visible_request = await uow.exchange_requests.add(
            build_exchange_request(
                creator_user_id=creator_id,
                from_currency_id=currencies["from_currency_id"],
                to_currency_id=currencies["to_currency_id"],
                status=ExchangeRequestStatus.REQUEST_OPEN,
            )
        )
        await uow.exchange_requests.add(
            build_exchange_request(
                creator_user_id=viewer_id,
                from_currency_id=currencies["to_currency_id"],
                to_currency_id=currencies["from_currency_id"],
                status=ExchangeRequestStatus.REQUEST_OPEN,
            )
        )
        await uow.exchange_requests.add(
            build_exchange_request(
                creator_user_id=creator_id,
                from_currency_id=currencies["from_currency_id"],
                to_currency_id=currencies["to_currency_id"],
                status=ExchangeRequestStatus.CANCELLED,
            )
        )
        await uow.exchange_requests.add(
            build_exchange_request(
                creator_user_id=creator_id,
                from_currency_id=currencies["from_currency_id"],
                to_currency_id=currencies["to_currency_id"],
                status=ExchangeRequestStatus.REQUEST_OPEN,
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
            )
        )
        await uow.commit()

    response = await client.get("/api/v1/exchange-requests", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert [request["id"] for request in body] == [str(visible_request.id)]
    assert all(request["creator_user_id"] != str(viewer_id) for request in body)


async def test_list_my_exchange_requests_returns_only_authenticated_users_requests(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    currencies = await seed_currencies_and_corridor(session_factory)
    user_id, headers = await create_user_and_token(
        session_factory, auth_service, email="owner@example.com"
    )
    other_user_id, _ = await create_user_and_token(
        session_factory, auth_service, email="other-owner@example.com"
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        older_request = await uow.exchange_requests.add(
            build_exchange_request(
                creator_user_id=user_id,
                from_currency_id=currencies["from_currency_id"],
                to_currency_id=currencies["to_currency_id"],
                created_at=datetime.now(UTC) - timedelta(minutes=10),
            )
        )
        newer_request = await uow.exchange_requests.add(
            build_exchange_request(
                creator_user_id=user_id,
                from_currency_id=currencies["to_currency_id"],
                to_currency_id=currencies["from_currency_id"],
                created_at=datetime.now(UTC),
            )
        )
        await uow.exchange_requests.add(
            build_exchange_request(
                creator_user_id=other_user_id,
                from_currency_id=currencies["from_currency_id"],
                to_currency_id=currencies["to_currency_id"],
            )
        )
        await uow.commit()

    response = await client.get("/api/v1/exchange-requests/mine", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert [request["id"] for request in body] == [str(newer_request.id), str(older_request.id)]
    assert all(request["creator_user_id"] == str(user_id) for request in body)


async def test_get_exchange_request_by_id_returns_own_or_board_visible_request(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    currencies = await seed_currencies_and_corridor(session_factory)
    user_id, headers = await create_user_and_token(
        session_factory, auth_service, email="get-owner@example.com"
    )
    other_user_id, _ = await create_user_and_token(
        session_factory,
        auth_service,
        email="hidden-owner@example.com",
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        own_request = await uow.exchange_requests.add(
            build_exchange_request(
                creator_user_id=user_id,
                from_currency_id=currencies["from_currency_id"],
                to_currency_id=currencies["to_currency_id"],
            )
        )
        visible_request = await uow.exchange_requests.add(
            build_exchange_request(
                creator_user_id=other_user_id,
                from_currency_id=currencies["from_currency_id"],
                to_currency_id=currencies["to_currency_id"],
                status=ExchangeRequestStatus.OFFER_PENDING,
            )
        )
        hidden_request = await uow.exchange_requests.add(
            build_exchange_request(
                creator_user_id=other_user_id,
                from_currency_id=currencies["from_currency_id"],
                to_currency_id=currencies["to_currency_id"],
                status=ExchangeRequestStatus.CANCELLED,
            )
        )
        await uow.commit()

    own_response = await client.get(f"/api/v1/exchange-requests/{own_request.id}", headers=headers)
    visible_response = await client.get(
        f"/api/v1/exchange-requests/{visible_request.id}",
        headers=headers,
    )
    hidden_response = await client.get(
        f"/api/v1/exchange-requests/{hidden_request.id}",
        headers=headers,
    )

    assert own_response.status_code == 200
    assert own_response.json()["id"] == str(own_request.id)
    assert visible_response.status_code == 200
    assert visible_response.json()["id"] == str(visible_request.id)
    assert hidden_response.status_code == 404
    assert hidden_response.json()["error_code"] == "not_found"


async def test_cancel_exchange_request_cancels_active_request_and_rejects_offers(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    currencies = await seed_currencies_and_corridor(session_factory)
    creator_id, creator_headers = await create_user_and_token(
        session_factory,
        auth_service,
        email="cancel-owner@example.com",
    )
    offer_user_id, _ = await create_user_and_token(
        session_factory,
        auth_service,
        email="cancel-offerer@example.com",
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        exchange_request = await uow.exchange_requests.add(
            build_exchange_request(
                creator_user_id=creator_id,
                from_currency_id=currencies["from_currency_id"],
                to_currency_id=currencies["to_currency_id"],
                status=ExchangeRequestStatus.OFFER_PENDING,
            )
        )
        active_offer = await uow.exchange_offers.add(
            build_exchange_offer(
                request_id=exchange_request.id,
                offer_user_id=offer_user_id,
            )
        )
        await uow.commit()

    response = await client.post(
        f"/api/v1/exchange-requests/{exchange_request.id}/cancel",
        headers=creator_headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        reloaded_request = await uow.exchange_requests.get(exchange_request.id)
        reloaded_offer = await uow.exchange_offers.get(active_offer.id)
        assert reloaded_request.status is ExchangeRequestStatus.CANCELLED
        assert reloaded_offer.status.name == "REJECTED"


async def test_cancel_exchange_request_hides_other_users_request(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    currencies = await seed_currencies_and_corridor(session_factory)
    owner_id, _ = await create_user_and_token(
        session_factory,
        auth_service,
        email="hidden-cancel-owner@example.com",
    )
    _, other_headers = await create_user_and_token(
        session_factory,
        auth_service,
        email="hidden-cancel-viewer@example.com",
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        exchange_request = await uow.exchange_requests.add(
            build_exchange_request(
                creator_user_id=owner_id,
                from_currency_id=currencies["from_currency_id"],
                to_currency_id=currencies["to_currency_id"],
            )
        )
        await uow.commit()

    response = await client.post(
        f"/api/v1/exchange-requests/{exchange_request.id}/cancel",
        headers=other_headers,
    )

    assert response.status_code == 404
    assert response.json()["error_code"] == "not_found"


async def test_cancel_exchange_request_rejects_locked_request(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    currencies = await seed_currencies_and_corridor(session_factory)
    owner_id, owner_headers = await create_user_and_token(
        session_factory,
        auth_service,
        email="locked-cancel-owner@example.com",
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        exchange_request = await uow.exchange_requests.add(
            build_exchange_request(
                creator_user_id=owner_id,
                from_currency_id=currencies["from_currency_id"],
                to_currency_id=currencies["to_currency_id"],
                status=ExchangeRequestStatus.TERMS_LOCKED,
            )
        )
        await uow.commit()

    response = await client.post(
        f"/api/v1/exchange-requests/{exchange_request.id}/cancel",
        headers=owner_headers,
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "invariant_violation"
