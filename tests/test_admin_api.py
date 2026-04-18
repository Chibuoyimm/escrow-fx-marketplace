from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import (
    CurrencyStatus,
    ExchangeOfferStatus,
    ExchangeRequestStatus,
    TradeContractStatus,
    UserRole,
    UserStatus,
)
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.security import SecurityService
from app.main import app
from app.services.admin import AdminService, get_admin_service
from app.services.auth import AuthService, get_auth_service
from tests.conftest import (
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
def admin_service(session_factory: async_sessionmaker[AsyncSession]) -> AdminService:
    return AdminService(uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory))


@pytest.fixture
async def client(
    auth_service: AuthService,
    admin_service: AdminService,
) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    app.dependency_overrides[get_admin_service] = lambda: admin_service
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
    role: UserRole = UserRole.CUSTOMER,
    status: UserStatus = UserStatus.ACTIVE,
    issue_token: bool = True,
) -> tuple[dict[str, str], str]:
    security = SecurityService()
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        user = await uow.users.add(
            build_user(
                email=email,
                password_hash=security.hash_password(PASSWORD),
                status=status,
            )
        )
        if role is not UserRole.CUSTOMER:
            user = await uow.users.update(replace(user, role=role))
        await uow.commit()

    if not issue_token:
        return {}, str(user.id)

    token_response = await auth_service.login_user(email=email, password=PASSWORD)
    return {"Authorization": f"Bearer {token_response.access_token}"}, user.id.hex


async def seed_admin_marketplace_data(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, str]:
    security = SecurityService()
    now = datetime.now(UTC)
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        requester = await uow.users.add(
            build_user(
                email="admin-requester@example.com",
                password_hash=security.hash_password(PASSWORD),
            )
        )
        counterparty = await uow.users.add(
            build_user(
                email="admin-counterparty@example.com",
                password_hash=security.hash_password(PASSWORD),
            )
        )
        usd = await uow.currencies.add(build_currency(code="USD", status=CurrencyStatus.ACTIVE))
        ngn = await uow.currencies.add(build_currency(code="NGN", status=CurrencyStatus.ACTIVE))

        older_open_request = await uow.exchange_requests.add(
            build_exchange_request(
                creator_user_id=requester.id,
                from_currency_id=usd.id,
                to_currency_id=ngn.id,
                status=ExchangeRequestStatus.REQUEST_OPEN,
                created_at=now - timedelta(minutes=10),
            )
        )
        newer_cancelled_request = await uow.exchange_requests.add(
            build_exchange_request(
                creator_user_id=requester.id,
                from_currency_id=usd.id,
                to_currency_id=ngn.id,
                status=ExchangeRequestStatus.CANCELLED,
                created_at=now - timedelta(minutes=1),
            )
        )

        active_offer = await uow.exchange_offers.add(
            build_exchange_offer(
                request_id=older_open_request.id,
                offer_user_id=counterparty.id,
                status=ExchangeOfferStatus.ACTIVE,
                created_at=now - timedelta(minutes=5),
            )
        )
        rejected_offer = await uow.exchange_offers.add(
            build_exchange_offer(
                request_id=newer_cancelled_request.id,
                offer_user_id=counterparty.id,
                status=ExchangeOfferStatus.REJECTED,
                created_at=now - timedelta(minutes=1),
            )
        )
        trade = await uow.trade_contracts.add(
            build_trade_contract(
                request_id=older_open_request.id,
                accepted_offer_id=active_offer.id,
                status=TradeContractStatus.TERMS_LOCKED,
            )
        )
        cancelled_trade = await uow.trade_contracts.add(
            build_trade_contract(
                request_id=newer_cancelled_request.id,
                accepted_offer_id=rejected_offer.id,
                status=TradeContractStatus.CANCELLED,
            )
        )
        await uow.commit()

    return {
        "older_open_request_id": str(older_open_request.id),
        "newer_cancelled_request_id": str(newer_cancelled_request.id),
        "active_offer_id": str(active_offer.id),
        "rejected_offer_id": str(rejected_offer.id),
        "trade_id": str(trade.id),
        "cancelled_trade_id": str(cancelled_trade.id),
    }


async def test_admin_routes_require_admin_or_operations_role(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    customer_headers, _ = await create_user_and_token(
        session_factory,
        auth_service,
        email="admin-customer@example.com",
    )
    operations_headers, _ = await create_user_and_token(
        session_factory,
        auth_service,
        email="operations@example.com",
        role=UserRole.OPERATIONS,
    )

    missing_response = await client.get("/api/v1/admin/users")
    customer_response = await client.get("/api/v1/admin/users", headers=customer_headers)
    operations_response = await client.get("/api/v1/admin/users", headers=operations_headers)

    assert missing_response.status_code == 401
    assert missing_response.json()["error_code"] == "authentication_error"
    assert customer_response.status_code == 403
    assert customer_response.json()["error_code"] == "authorization_error"
    assert operations_response.status_code == 200


async def test_admin_lists_users_with_status_filter(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    admin_headers, _ = await create_user_and_token(
        session_factory,
        auth_service,
        email="admin-user@example.com",
        role=UserRole.ADMIN,
    )
    await create_user_and_token(
        session_factory,
        auth_service,
        email="inactive-user@example.com",
        status=UserStatus.INACTIVE,
        issue_token=False,
    )

    response = await client.get("/api/v1/admin/users?status=inactive", headers=admin_headers)

    assert response.status_code == 200
    body = response.json()
    assert [user["email"] for user in body] == ["inactive-user@example.com"]
    assert body[0]["status"] == "inactive"


async def test_admin_lists_marketplace_records_with_status_filters(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    admin_headers, _ = await create_user_and_token(
        session_factory,
        auth_service,
        email="marketplace-admin@example.com",
        role=UserRole.ADMIN,
    )
    seeded = await seed_admin_marketplace_data(session_factory)

    requests_response = await client.get(
        "/api/v1/admin/exchange-requests",
        headers=admin_headers,
    )
    cancelled_requests_response = await client.get(
        "/api/v1/admin/exchange-requests?status=cancelled",
        headers=admin_headers,
    )
    active_offers_response = await client.get(
        "/api/v1/admin/exchange-offers?status=active",
        headers=admin_headers,
    )
    cancelled_trades_response = await client.get(
        "/api/v1/admin/trades?status=cancelled",
        headers=admin_headers,
    )

    assert requests_response.status_code == 200
    assert [request["id"] for request in requests_response.json()] == [
        seeded["newer_cancelled_request_id"],
        seeded["older_open_request_id"],
    ]
    assert cancelled_requests_response.status_code == 200
    assert [request["id"] for request in cancelled_requests_response.json()] == [
        seeded["newer_cancelled_request_id"]
    ]
    assert active_offers_response.status_code == 200
    assert [offer["id"] for offer in active_offers_response.json()] == [seeded["active_offer_id"]]
    assert cancelled_trades_response.status_code == 200
    assert [trade["id"] for trade in cancelled_trades_response.json()] == [
        seeded["cancelled_trade_id"]
    ]
