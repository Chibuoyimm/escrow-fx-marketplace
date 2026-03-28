from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import CorridorStatus, CurrencyStatus, RailStatus
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from app.main import app
from app.services.auth import AuthService, get_auth_service
from app.services.reference_data import ReferenceDataService, get_reference_data_service
from tests.conftest import build_corridor, build_corridor_rail, build_currency

pytestmark = pytest.mark.anyio


@pytest.fixture
def auth_service(session_factory: async_sessionmaker[AsyncSession]) -> AuthService:
    return AuthService(uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory))


@pytest.fixture
def reference_data_service(
    session_factory: async_sessionmaker[AsyncSession],
) -> ReferenceDataService:
    return ReferenceDataService(uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory))


@pytest.fixture
async def client(
    auth_service: AuthService,
    reference_data_service: ReferenceDataService,
) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    app.dependency_overrides[get_reference_data_service] = lambda: reference_data_service
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as test_client:
        yield test_client
    app.dependency_overrides.clear()


async def seed_reference_entities(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, UUID]:
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        usd = await uow.currencies.add(build_currency(code="USD"))
        ngn = await uow.currencies.add(build_currency(code="NGN"))
        gbp = await uow.currencies.add(build_currency(code="GBP", status=CurrencyStatus.INACTIVE))

        active_corridor = await uow.corridors.add(
            build_corridor(from_currency_id=usd.id, to_currency_id=ngn.id)
        )
        inactive_corridor = await uow.corridors.add(
            build_corridor(
                from_currency_id=ngn.id,
                to_currency_id=usd.id,
                status=CorridorStatus.INACTIVE,
            )
        )

        await uow.corridor_rails.add(
            build_corridor_rail(corridor_id=active_corridor.id, priority_order=1)
        )
        await uow.corridor_rails.add(
            build_corridor_rail(
                corridor_id=active_corridor.id,
                priority_order=2,
                status=RailStatus.INACTIVE,
            )
        )
        await uow.commit()

    return {
        "active_corridor_id": active_corridor.id,
        "inactive_corridor_id": inactive_corridor.id,
        "inactive_currency_id": gbp.id,
    }


async def authenticate(client: AsyncClient) -> dict[str, str]:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "customer@example.com",
            "password": "ChangeMe123!",
            "country": "NG",
            "phone": "+2348000000000",
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "customer@example.com", "password": "ChangeMe123!"},
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_list_currencies_returns_only_active_records(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_reference_entities(session_factory)

    response = await client.get("/api/v1/currencies")

    assert response.status_code == 200
    assert [currency["code"] for currency in response.json()] == ["NGN", "USD"]


async def test_get_currency_by_code_accepts_lowercase_input(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_reference_entities(session_factory)

    response = await client.get("/api/v1/currencies/ngn")

    assert response.status_code == 200
    assert response.json()["code"] == "NGN"


async def test_get_currency_returns_not_found_for_inactive_code(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_reference_entities(session_factory)

    response = await client.get("/api/v1/currencies/gbp")

    assert response.status_code == 404
    assert response.json()["error_code"] == "not_found"


async def test_corridor_endpoints_require_authentication(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    references = await seed_reference_entities(session_factory)

    response = await client.get(f"/api/v1/corridors/{references['active_corridor_id']}")

    assert response.status_code == 401
    assert response.json()["error_code"] == "authentication_error"


async def test_list_corridors_returns_active_corridors_for_authenticated_users(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_reference_entities(session_factory)
    headers = await authenticate(client)

    response = await client.get("/api/v1/corridors", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["from_currency_code"] == "USD"
    assert body[0]["to_currency_code"] == "NGN"
    assert body[0]["rails"] == [
        {
            "flow_type": "funding",
            "priority_order": 1,
            "provider": "paystack",
            "method": "bank_transfer",
            "status": "active",
        }
    ]
    assert "from_currency_id" not in body[0]
    assert "to_currency_id" not in body[0]


async def test_get_corridor_by_id_returns_active_corridor(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    references = await seed_reference_entities(session_factory)
    headers = await authenticate(client)

    response = await client.get(
        f"/api/v1/corridors/{references['active_corridor_id']}",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["id"] == str(references["active_corridor_id"])


async def test_get_corridor_by_currency_pair_returns_active_corridor(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_reference_entities(session_factory)
    headers = await authenticate(client)

    response = await client.get("/api/v1/corridors/usd/ngn", headers=headers)

    assert response.status_code == 200
    assert response.json()["from_currency_code"] == "USD"
    assert response.json()["to_currency_code"] == "NGN"


async def test_inactive_corridors_are_hidden_from_both_lookup_styles(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    references = await seed_reference_entities(session_factory)
    headers = await authenticate(client)

    by_id_response = await client.get(
        f"/api/v1/corridors/{references['inactive_corridor_id']}",
        headers=headers,
    )
    by_pair_response = await client.get("/api/v1/corridors/ngn/usd", headers=headers)

    assert by_id_response.status_code == 404
    assert by_pair_response.status_code == 404
    assert by_id_response.json()["error_code"] == "not_found"
    assert by_pair_response.json()["error_code"] == "not_found"
