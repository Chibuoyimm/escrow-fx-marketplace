from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import knockapi
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.entities import User
from app.domain.exceptions import InvariantViolationError
from app.infrastructure.config import settings
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.exceptions import InfrastructureError
from app.integrations.knock import KnockNotificationPreferenceGateway
from app.main import app
from app.models.outbox_event import OutboxEventModel
from app.schemas.notification_preferences import NotificationPreferencesPatch
from app.services.auth import AuthService, get_auth_service
from app.services.notification_preferences import (
    NotificationPreferenceService,
    NotificationPreferenceState,
    get_notification_preference_service,
)
from tests.conftest import build_user

pytestmark = pytest.mark.anyio

PASSWORD = "ChangeMe123!"


class RecordingPreferenceGateway:
    """Provider-neutral test double for API/service behavior."""

    def __init__(self, state: NotificationPreferenceState | None = None) -> None:
        self.state = state
        self.calls: list[tuple[str, object]] = []

    async def upsert_recipient(self, user: User, *, idempotency_key: str) -> None:
        self.calls.append(("upsert", idempotency_key))

    async def get_preferences(
        self, user_id: UUID, *, preference_set_id: str
    ) -> NotificationPreferenceState | None:
        self.calls.append(("get", (user_id, preference_set_id)))
        return self.state

    async def set_preferences(
        self,
        user_id: UUID,
        *,
        preference_set_id: str,
        email_enabled_by_category: dict[str, bool],
        idempotency_key: str,
    ) -> NotificationPreferenceState:
        self.calls.append(
            (
                "set",
                (user_id, preference_set_id, email_enabled_by_category, idempotency_key),
            )
        )
        current = dict(self.state.email_enabled_by_category) if self.state else {}
        current.update(email_enabled_by_category)
        self.state = NotificationPreferenceState(preference_set_id, current)
        return self.state


class FakeKnockUsers:
    """Small SDK-shaped fake that records exact adapter calls."""

    def __init__(self, preferences: object | None = None) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.preferences = preferences
        self.error: Exception | None = None

    async def update(self, *args: object, **kwargs: object) -> None:
        self.calls.append(("update", args, kwargs))
        if self.error is not None:
            raise self.error

    async def get_preferences(self, *args: object, **kwargs: object) -> object | None:
        self.calls.append(("get_preferences", args, kwargs))
        if self.error is not None:
            raise self.error
        return self.preferences

    async def set_preferences(self, *args: object, **kwargs: object) -> object:
        self.calls.append(("set_preferences", args, kwargs))
        if self.error is not None:
            raise self.error
        return {
            "id": args[1],
            "categories": kwargs.get("categories", {}),
        }


class FakeKnockClient:
    def __init__(self, users: FakeKnockUsers) -> None:
        self.users = users
        self.workflows = object()


async def create_authenticated_user(
    session_factory: async_sessionmaker[AsyncSession],
    auth_service: AuthService,
    *,
    email: str = "preferences@example.com",
) -> tuple[UUID, dict[str, str]]:
    security = auth_service._security
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        user = await uow.users.add(
            build_user(email=email, password_hash=security.hash_password(PASSWORD))
        )
        await uow.commit()
    token = await auth_service.login_user(email=email, password=PASSWORD)
    return user.id, {"Authorization": f"Bearer {token.access_token}"}


@pytest.fixture
def auth_service(session_factory: async_sessionmaker[AsyncSession]) -> AuthService:
    return AuthService(uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory))


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
    auth_service: AuthService,
) -> AsyncIterator[AsyncClient]:
    gateway = RecordingPreferenceGateway()
    preference_service = NotificationPreferenceService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        gateway=gateway,
    )
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    app.dependency_overrides[get_notification_preference_service] = lambda: preference_service
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as test_client:
        test_client.gateway = gateway  # type: ignore[attr-defined]
        yield test_client
    app.dependency_overrides.clear()


async def test_get_returns_all_categories_enabled_by_default_and_upserts_first(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id, headers = await create_authenticated_user(session_factory, auth_service)

    response = await client.get("/api/v1/users/me/notification-preferences", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["preference_set_id"] == "default"
    assert body["security"] == {"email_enabled": True, "mutable": False}
    assert body["kyc"] == {"email_enabled": True, "mutable": False}
    assert body["trade"] == {"email_enabled": True, "mutable": False}
    assert body["marketplace"] == {"email_enabled": True, "mutable": True}
    gateway = client.gateway  # type: ignore[attr-defined]
    assert gateway.calls[0][0] == "upsert"
    assert gateway.calls[1] == ("get", (user_id, "default"))


async def test_mandatory_categories_ignore_stale_provider_opt_outs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway = RecordingPreferenceGateway(
        NotificationPreferenceState(
            preference_set_id="default",
            email_enabled_by_category={
                "security": False,
                "kyc": False,
                "trade": False,
                "marketplace": False,
            },
        )
    )
    service = NotificationPreferenceService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        gateway=gateway,
    )
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        user = await uow.users.add(build_user(email="mandatory-preferences@example.com"))
        await uow.commit()

    response = await service.get(user_id=user.id)

    assert response.security.email_enabled is True
    assert response.security.mutable is False
    assert response.kyc.email_enabled is True
    assert response.kyc.mutable is False
    assert response.trade.email_enabled is True
    assert response.trade.mutable is False
    assert response.marketplace.email_enabled is False
    assert response.marketplace.mutable is True


async def test_recipient_upsert_key_changes_with_persisted_user_version(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway = RecordingPreferenceGateway()
    service = NotificationPreferenceService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        gateway=gateway,
    )
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        user = await uow.users.add(build_user(email="versioned-upsert@example.com"))
        await uow.commit()

    await service.get(user_id=user.id)
    first_key = gateway.calls[0][1]
    await service.get(user_id=user.id)
    same_version_key = gateway.calls[2][1]
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        current = await uow.users.get_for_update(user.id)
        await uow.users.update(
            replace(current, updated_at=current.updated_at + timedelta(seconds=1))
        )
        await uow.commit()
    await service.get(user_id=user.id)
    updated_version_key = gateway.calls[4][1]

    assert str(user.id) in str(first_key)
    assert same_version_key == first_key
    assert updated_version_key != first_key


async def test_patch_marketplace_merges_and_creates_no_outbox_event(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id, headers = await create_authenticated_user(session_factory, auth_service)

    response = await client.patch(
        "/api/v1/users/me/notification-preferences",
        headers=headers,
        json={"categories": {"marketplace": {"email_enabled": False}}},
    )

    assert response.status_code == 200
    assert response.json()["marketplace"] == {"email_enabled": False, "mutable": True}
    gateway = client.gateway  # type: ignore[attr-defined]
    assert gateway.calls[0][0] == "upsert"
    assert gateway.calls[1][0] == "set"
    assert gateway.calls[1][1][0] == user_id
    assert gateway.calls[1][1][1] == "default"
    assert gateway.calls[1][1][2] == {"marketplace": False}

    async with session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(OutboxEventModel))
    assert count == 0


async def test_mandatory_category_is_rejected_before_user_or_provider_access(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway = RecordingPreferenceGateway()
    service = NotificationPreferenceService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        gateway=gateway,
    )
    payload = NotificationPreferencesPatch.model_validate(
        {"categories": {"security": {"email_enabled": False}}}
    )

    with pytest.raises(InvariantViolationError):
        await service.update(user_id=uuid4(), payload=payload)

    assert gateway.calls == []


async def test_preference_payload_rejects_empty_and_unknown_fields(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, headers = await create_authenticated_user(session_factory, auth_service)

    empty = await client.patch(
        "/api/v1/users/me/notification-preferences", headers=headers, json={"categories": {}}
    )
    unknown = await client.patch(
        "/api/v1/users/me/notification-preferences",
        headers=headers,
        json={"categories": {"marketplace": {"email_enabled": False, "sms": True}}},
    )
    unknown_category = await client.patch(
        "/api/v1/users/me/notification-preferences",
        headers=headers,
        json={"categories": {"marketing": {"email_enabled": False}}},
    )

    assert empty.status_code == 422
    assert unknown.status_code == 422
    assert unknown_category.status_code == 422


async def test_preferences_require_authentication(
    client: AsyncClient,
) -> None:
    response = await client.get("/api/v1/users/me/notification-preferences")
    assert response.status_code == 401


async def test_knock_gateway_uses_shared_upsert_default_set_and_merge_payload() -> None:
    users = FakeKnockUsers(
        preferences={
            "id": "default",
            "categories": {"marketplace": {"channel_types": {"email": False}}},
        }
    )
    gateway = KnockNotificationPreferenceGateway(client=FakeKnockClient(users))
    user = build_user(email="sdk@example.com")

    await gateway.upsert_recipient(user, idempotency_key="upsert-key")
    state = await gateway.get_preferences(user.id, preference_set_id="default")
    updated = await gateway.set_preferences(
        user.id,
        preference_set_id="default",
        email_enabled_by_category={"marketplace": True},
        idempotency_key="patch-key",
    )

    assert state is not None
    assert state.email_enabled_by_category == {"marketplace": False}
    assert updated.email_enabled_by_category == {"marketplace": True}
    assert [call[0] for call in users.calls] == [
        "update",
        "get_preferences",
        "set_preferences",
    ]
    assert users.calls[1][1] == (str(user.id), "default")
    assert users.calls[2][1] == (str(user.id), "default")
    assert users.calls[2][2] == {
        "_persistence_strategy": "merge",
        "categories": {"marketplace": {"channel_types": {"email": True}}},
        "idempotency_key": "patch-key",
    }


async def test_missing_knock_preference_set_falls_back_to_defaults() -> None:
    users = FakeKnockUsers()
    users.error = knockapi.NotFoundError(
        "missing",
        response=httpx.Response(
            404,
            request=httpx.Request("GET", "https://api.knock.app/preferences"),
        ),
        body={"secret": "do-not-log"},
    )
    gateway = KnockNotificationPreferenceGateway(client=FakeKnockClient(users))

    state = await gateway.get_preferences(uuid4(), preference_set_id="default")

    assert state is None


@pytest.mark.parametrize(
    ("error_type", "status_code"),
    [
        (knockapi.AuthenticationError, 401),
        (knockapi.NotFoundError, 404),
        (knockapi.RateLimitError, 429),
        (knockapi.InternalServerError, 500),
    ],
)
async def test_knock_provider_errors_are_sanitized(
    error_type: type[knockapi.APIStatusError],
    status_code: int,
) -> None:
    users = FakeKnockUsers()
    users.error = error_type(
        "provider secret body",
        response=httpx.Response(
            status_code,
            headers={"x-request-id": "knock-request-123"},
            request=httpx.Request("GET", "https://api.knock.app/preferences"),
        ),
        body={"api_key": "secret"},
    )
    gateway = KnockNotificationPreferenceGateway(client=FakeKnockClient(users))

    with pytest.raises(InfrastructureError) as exc_info:
        await gateway.set_preferences(
            uuid4(),
            preference_set_id="default",
            email_enabled_by_category={"marketplace": False},
            idempotency_key="patch-key",
        )

    assert exc_info.value.status_code == 503
    assert "secret" not in exc_info.value.detail


async def test_non_knock_provider_is_unavailable_for_preferences(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: async_sessionmaker[AsyncSession],
    auth_service: AuthService,
) -> None:
    monkeypatch.setattr(settings, "notification_provider", "logging")
    _, headers = await create_authenticated_user(session_factory, auth_service)

    app.dependency_overrides[get_auth_service] = lambda: auth_service
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.get(
                "/api/v1/users/me/notification-preferences",
                headers=headers,
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["detail"] == "The service could not complete the request."


@pytest.mark.parametrize("error_kind", ["timeout", "connection"])
async def test_knock_connection_failures_are_sanitized(error_kind: str) -> None:
    users = FakeKnockUsers()
    request = httpx.Request("GET", "https://api.knock.app/preferences")
    if error_kind == "timeout":
        users.error = knockapi.APITimeoutError(request)
    else:
        users.error = knockapi.APIConnectionError(request=request)
    gateway = KnockNotificationPreferenceGateway(client=FakeKnockClient(users))

    with pytest.raises(InfrastructureError) as exc_info:
        await gateway.set_preferences(
            uuid4(),
            preference_set_id="default",
            email_enabled_by_category={"marketplace": False},
            idempotency_key="patch-key",
        )

    assert exc_info.value.status_code == 503


async def test_repeated_identical_patch_is_safe_and_has_no_outbox(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, headers = await create_authenticated_user(session_factory, auth_service)
    payload = {"categories": {"marketplace": {"email_enabled": False}}}

    first = await client.patch(
        "/api/v1/users/me/notification-preferences", headers=headers, json=payload
    )
    second = await client.patch(
        "/api/v1/users/me/notification-preferences", headers=headers, json=payload
    )

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
