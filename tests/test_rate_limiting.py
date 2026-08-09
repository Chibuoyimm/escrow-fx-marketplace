from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import UserStatus
from app.domain.exceptions import RateLimitExceededError
from app.infrastructure.config import settings
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.exceptions import InfrastructureError
from app.infrastructure.idempotency import (
    build_idempotency_fingerprint,
    hash_idempotency_key,
)
from app.infrastructure.metrics import REGISTRY
from app.infrastructure.rate_limiting import (
    AUTH_LOGIN,
    MARKETPLACE_MUTATION,
    RateLimitService,
    get_rate_limit_policy,
    hash_rate_limit_key,
    resolve_client_ip,
)
from app.infrastructure.security import SecurityService
from app.main import app
from app.models.rate_limit_bucket import RateLimitBucketModel
from app.services.auth import AuthService, get_auth_service
from tests.conftest import build_user

pytestmark = pytest.mark.anyio


@pytest.fixture
def auth_service(session_factory: async_sessionmaker[AsyncSession]) -> AuthService:
    return AuthService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        security=SecurityService(),
    )


@pytest.fixture
async def client(auth_service: AuthService) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as test_client:
        yield test_client
    app.dependency_overrides.clear()


async def create_login_user(
    session_factory: async_sessionmaker[AsyncSession],
    email: str,
) -> UUID:
    security = SecurityService()
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        user = await uow.users.add(
            build_user(
                email=email,
                password_hash=security.hash_password("ChangeMe123!"),
                status=UserStatus.ACTIVE,
            )
        )
        await uow.commit()
        return user.id


def set_policy_overrides(monkeypatch: pytest.MonkeyPatch, **overrides: dict[str, int]) -> None:
    monkeypatch.setattr(settings, "rate_limit_policy_overrides", overrides)


async def test_failed_login_attempts_are_counted_and_return_problem_details(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_login_user(session_factory, "rate-login@example.com")
    set_policy_overrides(
        monkeypatch,
        **{
            f"{AUTH_LOGIN}.ip": {"limit": 100, "window_seconds": 60},
            f"{AUTH_LOGIN}.account": {"limit": 2, "window_seconds": 60},
        },
    )

    first = await client.post(
        "/api/v1/auth/login",
        json={"email": "RATE-LOGIN@example.com", "password": "WrongPass123!"},
    )
    second = await client.post(
        "/api/v1/auth/login",
        json={"email": "rate-login@example.com", "password": "WrongPass123!"},
    )
    blocked = await client.post(
        "/api/v1/auth/login",
        json={"email": "rate-login@example.com", "password": "WrongPass123!"},
    )

    assert first.status_code == 401
    assert second.status_code == 401
    assert second.headers["ratelimit-remaining"] == "0"
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"].isdigit()
    assert blocked.headers["ratelimit-remaining"] == "0"
    assert blocked.headers["content-type"].startswith("application/problem+json")
    assert blocked.json()["error_code"] == "rate_limited"
    assert blocked.json()["request_id"] == blocked.headers["x-request-id"]


async def test_public_identifier_and_ip_dimensions_are_scoped_independently(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_login_user(session_factory, "rate-a@example.com")
    await create_login_user(session_factory, "rate-b@example.com")
    set_policy_overrides(
        monkeypatch,
        **{
            f"{AUTH_LOGIN}.ip": {"limit": 100, "window_seconds": 60},
            f"{AUTH_LOGIN}.account": {"limit": 1, "window_seconds": 60},
        },
    )

    first_a = await client.post(
        "/api/v1/auth/login",
        json={"email": "rate-a@example.com", "password": "WrongPass123!"},
    )
    first_b = await client.post(
        "/api/v1/auth/login",
        json={"email": "rate-b@example.com", "password": "WrongPass123!"},
    )
    second_a = await client.post(
        "/api/v1/auth/login",
        json={"email": "rate-a@example.com", "password": "WrongPass123!"},
    )

    assert first_a.status_code == 401
    assert first_b.status_code == 401
    assert second_a.status_code == 429


async def test_malformed_auth_requests_consume_ip_capacity(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_policy_overrides(
        monkeypatch,
        **{
            f"{AUTH_LOGIN}.ip": {"limit": 2, "window_seconds": 60},
            f"{AUTH_LOGIN}.account": {"limit": 100, "window_seconds": 60},
        },
    )

    first = await client.post("/api/v1/auth/login", json={"password": "wrong"})
    second = await client.post("/api/v1/auth/login", json={"password": "wrong"})
    blocked = await client.post("/api/v1/auth/login", json={"password": "wrong"})

    assert first.status_code == 422
    assert second.status_code == 422
    assert blocked.status_code == 429


async def test_completed_idempotency_replay_does_not_consume_or_hit_marketplace_limit(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = await create_login_user(session_factory, "rate-replay@example.com")
    set_policy_overrides(
        monkeypatch,
        **{f"{MARKETPLACE_MUTATION}.user": {"limit": 1, "window_seconds": 60}},
    )
    key_hash = hash_idempotency_key("completed-replay")
    operation_scope = "exchange-request.create"
    request_fingerprint = build_idempotency_fingerprint(
        operation_scope=operation_scope,
        payload={},
    )
    current_time = datetime.now(UTC)
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        record = await uow.idempotency_records.claim(
            principal_user_id=user_id,
            operation_scope=operation_scope,
            key_hash=key_hash,
            request_fingerprint=request_fingerprint,
            now=current_time,
        )
        await uow.idempotency_records.complete(
            record_id=record.id,
            response_status_code=201,
            response_body={"id": str(uuid4())},
            now=current_time,
        )
        await uow.commit()

    service = RateLimitService(uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory))
    first = await service.enforce_or_raise(
        policy_name=MARKETPLACE_MUTATION,
        identities={"user": str(user_id)},
    )
    replay = await service.enforce_or_raise(
        policy_name=MARKETPLACE_MUTATION,
        identities={"user": str(user_id)},
        principal_user_id=str(user_id),
        idempotency_scope=operation_scope,
        idempotency_key_hash=key_hash,
        idempotency_request_fingerprint=request_fingerprint,
    )

    assert not first.limited
    assert replay.bypassed_completed_replay
    assert not replay.limited
    assert replay.headers["RateLimit-Remaining"] == "0"


async def test_rate_limit_keys_are_hashed_and_cleanup_is_bounded(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_policy_overrides(
        monkeypatch,
        **{f"{MARKETPLACE_MUTATION}.user": {"limit": 10, "window_seconds": 60}},
    )
    service = RateLimitService(uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory))
    sensitive_identity = "sensitive-user@example.com"
    await service.enforce_or_raise(
        policy_name=MARKETPLACE_MUTATION,
        identities={"user": sensitive_identity},
    )
    await service.enforce_or_raise(
        policy_name=MARKETPLACE_MUTATION,
        identities={"user": "another-sensitive-user@example.com"},
    )

    async with session_factory() as session:
        result = await session.execute(select(RateLimitBucketModel))
        buckets = result.scalars().all()
        assert len(buckets) == 2
        assert all(sensitive_identity not in bucket.key_hash for bucket in buckets)
        assert any(
            bucket.key_hash
            == hash_rate_limit_key(f"{MARKETPLACE_MUTATION}:user:{sensitive_identity}")
            for bucket in buckets
        )
        expired_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.execute(update(RateLimitBucketModel).values(expires_at=expired_at))
        await session.commit()

    cleanup_time = datetime.now(UTC)
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        deleted = await uow.rate_limits.delete_expired(now=cleanup_time, limit=1)
        await uow.commit()

    assert deleted == 1
    async with session_factory() as session:
        result = await session.execute(select(RateLimitBucketModel))
        assert len(result.scalars().all()) == 1


def test_rate_limit_key_uses_the_configured_hmac_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "rate_limit_key_secret", "first-secret")
    first = hash_rate_limit_key("auth.login:account:user@example.com")
    monkeypatch.setattr(settings, "rate_limit_key_secret", "second-secret")
    second = hash_rate_limit_key("auth.login:account:user@example.com")

    assert first != second


def test_direct_peer_is_used_until_trusted_proxy_networks_are_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "trusted_proxy_networks", "")
    assert resolve_client_ip("192.0.2.10", "198.51.100.1") == "192.0.2.10"

    monkeypatch.setattr(settings, "trusted_proxy_networks", "192.0.2.10/32,10.0.0.0/8")
    assert resolve_client_ip("192.0.2.10", "198.51.100.1, 10.1.2.3") == "198.51.100.1"


async def test_disabled_limiter_does_not_open_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "rate_limit_enabled", False)

    def broken_factory() -> Any:
        raise RuntimeError("database unavailable")

    service = RateLimitService(uow_factory=broken_factory)
    decision = await service.enforce_or_raise(
        policy_name=AUTH_LOGIN,
        identities={"ip": "192.0.2.1"},
    )

    assert not decision.limited
    assert not decision.headers


async def test_fail_closed_limiter_hides_storage_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_fail_closed_auth", True)

    def broken_factory() -> Any:
        raise RuntimeError("database unavailable")

    service = RateLimitService(uow_factory=broken_factory)
    with pytest.raises(InfrastructureError) as caught:
        await service.enforce_or_raise(
            policy_name=AUTH_LOGIN,
            identities={"ip": "192.0.2.1"},
        )

    assert getattr(caught.value, "status_code", None) == 503
    assert "database unavailable" not in str(caught.value)


async def test_fail_open_marketplace_limiter_allows_storage_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_fail_closed_marketplace", False)

    def broken_factory() -> Any:
        raise RuntimeError("database unavailable")

    service = RateLimitService(uow_factory=broken_factory)
    decision = await service.enforce_or_raise(
        policy_name=MARKETPLACE_MUTATION,
        identities={"user": str(uuid4())},
    )

    assert not decision.limited
    assert decision.storage_unavailable


def test_policy_overrides_are_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings,
        "rate_limit_policy_overrides",
        {f"{AUTH_LOGIN}.ip": {"limit": 2, "window_seconds": 30}},
    )
    policy = get_rate_limit_policy(AUTH_LOGIN)
    assert policy.rules[0].limit == 2
    assert policy.rules[0].window_seconds == 30

    monkeypatch.setattr(
        settings,
        "rate_limit_policy_overrides",
        {f"{AUTH_LOGIN}.ip": {"limit": 0}},
    )
    with pytest.raises(ValueError):
        get_rate_limit_policy(AUTH_LOGIN)


async def test_rate_limit_metrics_record_rejection_and_storage_failure(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "metrics_enabled", True)
    set_policy_overrides(
        monkeypatch,
        **{
            f"{AUTH_LOGIN}.ip": {"limit": 1, "window_seconds": 60},
            f"{AUTH_LOGIN}.account": {"limit": 100, "window_seconds": 60},
        },
    )
    service = RateLimitService(uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory))
    labels = {"policy": AUTH_LOGIN, "outcome": "rejected"}
    before_rejected = float(REGISTRY.get_sample_value("rate_limit_decisions_total", labels) or 0)

    await service.enforce_or_raise(policy_name=AUTH_LOGIN, identities={"ip": "192.0.2.20"})
    with pytest.raises(RateLimitExceededError):
        await service.enforce_or_raise(policy_name=AUTH_LOGIN, identities={"ip": "192.0.2.20"})

    assert float(REGISTRY.get_sample_value("rate_limit_decisions_total", labels) or 0) == (
        before_rejected + 1
    )

    def broken_factory() -> Any:
        raise RuntimeError("database unavailable")

    storage_labels = {"policy": AUTH_LOGIN, "outcome": "storage_unavailable"}
    before_storage = float(
        REGISTRY.get_sample_value("rate_limit_decisions_total", storage_labels) or 0
    )
    with pytest.raises(InfrastructureError):
        await RateLimitService(uow_factory=broken_factory).enforce_or_raise(
            policy_name=AUTH_LOGIN,
            identities={"ip": "192.0.2.21"},
        )
    assert float(REGISTRY.get_sample_value("rate_limit_decisions_total", storage_labels) or 0) == (
        before_storage + 1
    )
