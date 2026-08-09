from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import (
    CorridorStatus,
    CurrencyStatus,
    ExchangeOfferStatus,
    ExchangeRequestStatus,
    KycStatus,
    UserStatus,
)
from app.domain.exceptions import InvariantViolationError
from app.infrastructure.config import settings
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.idempotency import build_idempotency_request
from app.infrastructure.rate_limiting import MARKETPLACE_MUTATION
from app.infrastructure.security import SecurityService
from app.main import app
from app.models.exchange_offer import ExchangeOfferModel
from app.models.exchange_request import ExchangeRequestModel
from app.models.idempotency_record import IdempotencyRecordModel
from app.models.outbox_event import OutboxEventModel
from app.models.trade_contract import TradeContractModel
from app.services.auth import AuthService, get_auth_service
from app.services.exchange_offer import ExchangeOfferService, get_exchange_offer_service
from app.services.exchange_request import ExchangeRequestService, get_exchange_request_service
from app.services.trade import TradeService, get_trade_service
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
) -> tuple[UUID, dict[str, str]]:
    security = SecurityService()
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        user = await uow.users.add(
            build_user(
                email=email,
                password_hash=security.hash_password(PASSWORD),
                kyc_status=KycStatus.VERIFIED,
                status=UserStatus.ACTIVE,
            )
        )
        await uow.commit()
    login = await auth_service.login_user(email=email, password=PASSWORD)
    return user.id, {"Authorization": f"Bearer {login.access_token}"}


async def seed_request(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    request_status: ExchangeRequestStatus = ExchangeRequestStatus.REQUEST_OPEN,
    offer_owner: UUID | None = None,
    expires_at: datetime | None = None,
    from_code: str = "USD",
    to_code: str = "NGN",
) -> dict[str, UUID]:
    security = SecurityService()
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        creator = await uow.users.add(
            build_user(
                email=f"idempotency-creator-{uuid4()}@example.com",
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
        request = await uow.exchange_requests.add(
            build_exchange_request(
                creator_user_id=creator.id,
                from_currency_id=usd.id,
                to_currency_id=ngn.id,
                status=request_status,
                expires_at=expires_at or datetime.now(UTC) + timedelta(hours=1),
            )
        )
        result = {"creator_id": creator.id, "request_id": request.id}
        if offer_owner is not None:
            offer = await uow.exchange_offers.add(
                build_exchange_offer(
                    request_id=request.id,
                    offer_user_id=offer_owner,
                    status=ExchangeOfferStatus.ACTIVE,
                )
            )
            result["offer_id"] = offer.id
        await uow.commit()
    return result


def request_payload(*, preferred_rate: str = "1500") -> dict[str, str]:
    return {
        "from_currency_code": "USD",
        "to_currency_code": "NGN",
        "from_amount": "100",
        "preferred_rate": preferred_rate,
        "min_rate": "1450",
    }


async def test_create_request_replays_and_rejects_payload_reuse(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        usd = await uow.currencies.add(build_currency(code="USD"))
        ngn = await uow.currencies.add(build_currency(code="NGN"))
        await uow.corridors.add(build_corridor(from_currency_id=usd.id, to_currency_id=ngn.id))
        await uow.commit()
    _, headers = await create_user_and_token(
        session_factory, auth_service, email="idempotency-create@example.com"
    )
    headers = {**headers, "Idempotency-Key": "create-request-1"}

    first = await client.post("/api/v1/exchange-requests", headers=headers, json=request_payload())
    replay = await client.post(
        "/api/v1/exchange-requests",
        headers=headers,
        json=request_payload(preferred_rate="1500.0"),
    )
    conflict = await client.post(
        "/api/v1/exchange-requests",
        headers=headers,
        json=request_payload(preferred_rate="1510"),
    )

    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == "conflict"
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ExchangeRequestModel)) == 1
        assert await session.scalar(select(func.count()).select_from(OutboxEventModel)) == 1
        assert await session.scalar(select(func.count()).select_from(IdempotencyRecordModel)) == 1


async def test_exact_completed_create_replay_bypasses_an_exhausted_quota_but_changed_payload_does_not(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        usd = await uow.currencies.add(build_currency(code="USD"))
        ngn = await uow.currencies.add(build_currency(code="NGN"))
        await uow.corridors.add(build_corridor(from_currency_id=usd.id, to_currency_id=ngn.id))
        await uow.commit()
    _, headers = await create_user_and_token(
        session_factory, auth_service, email="rate-limit-idempotency-create@example.com"
    )
    headers = {**headers, "Idempotency-Key": "rate-limit-create-1"}
    monkeypatch.setattr(
        settings,
        "rate_limit_policy_overrides",
        {f"{MARKETPLACE_MUTATION}.user": {"limit": 1, "window_seconds": 60}},
    )

    first = await client.post("/api/v1/exchange-requests", headers=headers, json=request_payload())
    replay = await client.post(
        "/api/v1/exchange-requests",
        headers=headers,
        json=request_payload(preferred_rate="1500.0"),
    )
    changed = await client.post(
        "/api/v1/exchange-requests",
        headers=headers,
        json=request_payload(preferred_rate="1510"),
    )

    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json() == first.json()
    assert changed.status_code == 429
    assert changed.json()["error_code"] == "rate_limited"


async def test_authentication_and_schema_failures_do_not_reserve_keys(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    unauthenticated = await client.post(
        "/api/v1/exchange-requests",
        headers={"Idempotency-Key": "unauthenticated-1"},
        json=request_payload(),
    )

    assert unauthenticated.status_code == 401

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        usd = await uow.currencies.add(build_currency(code="USD"))
        ngn = await uow.currencies.add(build_currency(code="NGN"))
        await uow.corridors.add(build_corridor(from_currency_id=usd.id, to_currency_id=ngn.id))
        await uow.commit()

    _, headers = await create_user_and_token(
        session_factory, auth_service, email="idempotency-schema@example.com"
    )
    headers["Idempotency-Key"] = "schema-failure-1"
    invalid_schema = await client.post(
        "/api/v1/exchange-requests",
        headers=headers,
        json={
            "from_currency_code": "USD",
            "to_currency_code": "NGN",
            "from_amount": "100",
            "min_rate": "1450",
        },
    )
    valid_after_schema_failure = await client.post(
        "/api/v1/exchange-requests",
        headers=headers,
        json=request_payload(),
    )

    assert invalid_schema.status_code == 422
    assert valid_after_schema_failure.status_code == 201
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(IdempotencyRecordModel)) == 1
        assert await session.scalar(select(func.count()).select_from(ExchangeRequestModel)) == 1


def test_idempotency_key_boundaries() -> None:
    principal_user_id = uuid4()

    accepted = build_idempotency_request(
        principal_user_id=principal_user_id,
        key="a" * 128,
        operation_scope="exchange-request.create",
        payload={},
    )

    assert accepted is not None
    for invalid_key in ("a" * 129, "contains whitespace", "é"):
        with pytest.raises(InvariantViolationError):
            build_idempotency_request(
                principal_user_id=principal_user_id,
                key=invalid_key,
                operation_scope="exchange-request.create",
                payload={},
            )


async def test_idempotency_is_scoped_to_principal_and_validation_does_not_poison_key(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        usd = await uow.currencies.add(build_currency(code="USD"))
        ngn = await uow.currencies.add(build_currency(code="NGN"))
        await uow.corridors.add(build_corridor(from_currency_id=usd.id, to_currency_id=ngn.id))
        await uow.commit()
    _, first_headers = await create_user_and_token(
        session_factory, auth_service, email="idempotency-principal-1@example.com"
    )
    _, second_headers = await create_user_and_token(
        session_factory, auth_service, email="idempotency-principal-2@example.com"
    )
    first_headers = {**first_headers, "Idempotency-Key": "shared-key"}
    second_headers = {**second_headers, "Idempotency-Key": "shared-key"}

    invalid = await client.post(
        "/api/v1/exchange-requests",
        headers={**first_headers, "Idempotency-Key": "bad key"},
        json=request_payload(),
    )
    first = await client.post(
        "/api/v1/exchange-requests",
        headers=first_headers,
        json={**request_payload(), "from_currency_code": "USD", "to_currency_code": "USD"},
    )
    recovered = await client.post(
        "/api/v1/exchange-requests",
        headers=first_headers,
        json=request_payload(),
    )
    second = await client.post(
        "/api/v1/exchange-requests",
        headers=second_headers,
        json=request_payload(),
    )

    assert invalid.status_code == 422
    assert first.status_code == 422
    assert recovered.status_code == second.status_code == 201
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ExchangeRequestModel)) == 2
        assert await session.scalar(select(func.count()).select_from(IdempotencyRecordModel)) == 2


async def test_relist_replays_without_creating_a_second_successor(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "rate_limit_policy_overrides",
        {f"{MARKETPLACE_MUTATION}.user": {"limit": 1, "window_seconds": 60}},
    )
    omitted_seed = await seed_request(
        session_factory,
        request_status=ExchangeRequestStatus.CANCELLED,
    )
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        omitted_creator = await uow.users.get(omitted_seed["creator_id"])
    omitted_headers = await _login_headers(auth_service, omitted_creator.email)
    omitted_headers["Idempotency-Key"] = "relist-omitted"
    omitted_path = f"/api/v1/exchange-requests/{omitted_seed['request_id']}/relist"

    omitted_first = await client.post(omitted_path, headers=omitted_headers)
    omitted_replay = await client.post(omitted_path, headers=omitted_headers)

    explicit_null_seed = await seed_request(
        session_factory,
        request_status=ExchangeRequestStatus.CANCELLED,
        from_code="CAD",
        to_code="GBP",
    )
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        explicit_null_creator = await uow.users.get(explicit_null_seed["creator_id"])
    explicit_null_headers = await _login_headers(auth_service, explicit_null_creator.email)
    explicit_null_headers["Idempotency-Key"] = "relist-explicit-null"
    explicit_null_path = f"/api/v1/exchange-requests/{explicit_null_seed['request_id']}/relist"

    explicit_null_first = await client.post(
        explicit_null_path,
        headers=explicit_null_headers,
        json={"min_rate": None},
    )
    explicit_null_replay = await client.post(
        explicit_null_path,
        headers=explicit_null_headers,
        json={"min_rate": None},
    )

    conflict_seed = await seed_request(
        session_factory,
        request_status=ExchangeRequestStatus.CANCELLED,
        from_code="EUR",
        to_code="JPY",
    )
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        conflict_creator = await uow.users.get(conflict_seed["creator_id"])
    conflict_headers = await _login_headers(auth_service, conflict_creator.email)
    conflict_headers["Idempotency-Key"] = "relist-presence"
    conflict_path = f"/api/v1/exchange-requests/{conflict_seed['request_id']}/relist"

    conflict_first = await client.post(conflict_path, headers=conflict_headers, json={})
    conflict_response = await client.post(
        conflict_path,
        headers=conflict_headers,
        json={"min_rate": None},
    )

    assert omitted_first.status_code == omitted_replay.status_code == 201
    assert omitted_first.json() == omitted_replay.json()
    assert explicit_null_first.status_code == explicit_null_replay.status_code == 201
    assert explicit_null_first.json() == explicit_null_replay.json()
    assert explicit_null_first.json()["min_rate"] is None
    assert conflict_first.status_code == 201
    assert conflict_response.status_code == 429
    assert conflict_response.json()["error_code"] == "rate_limited"
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ExchangeRequestModel)) == 6
        assert await session.scalar(select(func.count()).select_from(OutboxEventModel)) == 3
        assert await session.scalar(select(func.count()).select_from(IdempotencyRecordModel)) == 3


async def test_offer_creation_replays_without_duplicate_offer_or_event(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await seed_request(session_factory)
    _, headers = await create_user_and_token(
        session_factory, auth_service, email="idempotency-offer@example.com"
    )
    headers["Idempotency-Key"] = "offer-1"
    path = f"/api/v1/exchange-requests/{seeded['request_id']}/offers"

    first = await client.post(path, headers=headers, json={"offered_rate": "1490"})
    replay = await client.post(path, headers=headers, json={"offered_rate": "1490"})

    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    second_seed = await seed_request(session_factory, from_code="CAD", to_code="GBP")
    second = await client.post(
        f"/api/v1/exchange-requests/{second_seed['request_id']}/offers",
        headers=headers,
        json={"offered_rate": "1490"},
    )
    assert second.status_code == 201
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ExchangeOfferModel)) == 2
        assert await session.scalar(select(func.count()).select_from(OutboxEventModel)) == 2


async def test_exact_completed_offer_replay_bypasses_an_exhausted_quota(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = await seed_request(session_factory)
    _, headers = await create_user_and_token(
        session_factory, auth_service, email="rate-limit-idempotency-offer@example.com"
    )
    headers["Idempotency-Key"] = "rate-limit-offer-1"
    monkeypatch.setattr(
        settings,
        "rate_limit_policy_overrides",
        {f"{MARKETPLACE_MUTATION}.user": {"limit": 1, "window_seconds": 60}},
    )
    path = f"/api/v1/exchange-requests/{seeded['request_id']}/offers"

    first = await client.post(path, headers=headers, json={"offered_rate": "1490"})
    replay = await client.post(path, headers=headers, json={"offered_rate": "1490.0"})

    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json() == first.json()


async def test_malformed_idempotency_key_still_consumes_marketplace_capacity(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        usd = await uow.currencies.add(build_currency(code="USD"))
        ngn = await uow.currencies.add(build_currency(code="NGN"))
        await uow.corridors.add(build_corridor(from_currency_id=usd.id, to_currency_id=ngn.id))
        await uow.commit()
    _, headers = await create_user_and_token(
        session_factory, auth_service, email="rate-limit-malformed-key@example.com"
    )
    monkeypatch.setattr(
        settings,
        "rate_limit_policy_overrides",
        {f"{MARKETPLACE_MUTATION}.user": {"limit": 1, "window_seconds": 60}},
    )

    malformed = await client.post(
        "/api/v1/exchange-requests",
        headers={**headers, "Idempotency-Key": "malformed key"},
        json=request_payload(),
    )
    blocked = await client.post(
        "/api/v1/exchange-requests",
        headers={**headers, "Idempotency-Key": "valid-key-after-malformed"},
        json=request_payload(),
    )

    assert malformed.status_code == 422
    assert malformed.json()["error_code"] == "invariant_violation"
    assert blocked.status_code == 429
    assert blocked.json()["error_code"] == "rate_limited"


async def test_invalid_idempotency_preflight_body_still_consumes_marketplace_capacity(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        usd = await uow.currencies.add(build_currency(code="USD"))
        ngn = await uow.currencies.add(build_currency(code="NGN"))
        await uow.corridors.add(build_corridor(from_currency_id=usd.id, to_currency_id=ngn.id))
        await uow.commit()
    _, headers = await create_user_and_token(
        session_factory, auth_service, email="rate-limit-invalid-body@example.com"
    )
    headers = {**headers, "Idempotency-Key": "invalid-body-key"}
    monkeypatch.setattr(
        settings,
        "rate_limit_policy_overrides",
        {f"{MARKETPLACE_MUTATION}.user": {"limit": 1, "window_seconds": 60}},
    )

    invalid = await client.post(
        "/api/v1/exchange-requests",
        headers=headers,
        json={"from_currency_code": "USD"},
    )
    blocked = await client.post(
        "/api/v1/exchange-requests",
        headers=headers,
        json=request_payload(),
    )

    assert invalid.status_code == 422
    assert invalid.json()["error_code"] == "validation_error"
    assert blocked.status_code == 429
    assert blocked.json()["error_code"] == "rate_limited"


async def test_cancel_withdraw_reject_and_accept_are_replayable(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "rate_limit_policy_overrides",
        {f"{MARKETPLACE_MUTATION}.user": {"limit": 1, "window_seconds": 60}},
    )
    cancel_seed = await seed_request(session_factory)
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        cancel_owner = await uow.users.get(cancel_seed["creator_id"])
    cancel_headers = await _login_headers(auth_service, cancel_owner.email)
    cancel_headers["Idempotency-Key"] = "cancel-1"
    cancel_path = f"/api/v1/exchange-requests/{cancel_seed['request_id']}/cancel"
    cancel_first = await client.post(cancel_path, headers=cancel_headers)
    cancel_replay = await client.post(cancel_path, headers=cancel_headers)
    assert cancel_first.status_code == cancel_replay.status_code == 200
    assert cancel_first.json() == cancel_replay.json()

    withdraw_owner_id, withdraw_headers = await create_user_and_token(
        session_factory, auth_service, email="idempotency-withdraw@example.com"
    )
    withdraw_seed = await seed_request(
        session_factory,
        offer_owner=withdraw_owner_id,
        from_code="CAD",
        to_code="GBP",
    )
    withdraw_headers["Idempotency-Key"] = "withdraw-1"
    withdraw_path = f"/api/v1/offers/{withdraw_seed['offer_id']}/withdraw"
    withdraw_first = await client.post(withdraw_path, headers=withdraw_headers)
    withdraw_replay = await client.post(withdraw_path, headers=withdraw_headers)
    assert withdraw_first.status_code == withdraw_replay.status_code == 200
    assert withdraw_first.json() == withdraw_replay.json()

    reject_owner_id, reject_headers = await create_user_and_token(
        session_factory, auth_service, email="idempotency-reject-owner@example.com"
    )
    reject_seed = await seed_request(
        session_factory,
        offer_owner=reject_owner_id,
        from_code="EUR",
        to_code="JPY",
    )
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        reject_owner = await uow.users.get(reject_seed["creator_id"])
    reject_headers = await _login_headers(auth_service, reject_owner.email)
    reject_headers["Idempotency-Key"] = "reject-1"
    reject_path = f"/api/v1/offers/{reject_seed['offer_id']}/reject"
    reject_first = await client.post(reject_path, headers=reject_headers)
    reject_replay = await client.post(reject_path, headers=reject_headers)
    assert reject_first.status_code == reject_replay.status_code == 200
    assert reject_first.json() == reject_replay.json()

    accept_owner_id, _ = await create_user_and_token(
        session_factory, auth_service, email="idempotency-accept-owner@example.com"
    )
    accept_seed = await seed_request(
        session_factory,
        offer_owner=accept_owner_id,
        from_code="AUD",
        to_code="CHF",
    )
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        accept_owner = await uow.users.get(accept_seed["creator_id"])
    accept_headers = await _login_headers(auth_service, accept_owner.email)
    accept_headers["Idempotency-Key"] = "accept-1"
    accept_path = f"/api/v1/offers/{accept_seed['offer_id']}/accept"
    accept_first = await client.post(accept_path, headers=accept_headers)
    accept_replay = await client.post(accept_path, headers=accept_headers)
    assert accept_first.status_code == accept_replay.status_code == 200
    assert accept_first.json() == accept_replay.json()

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(TradeContractModel)) == 1
        assert await session.scalar(select(func.count()).select_from(IdempotencyRecordModel)) == 4


async def _login_headers(auth_service: AuthService, email: str) -> dict[str, str]:
    login = await auth_service.login_user(email=email, password=PASSWORD)
    return {"Authorization": f"Bearer {login.access_token}"}
