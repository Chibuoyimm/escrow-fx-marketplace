from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import require_roles
from app.api.exception_handlers import register_exception_handlers
from app.api.router import api_router
from app.domain.auth import AuthenticatedPrincipal
from app.domain.enums import UserRole, UserStatus
from app.infrastructure.config import settings
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.request_context import register_request_context
from app.infrastructure.security import SecurityService
from app.main import app
from app.models.email_verification_token import EmailVerificationTokenModel
from app.models.outbox_event import OutboxEventModel
from app.models.user import UserModel
from app.services.auth import AuthService, get_auth_service, hash_email_verification_token

admin_role_dependency = Depends(require_roles(UserRole.ADMIN))
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


def build_guard_app(auth_service: AuthService) -> FastAPI:
    application = FastAPI()
    register_request_context(application)
    register_exception_handlers(application)
    application.include_router(api_router)
    application.dependency_overrides[get_auth_service] = lambda: auth_service

    @application.get(f"{settings.api_v1_prefix}/admin-only")
    async def admin_only(
        principal: AuthenticatedPrincipal = admin_role_dependency,
    ) -> dict[str, str]:
        return {"email": principal.email}

    return application


async def verification_token_for_email(
    session_factory: async_sessionmaker[AsyncSession],
    email: str,
) -> str:
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        user = await uow.users.get_by_email(email)

    async with session_factory() as session:
        result = await session.execute(
            select(OutboxEventModel)
            .where(
                OutboxEventModel.event_type == "user.email_verification_requested",
                OutboxEventModel.recipient_user_id == user.id,
            )
            .order_by(OutboxEventModel.created_at.desc())
        )
        event = result.scalars().first()

    assert event is not None
    verification_url = event.payload["verify_email_url"]
    token = parse_qs(urlparse(verification_url).query)["token"][0]
    assert isinstance(token, str)
    assert token
    return token


async def verify_registered_email(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    email: str,
) -> None:
    token = await verification_token_for_email(session_factory, email)
    response = await client.post("/api/v1/auth/verify-email", json={"token": token})
    assert response.status_code == 200


async def test_register_user_succeeds(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "customer@example.com",
            "password": "ChangeMe123!",
            "country": "ng",
            "phone": "+2348000000000",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "customer@example.com"
    assert body["country"] == "NG"
    assert body["email_verified_at"] is None
    assert "password_hash" not in body


async def test_register_queues_email_verification(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "verify-queued@example.com",
            "password": "ChangeMe123!",
            "country": "NG",
            "phone": "+2348000000000",
        },
    )

    async with session_factory() as session:
        result = await session.execute(
            select(OutboxEventModel).where(
                OutboxEventModel.event_type == "user.email_verification_requested"
            )
        )
        event = result.scalar_one()

    assert event.payload["email"] == "verify-queued@example.com"
    assert "token=" in event.payload["verify_email_url"]
    assert event.payload["expires_at_display"].endswith(" UTC")
    assert " at " in event.payload["expires_at_display"]
    assert "-" not in event.payload["expires_at_display"]


async def test_register_duplicate_email_returns_conflict(client: AsyncClient) -> None:
    payload = {
        "email": "duplicate@example.com",
        "password": "ChangeMe123!",
        "country": "NG",
        "phone": "+2348000000000",
    }

    first = await client.post("/api/v1/auth/register", json=payload)
    second = await client.post("/api/v1/auth/register", json=payload)

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error_code"] == "conflict"


async def test_register_rejects_non_alpha_country_code(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "invalid-country@example.com",
            "password": "ChangeMe123!",
            "country": "N1",
            "phone": "+2348000000000",
        },
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "validation_error"


async def test_login_succeeds_and_returns_bearer_token(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "login@example.com",
            "password": "ChangeMe123!",
            "country": "NG",
            "phone": "+2348000000000",
        },
    )
    await verify_registered_email(client, session_factory, "login@example.com")

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "login@example.com", "password": "ChangeMe123!"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["expires_in_seconds"] > 0


async def test_login_requires_email_verification(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "unverified-login@example.com",
            "password": "ChangeMe123!",
            "country": "NG",
            "phone": "+2348000000000",
        },
    )

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "unverified-login@example.com", "password": "ChangeMe123!"},
    )

    assert response.status_code == 412
    assert response.json()["error_code"] == "precondition_failed"


async def test_login_invalid_credentials_return_sanitized_error(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "badlogin@example.com",
            "password": "ChangeMe123!",
            "country": "NG",
            "phone": "+2348000000000",
        },
    )

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "badlogin@example.com", "password": "WrongPass123!"},
    )

    assert response.status_code == 401
    assert response.json()["error_code"] == "authentication_error"
    assert response.json()["detail"] == "Invalid authentication credentials."


async def test_verify_email_allows_login(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "verify-login@example.com",
            "password": "ChangeMe123!",
            "country": "NG",
            "phone": "+2348000000000",
        },
    )
    token = await verification_token_for_email(session_factory, "verify-login@example.com")

    verify_response = await client.post("/api/v1/auth/verify-email", json={"token": token})
    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": "verify-login@example.com", "password": "ChangeMe123!"},
    )
    reuse_response = await client.post("/api/v1/auth/verify-email", json={"token": token})

    assert verify_response.status_code == 200
    assert verify_response.json()["token_type"] == "bearer"
    assert verify_response.json()["access_token"]
    assert verify_response.json()["user"]["email_verified_at"] is not None
    assert login_response.status_code == 200
    assert reuse_response.status_code == 412


async def test_verify_email_does_not_support_direct_get_link(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "get-link@example.com",
            "password": "ChangeMe123!",
            "country": "NG",
            "phone": "+2348000000000",
        },
    )
    token = await verification_token_for_email(session_factory, "get-link@example.com")

    response = await client.get(f"/api/v1/auth/verify-email?token={token}")

    assert response.status_code == 405


async def test_verify_email_rejects_expired_token(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "expired-token@example.com",
            "password": "ChangeMe123!",
            "country": "NG",
            "phone": "+2348000000000",
        },
    )
    token = await verification_token_for_email(session_factory, "expired-token@example.com")

    async with session_factory() as session:
        result = await session.execute(
            select(EmailVerificationTokenModel).where(
                EmailVerificationTokenModel.token_hash == hash_email_verification_token(token)
            )
        )
        token_row = result.scalar_one()
        token_row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()

    response = await client.post("/api/v1/auth/verify-email", json={"token": token})

    assert response.status_code == 412
    assert response.json()["error_code"] == "precondition_failed"


async def test_resend_email_verification_queues_another_event(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "resend@example.com",
            "password": "ChangeMe123!",
            "country": "NG",
            "phone": "+2348000000000",
        },
    )

    response = await client.post(
        "/api/v1/auth/resend-verification",
        json={"email": "resend@example.com"},
    )

    async with session_factory() as session:
        result = await session.execute(
            select(OutboxEventModel).where(
                OutboxEventModel.event_type == "user.email_verification_requested"
            )
        )
        events = result.scalars().all()

    assert response.status_code == 202
    assert len(events) == 2


async def test_resend_email_verification_does_not_reveal_unknown_accounts(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/v1/auth/resend-verification",
        json={"email": "missing@example.com"},
    )

    assert response.status_code == 202


async def test_users_me_returns_authenticated_user_profile(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "me@example.com",
            "password": "ChangeMe123!",
            "country": "NG",
            "phone": "+2348000000000",
        },
    )
    await verify_registered_email(client, session_factory, "me@example.com")

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "me@example.com", "password": "ChangeMe123!"},
    )

    response = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )

    assert response.status_code == 200
    assert response.json()["email"] == "me@example.com"


async def test_missing_bearer_token_returns_authentication_error(client: AsyncClient) -> None:
    response = await client.get("/api/v1/users/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error_code"] == "authentication_error"


async def test_invalid_bearer_token_returns_authentication_error(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": "Bearer not-a-real-token"},
    )

    assert response.status_code == 401
    assert response.json()["error_code"] == "authentication_error"


async def test_expired_bearer_token_returns_authentication_error(client: AsyncClient) -> None:
    expired_token = jwt.encode(
        {
            "sub": "expired@example.com",
            "user_id": "00000000-0000-0000-0000-000000000001",
            "role": UserRole.CUSTOMER.value,
            "iss": settings.jwt_issuer,
            "iat": int((datetime.now(UTC) - timedelta(minutes=10)).timestamp()),
            "exp": int((datetime.now(UTC) - timedelta(minutes=5)).timestamp()),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    response = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )

    assert response.status_code == 401
    assert response.json()["error_code"] == "authentication_error"


async def test_role_guard_returns_forbidden_for_non_admin(
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=build_guard_app(auth_service)),
        base_url="http://testserver",
    ) as client:
        await client.post(
            "/api/v1/auth/register",
            json={
                "email": "customer-role@example.com",
                "password": "ChangeMe123!",
                "country": "NG",
                "phone": "+2348000000000",
            },
        )
        await verify_registered_email(client, session_factory, "customer-role@example.com")
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": "customer-role@example.com", "password": "ChangeMe123!"},
        )

        response = await client.get(
            f"{settings.api_v1_prefix}/admin-only",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )

    assert response.status_code == 403
    assert response.json()["error_code"] == "authorization_error"


async def test_role_guard_allows_admin_user(auth_service: AuthService) -> None:
    await auth_service.create_admin(
        email="admin-role@example.com",
        password="ChangeMe123!",
        country="NG",
    )

    async with AsyncClient(
        transport=ASGITransport(app=build_guard_app(auth_service)),
        base_url="http://testserver",
    ) as client:
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin-role@example.com", "password": "ChangeMe123!"},
        )

        response = await client.get(
            f"{settings.api_v1_prefix}/admin-only",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )

    assert response.status_code == 200
    assert response.json()["email"] == "admin-role@example.com"


async def test_login_fails_for_inactive_user(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "inactive@example.com",
            "password": "ChangeMe123!",
            "country": "NG",
            "phone": "+2348000000000",
        },
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        user = await uow.users.get_by_email("inactive@example.com")
        await uow.users.update(
            replace(
                user,
                status=UserStatus.INACTIVE,
                updated_at=datetime.now(UTC),
            )
        )
        await uow.commit()

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "inactive@example.com", "password": "ChangeMe123!"},
    )

    assert response.status_code == 401
    assert response.json()["error_code"] == "authentication_error"


async def test_users_me_fails_when_token_user_is_inactive(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "inactive-me@example.com",
            "password": "ChangeMe123!",
            "country": "NG",
            "phone": "+2348000000000",
        },
    )
    await verify_registered_email(client, session_factory, "inactive-me@example.com")
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "inactive-me@example.com", "password": "ChangeMe123!"},
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        user = await uow.users.get_by_email("inactive-me@example.com")
        await uow.users.update(
            replace(
                user,
                status=UserStatus.INACTIVE,
                updated_at=datetime.now(UTC),
            )
        )
        await uow.commit()

    response = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )

    assert response.status_code == 401
    assert response.json()["error_code"] == "authentication_error"


@pytest.mark.anyio
async def test_passwords_are_stored_as_hashes(
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    plain_password = "ChangeMe123!"
    await auth_service.register_user(
        email="hashcheck@example.com",
        password=plain_password,
        country="NG",
        phone="+2348000000000",
    )

    async with session_factory() as session:
        result = await session.execute(
            select(UserModel).where(UserModel.email == "hashcheck@example.com")
        )
        user_row = result.scalar_one()

    assert user_row.password_hash != plain_password
    assert SecurityService().verify_password(plain_password, user_row.password_hash)
