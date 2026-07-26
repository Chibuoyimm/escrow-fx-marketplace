from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
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
from tests.conftest import (
    assert_canonical_mutation_lock_order,
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
    mutation_lock_calls: list[tuple[str, UUID]],
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
    assert_canonical_mutation_lock_order(mutation_lock_calls)
    assert [resource for resource, _ in mutation_lock_calls] == [
        "user",
        "user",
        "request",
    ]

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        exchange_request = await uow.exchange_requests.get(seeded["request_id"])
        assert exchange_request.status is ExchangeRequestStatus.OFFER_PENDING


@pytest.mark.parametrize("creator_status", [UserStatus.SUSPENDED, UserStatus.INACTIVE])
async def test_create_offer_rejects_request_with_non_active_creator(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
    creator_status: UserStatus,
) -> None:
    seeded = await seed_marketplace_request(
        session_factory,
        creator_email=f"non-active-creator-{creator_status.value}@example.com",
    )
    _, headers = await create_user_and_token(
        session_factory,
        auth_service,
        email=f"active-offerer-{creator_status.value}@example.com",
    )
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        creator = await uow.users.get_for_update(seeded["creator_id"])
        await uow.users.update(replace(creator, status=creator_status))
        await uow.commit()

    response = await client.post(
        f"/api/v1/exchange-requests/{seeded['request_id']}/offers",
        headers=headers,
        json={"offered_rate": "1490.00"},
    )

    assert response.status_code == 412
    assert response.json()["error_code"] == "precondition_failed"
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        request = await uow.exchange_requests.get(seeded["request_id"])
        assert request.status is ExchangeRequestStatus.REQUEST_OPEN
        assert await uow.exchange_offers.list_for_request(request.id) == []


async def test_update_exchange_offer_persists_rate_and_notifies_request_creator(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
    mutation_lock_calls: list[tuple[str, UUID]],
) -> None:
    seeded = await seed_marketplace_request(
        session_factory, creator_email="offer-update-owner@example.com"
    )
    creator_headers = await login_headers(auth_service, email="offer-update-owner@example.com")
    _, offer_headers = await create_user_and_token(
        session_factory, auth_service, email="offer-update-counterparty@example.com"
    )
    created = await client.post(
        f"/api/v1/exchange-requests/{seeded['request_id']}/offers",
        headers=offer_headers,
        json={"offered_rate": "1490"},
    )
    offer_id = created.json()["id"]
    mutation_lock_calls.clear()

    response = await client.patch(
        f"/api/v1/offers/{offer_id}",
        headers=offer_headers,
        json={"offered_rate": "1510"},
    )

    assert response.status_code == 200
    assert Decimal(response.json()["offered_rate"]) == Decimal("1510")
    assert_canonical_mutation_lock_order(mutation_lock_calls)
    assert [resource for resource, _ in mutation_lock_calls] == [
        "user",
        "user",
        "request",
        "offer",
    ]
    creator_view = await client.get(f"/api/v1/offers/{offer_id}", headers=creator_headers)
    assert creator_view.status_code == 200
    assert Decimal(creator_view.json()["offered_rate"]) == Decimal("1510")
    mine_view = await client.get(
        "/api/v1/offers/mine?min_offered_rate=1500",
        headers=offer_headers,
    )
    assert mine_view.status_code == 200
    assert [item["id"] for item in mine_view.json()["items"]] == [offer_id]
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        events = await uow.outbox_events.list_admin(event_type="exchange_offer.updated")
        assert len(events) == 1
        assert events[0].recipient_user_id is not None
        assert events[0].payload["offered_rate"] == "1510"


async def test_update_exchange_offer_noop_preserves_timestamp_and_emits_no_event(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await seed_marketplace_request(
        session_factory, creator_email="offer-noop-owner@example.com"
    )
    _, offer_headers = await create_user_and_token(
        session_factory, auth_service, email="offer-noop-counterparty@example.com"
    )
    created = await client.post(
        f"/api/v1/exchange-requests/{seeded['request_id']}/offers",
        headers=offer_headers,
        json={"offered_rate": "1490"},
    )
    offer_id = created.json()["id"]
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        before = await uow.exchange_offers.get(UUID(offer_id))

    response = await client.patch(
        f"/api/v1/offers/{offer_id}",
        headers=offer_headers,
        json={"offered_rate": "1490"},
    )

    assert response.status_code == 200
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        after = await uow.exchange_offers.get(UUID(offer_id))
        events = await uow.outbox_events.list_admin(event_type="exchange_offer.updated")
    assert after.updated_at == before.updated_at
    assert events == []


async def test_update_exchange_offer_requires_current_verified_kyc(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await seed_marketplace_request(
        session_factory, creator_email="offer-kyc-owner@example.com"
    )
    offer_user_id, offer_headers = await create_user_and_token(
        session_factory, auth_service, email="offer-kyc-counterparty@example.com"
    )
    created = await client.post(
        f"/api/v1/exchange-requests/{seeded['request_id']}/offers",
        headers=offer_headers,
        json={"offered_rate": "1490"},
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        user = await uow.users.get(offer_user_id)
        await uow.users.update(replace(user, kyc_status=KycStatus.PENDING))
        await uow.commit()

    response = await client.patch(
        f"/api/v1/offers/{created.json()['id']}",
        headers=offer_headers,
        json={"offered_rate": "1510"},
    )

    assert response.status_code == 412
    assert response.json()["error_code"] == "precondition_failed"


@pytest.mark.parametrize(
    ("request_status", "offer_status", "offer_expired", "request_expired"),
    [
        (ExchangeRequestStatus.CANCELLED, ExchangeOfferStatus.ACTIVE, False, False),
        (ExchangeRequestStatus.REQUEST_OPEN, ExchangeOfferStatus.WITHDRAWN, False, False),
        (ExchangeRequestStatus.REQUEST_OPEN, ExchangeOfferStatus.ACTIVE, True, False),
        (ExchangeRequestStatus.REQUEST_OPEN, ExchangeOfferStatus.ACTIVE, False, True),
    ],
)
async def test_update_exchange_offer_rejects_terminal_or_expired_market_state(
    request_status: ExchangeRequestStatus,
    offer_status: ExchangeOfferStatus,
    offer_expired: bool,
    request_expired: bool,
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await seed_marketplace_request(
        session_factory,
        request_status=ExchangeRequestStatus.REQUEST_OPEN,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        creator_email=f"offer-state-owner-{request_status.value}-{offer_status.value}@example.com",
    )
    _, offer_headers = await create_user_and_token(
        session_factory,
        auth_service,
        email=f"offer-state-counterparty-{request_status.value}-{offer_status.value}@example.com",
    )
    created = await client.post(
        f"/api/v1/exchange-requests/{seeded['request_id']}/offers",
        headers=offer_headers,
        json={"offered_rate": "1490"},
    )
    assert created.status_code == 201
    offer_id = UUID(created.json()["id"])

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        request = await uow.exchange_requests.get(seeded["request_id"])
        if request_status is not ExchangeRequestStatus.REQUEST_OPEN or request_expired:
            request = replace(
                request,
                status=request_status,
                expires_at=datetime.now(UTC) - timedelta(minutes=1)
                if request_expired
                else request.expires_at,
            )
            await uow.exchange_requests.update(request)
        offer = await uow.exchange_offers.get(offer_id)
        if offer_status is not ExchangeOfferStatus.ACTIVE or offer_expired:
            offer = replace(
                offer,
                status=offer_status,
                expires_at=datetime.now(UTC) - timedelta(minutes=1)
                if offer_expired
                else offer.expires_at,
            )
            await uow.exchange_offers.update(offer)
        await uow.commit()

    response = await client.patch(
        f"/api/v1/offers/{offer_id}",
        headers=offer_headers,
        json={"offered_rate": "1510"},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "invariant_violation"


async def test_update_exchange_offer_rejects_rate_below_request_minimum(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await seed_marketplace_request(
        session_factory, creator_email="offer-min-update-owner@example.com"
    )
    _, offer_headers = await create_user_and_token(
        session_factory, auth_service, email="offer-min-update-counterparty@example.com"
    )
    created = await client.post(
        f"/api/v1/exchange-requests/{seeded['request_id']}/offers",
        headers=offer_headers,
        json={"offered_rate": "1490"},
    )

    response = await client.patch(
        f"/api/v1/offers/{created.json()['id']}",
        headers=offer_headers,
        json={"offered_rate": "1400"},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "invariant_violation"


async def test_offer_history_includes_terminal_request_context_for_owner(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await seed_marketplace_request(
        session_factory, creator_email="offer-history-owner@example.com"
    )
    _, offer_headers = await create_user_and_token(
        session_factory, auth_service, email="offer-history-counterparty@example.com"
    )
    created = await client.post(
        f"/api/v1/exchange-requests/{seeded['request_id']}/offers",
        headers=offer_headers,
        json={"offered_rate": "1490"},
    )
    offer_id = UUID(created.json()["id"])
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        request = await uow.exchange_requests.get(seeded["request_id"])
        await uow.exchange_requests.update(replace(request, status=ExchangeRequestStatus.CANCELLED))
        await uow.exchange_offers.update(
            replace(
                await uow.exchange_offers.get(offer_id),
                status=ExchangeOfferStatus.WITHDRAWN,
            )
        )
        await uow.commit()

    response = await client.get(f"/api/v1/offers/{offer_id}", headers=offer_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["request_status"] == "cancelled"
    assert body["from_currency_code"] == "USD"
    assert body["to_currency_code"] == "NGN"
    assert Decimal(body["request_from_amount"]) == Decimal("100")
    assert Decimal(body["request_preferred_rate"]) == Decimal("1500")
    assert "creator_user_id" not in body


async def test_exchange_offer_visibility_is_limited_to_participants(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await seed_marketplace_request(
        session_factory, creator_email="offer-visible-owner@example.com"
    )
    creator_headers = await login_headers(auth_service, email="offer-visible-owner@example.com")
    _, offer_headers = await create_user_and_token(
        session_factory, auth_service, email="offer-visible-counterparty@example.com"
    )
    _, outsider_headers = await create_user_and_token(
        session_factory, auth_service, email="offer-visible-outsider@example.com"
    )
    created = await client.post(
        f"/api/v1/exchange-requests/{seeded['request_id']}/offers",
        headers=offer_headers,
        json={"offered_rate": "1490"},
    )
    offer_id = created.json()["id"]

    owner_response = await client.get(f"/api/v1/offers/{offer_id}", headers=offer_headers)
    creator_response = await client.get(f"/api/v1/offers/{offer_id}", headers=creator_headers)
    outsider_response = await client.get(f"/api/v1/offers/{offer_id}", headers=outsider_headers)

    assert owner_response.status_code == 200
    assert creator_response.status_code == 200
    assert outsider_response.status_code == 404


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


async def test_offer_mine_validates_ranges_and_malformed_cursor(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await seed_marketplace_request(session_factory)
    _, headers = await create_user_and_token(
        session_factory, auth_service, email="offer-filter-owner@example.com"
    )
    await client.post(
        f"/api/v1/exchange-requests/{seeded['request_id']}/offers",
        headers=headers,
        json={"offered_rate": "1490"},
    )

    invalid_range = await client.get(
        "/api/v1/offers/mine?min_offered_rate=1500&max_offered_rate=1400",
        headers=headers,
    )
    invalid_dates = await client.get(
        "/api/v1/offers/mine?created_from=2026-07-02T00:00:00Z&created_to=2026-07-01T00:00:00Z",
        headers=headers,
    )
    malformed_cursor = await client.get(
        "/api/v1/offers/mine?cursor=bad",
        headers=headers,
    )

    assert invalid_range.status_code == 422
    assert invalid_range.json()["error_code"] == "invariant_violation"
    assert invalid_dates.status_code == 422
    assert malformed_cursor.status_code == 422


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
    assert len(creator_response.json()["items"]) == 2
    assert all(
        offer["request_id"] == str(seeded["request_id"])
        for offer in creator_response.json()["items"]
    )
    assert other_response.status_code == 403
    assert other_response.json()["error_code"] == "authorization_error"


async def test_withdraw_exchange_offer_marks_offer_withdrawn_and_reopens_request_if_last_active(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
    mutation_lock_calls: list[tuple[str, UUID]],
) -> None:
    seeded = await seed_marketplace_request(
        session_factory,
        request_status=ExchangeRequestStatus.OFFER_PENDING,
    )
    offer_user_id, offer_headers = await create_user_and_token(
        session_factory,
        auth_service,
        email="withdraw-owner@example.com",
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        offer = await uow.exchange_offers.add(
            build_exchange_offer(
                request_id=seeded["request_id"],
                offer_user_id=offer_user_id,
            )
        )
        await uow.commit()

    response = await client.post(f"/api/v1/offers/{offer.id}/withdraw", headers=offer_headers)

    assert response.status_code == 200
    assert response.json()["status"] == "withdrawn"
    assert_canonical_mutation_lock_order(mutation_lock_calls)
    assert [resource for resource, _ in mutation_lock_calls] == [
        "user",
        "user",
        "request",
        "offer",
    ]

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        reloaded_offer = await uow.exchange_offers.get(offer.id)
        reloaded_request = await uow.exchange_requests.get(seeded["request_id"])
        assert reloaded_offer.status is ExchangeOfferStatus.WITHDRAWN
        assert reloaded_request.status is ExchangeRequestStatus.REQUEST_OPEN


async def test_withdraw_exchange_offer_hides_other_users_offer(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await seed_marketplace_request(session_factory)
    offer_user_id, _ = await create_user_and_token(
        session_factory,
        auth_service,
        email="withdraw-hidden-owner@example.com",
    )
    _, other_headers = await create_user_and_token(
        session_factory,
        auth_service,
        email="withdraw-hidden-other@example.com",
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        offer = await uow.exchange_offers.add(
            build_exchange_offer(
                request_id=seeded["request_id"],
                offer_user_id=offer_user_id,
            )
        )
        await uow.commit()

    response = await client.post(f"/api/v1/offers/{offer.id}/withdraw", headers=other_headers)

    assert response.status_code == 404
    assert response.json()["error_code"] == "not_found"


async def test_reject_exchange_offer_marks_offer_rejected_and_reopens_request_if_last_active(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
    mutation_lock_calls: list[tuple[str, UUID]],
) -> None:
    seeded = await seed_marketplace_request(
        session_factory,
        request_status=ExchangeRequestStatus.OFFER_PENDING,
        creator_email="reject-owner@example.com",
    )
    creator_headers = await login_headers(auth_service, email="reject-owner@example.com")
    offer_user_id, _ = await create_user_and_token(
        session_factory,
        auth_service,
        email="reject-offerer@example.com",
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        offer = await uow.exchange_offers.add(
            build_exchange_offer(
                request_id=seeded["request_id"],
                offer_user_id=offer_user_id,
            )
        )
        await uow.commit()

    response = await client.post(f"/api/v1/offers/{offer.id}/reject", headers=creator_headers)

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert_canonical_mutation_lock_order(mutation_lock_calls)
    assert [resource for resource, _ in mutation_lock_calls] == [
        "user",
        "user",
        "request",
        "offer",
    ]

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        reloaded_offer = await uow.exchange_offers.get(offer.id)
        reloaded_request = await uow.exchange_requests.get(seeded["request_id"])
        assert reloaded_offer.status is ExchangeOfferStatus.REJECTED
        assert reloaded_request.status is ExchangeRequestStatus.REQUEST_OPEN


async def test_reject_exchange_offer_requires_request_creator(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await seed_marketplace_request(session_factory)
    offer_user_id, offer_headers = await create_user_and_token(
        session_factory,
        auth_service,
        email="reject-owner-check@example.com",
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        offer = await uow.exchange_offers.add(
            build_exchange_offer(
                request_id=seeded["request_id"],
                offer_user_id=offer_user_id,
            )
        )
        await uow.commit()

    response = await client.post(f"/api/v1/offers/{offer.id}/reject", headers=offer_headers)

    assert response.status_code == 403
    assert response.json()["error_code"] == "authorization_error"
