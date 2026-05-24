from __future__ import annotations

import hmac
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import date
from hashlib import sha256

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import KycIdType, KycProvider, KycStatus, KycVerificationStatus
from app.infrastructure.config import settings
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.security import SecurityService
from app.integrations.youverify import KycProviderRequest, KycProviderResult, LocalKycProvider
from app.main import app
from app.models.kyc_verification import KycVerificationModel
from app.models.outbox_event import OutboxEventModel
from app.services.auth import AuthService, get_auth_service
from app.services.kyc import KycService, get_kyc_service
from tests.conftest import build_user

pytestmark = pytest.mark.anyio


class PendingThenVerifiedProvider:
    """Provider test double that returns pending first, then verified on retrieval."""

    async def verify_identity(self, request: KycProviderRequest) -> KycProviderResult:
        return KycProviderResult(
            provider=KycProvider.LOCAL,
            provider_reference_id="pending-reference",
            status=KycVerificationStatus.PENDING,
            provider_status="pending",
            field_match_summary={"id_type": request.id_type.value},
            rejection_reason=None,
        )

    async def retrieve_identity(
        self,
        *,
        provider_reference_id: str,
        id_type: KycIdType,
    ) -> KycProviderResult:
        return KycProviderResult(
            provider=KycProvider.LOCAL,
            provider_reference_id=provider_reference_id,
            status=KycVerificationStatus.VERIFIED,
            provider_status="verified",
            field_match_summary={
                "id_type": id_type.value,
                "first_name": True,
                "last_name": True,
                "date_of_birth": True,
            },
            rejection_reason=None,
        )


class PartialMatchVerifiedProvider:
    """Provider test double that returns verified with incomplete match coverage."""

    async def verify_identity(self, request: KycProviderRequest) -> KycProviderResult:
        return KycProviderResult(
            provider=KycProvider.LOCAL,
            provider_reference_id="partial-match-reference",
            status=KycVerificationStatus.VERIFIED,
            provider_status="verified",
            field_match_summary={
                "id_type": request.id_type.value,
                "first_name": True,
            },
            rejection_reason=None,
        )

    async def retrieve_identity(
        self,
        *,
        provider_reference_id: str,
        id_type: KycIdType,
    ) -> KycProviderResult:
        return KycProviderResult(
            provider=KycProvider.LOCAL,
            provider_reference_id=provider_reference_id,
            status=KycVerificationStatus.VERIFIED,
            provider_status="verified",
            field_match_summary={
                "id_type": id_type.value,
                "first_name": True,
            },
            rejection_reason=None,
        )


class PendingThenPartialMatchProvider:
    """Provider test double that resolves pending checks into review."""

    async def verify_identity(self, request: KycProviderRequest) -> KycProviderResult:
        return KycProviderResult(
            provider=KycProvider.LOCAL,
            provider_reference_id="pending-review-reference",
            status=KycVerificationStatus.PENDING,
            provider_status="pending",
            field_match_summary={"id_type": request.id_type.value},
            rejection_reason=None,
        )

    async def retrieve_identity(
        self,
        *,
        provider_reference_id: str,
        id_type: KycIdType,
    ) -> KycProviderResult:
        return KycProviderResult(
            provider=KycProvider.LOCAL,
            provider_reference_id=provider_reference_id,
            status=KycVerificationStatus.VERIFIED,
            provider_status="verified",
            field_match_summary={
                "id_type": id_type.value,
                "first_name": True,
            },
            rejection_reason=None,
        )


class PendingProvider:
    """Provider test double that keeps verifications pending."""

    async def verify_identity(self, request: KycProviderRequest) -> KycProviderResult:
        return KycProviderResult(
            provider=KycProvider.LOCAL,
            provider_reference_id="still-pending-reference",
            status=KycVerificationStatus.PENDING,
            provider_status="pending",
            field_match_summary={"id_type": request.id_type.value},
            rejection_reason=None,
        )

    async def retrieve_identity(
        self,
        *,
        provider_reference_id: str,
        id_type: KycIdType,
    ) -> KycProviderResult:
        return KycProviderResult(
            provider=KycProvider.LOCAL,
            provider_reference_id=provider_reference_id,
            status=KycVerificationStatus.PENDING,
            provider_status="pending",
            field_match_summary={"id_type": id_type.value, "polled": True},
            rejection_reason=None,
        )


@pytest.fixture
def security() -> SecurityService:
    return SecurityService()


@pytest.fixture
def auth_service(
    session_factory: async_sessionmaker[AsyncSession],
    security: SecurityService,
) -> AuthService:
    return AuthService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        security=security,
    )


@pytest.fixture
def kyc_service(session_factory: async_sessionmaker[AsyncSession]) -> KycService:
    return KycService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        provider=LocalKycProvider(),
    )


@pytest.fixture
async def client(
    auth_service: AuthService,
    kyc_service: KycService,
) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    app.dependency_overrides[get_kyc_service] = lambda: kyc_service
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as test_client:
        yield test_client
    app.dependency_overrides.clear()


async def create_pending_kyc_user(
    session_factory: async_sessionmaker[AsyncSession],
    security: SecurityService,
    *,
    email: str = "kyc-user@example.com",
    country: str = "NG",
) -> None:
    user = build_user(
        email=email,
        password_hash=security.hash_password("ChangeMe123!"),
        kyc_status=KycStatus.PENDING,
    )
    user = replace(user, country=country)
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        await uow.users.add(user)
        await uow.commit()


async def login(client: AsyncClient, *, email: str = "kyc-user@example.com") -> str:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "ChangeMe123!"},
    )
    assert response.status_code == 200
    return str(response.json()["access_token"])


async def test_submit_kyc_verifies_user_without_storing_raw_identifier(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    security: SecurityService,
) -> None:
    await create_pending_kyc_user(session_factory, security)
    token = await login(client)

    response = await client.post(
        "/api/v1/kyc/submit",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "id_type": "bvn",
            "id_number": "22222222221",
            "first_name": "Chibuoyim",
            "last_name": "Onuigwe",
            "date_of_birth": "1997-05-16",
            "subject_consent": True,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "verified"
    assert body["masked_identifier"] == "22*****2221"
    assert "22222222221" not in str(body)

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        user = await uow.users.get_by_email("kyc-user@example.com")
    assert user.kyc_status is KycStatus.VERIFIED

    async with session_factory() as session:
        verification_result = await session.execute(select(KycVerificationModel))
        verification = verification_result.scalar_one()
        event_result = await session.execute(select(OutboxEventModel))
        events = event_result.scalars().all()

    assert verification.identifier_hash != "22222222221"
    assert verification.masked_identifier == "22*****2221"
    assert {event.event_type for event in events} == {
        "user.kyc_submitted",
        "user.kyc_verified",
    }


async def test_submit_kyc_routes_incomplete_verified_matches_to_requires_review(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    security: SecurityService,
) -> None:
    await create_pending_kyc_user(session_factory, security)
    review_service = KycService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        provider=PartialMatchVerifiedProvider(),
    )
    app.dependency_overrides[get_kyc_service] = lambda: review_service
    token = await login(client)

    response = await client.post(
        "/api/v1/kyc/submit",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "id_type": "bvn",
            "id_number": "22222222221",
            "first_name": "Chibuoyim",
            "last_name": "Onuigwe",
            "date_of_birth": "1997-05-16",
            "subject_consent": True,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "requires_review"
    assert body["field_match_summary"]["policy"]["decision"] == "requires_review"
    assert body["field_match_summary"]["policy"]["required_matches"] == {
        "first_name": True,
        "last_name": None,
        "date_of_birth": None,
    }

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        user = await uow.users.get_by_email("kyc-user@example.com")
        verification = await uow.kyc_verifications.get_latest_for_user(user.id)
    assert user.kyc_status is KycStatus.REQUIRES_REVIEW
    assert verification.status is KycVerificationStatus.REQUIRES_REVIEW
    assert verification.completed_at is not None

    async with session_factory() as session:
        event_result = await session.execute(select(OutboxEventModel))
        events = event_result.scalars().all()
    assert [event.event_type for event in events] == [
        "user.kyc_submitted",
        "user.kyc_requires_review",
    ]


async def test_submit_kyc_rejects_missing_subject_consent(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    security: SecurityService,
) -> None:
    await create_pending_kyc_user(session_factory, security)
    token = await login(client)

    response = await client.post(
        "/api/v1/kyc/submit",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "id_type": "nin",
            "id_number": "12345678901",
            "first_name": "Chibuoyim",
            "last_name": "Onuigwe",
            "date_of_birth": "1997-05-16",
            "subject_consent": False,
        },
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "invariant_violation"


async def test_submit_kyc_rejected_result_updates_user_status(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    security: SecurityService,
) -> None:
    await create_pending_kyc_user(session_factory, security)
    token = await login(client)

    response = await client.post(
        "/api/v1/kyc/submit",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "id_type": "bvn",
            "id_number": "22222222220",
            "first_name": "Chibuoyim",
            "last_name": "Onuigwe",
            "date_of_birth": "1997-05-16",
            "subject_consent": True,
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "rejected"

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        user = await uow.users.get_by_email("kyc-user@example.com")
    assert user.kyc_status is KycStatus.REJECTED


async def test_get_kyc_status_returns_latest_attempt(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    security: SecurityService,
) -> None:
    await create_pending_kyc_user(session_factory, security)
    token = await login(client)
    await client.post(
        "/api/v1/kyc/submit",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "id_type": "vnin",
            "id_number": "AB12345678901234",
            "first_name": "Chibuoyim",
            "last_name": "Onuigwe",
            "date_of_birth": "1997-05-16",
            "subject_consent": True,
        },
    )

    response = await client.get(
        "/api/v1/kyc/status",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["id_type"] == "vnin"


async def test_submit_kyc_rejects_non_ng_users(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    security: SecurityService,
) -> None:
    await create_pending_kyc_user(
        session_factory,
        security,
        email="ghana-user@example.com",
        country="GH",
    )
    token = await login(client, email="ghana-user@example.com")

    response = await client.post(
        "/api/v1/kyc/submit",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "id_type": "bvn",
            "id_number": "22222222221",
            "first_name": "Chibuoyim",
            "last_name": "Onuigwe",
            "date_of_birth": "1997-05-16",
            "subject_consent": True,
        },
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "invariant_violation"


async def test_reconcile_pending_kyc_updates_final_status(
    session_factory: async_sessionmaker[AsyncSession],
    security: SecurityService,
) -> None:
    await create_pending_kyc_user(session_factory, security)
    service = KycService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        provider=PendingThenVerifiedProvider(),
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        user = await uow.users.get_by_email("kyc-user@example.com")

    verification = await service.submit_identity_check(
        user_id=user.id,
        id_type=KycIdType.BVN,
        id_number="22222222221",
        first_name="Chibuoyim",
        last_name="Onuigwe",
        date_of_birth=date(1997, 5, 16),
        subject_consent=True,
    )

    assert verification.status is KycVerificationStatus.PENDING

    completed = await service.reconcile_pending()

    assert completed == 1
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        updated_user = await uow.users.get(user.id)
        updated_verification = await uow.kyc_verifications.get(verification.id)

    assert updated_user.kyc_status is KycStatus.VERIFIED
    assert updated_verification.status is KycVerificationStatus.VERIFIED
    assert updated_verification.completed_at is not None

    async with session_factory() as session:
        event_result = await session.execute(select(OutboxEventModel))
        events = event_result.scalars().all()

    assert [event.event_type for event in events] == [
        "user.kyc_submitted",
        "user.kyc_verified",
    ]


async def test_reconcile_pending_kyc_routes_incomplete_verified_matches_to_review(
    session_factory: async_sessionmaker[AsyncSession],
    security: SecurityService,
) -> None:
    await create_pending_kyc_user(session_factory, security)
    service = KycService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        provider=PendingThenPartialMatchProvider(),
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        user = await uow.users.get_by_email("kyc-user@example.com")

    verification = await service.submit_identity_check(
        user_id=user.id,
        id_type=KycIdType.BVN,
        id_number="22222222221",
        first_name="Chibuoyim",
        last_name="Onuigwe",
        date_of_birth=date(1997, 5, 16),
        subject_consent=True,
    )

    assert verification.status is KycVerificationStatus.PENDING

    completed = await service.reconcile_pending()

    assert completed == 1
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        updated_user = await uow.users.get(user.id)
        updated_verification = await uow.kyc_verifications.get(verification.id)

    assert updated_user.kyc_status is KycStatus.REQUIRES_REVIEW
    assert updated_verification.status is KycVerificationStatus.REQUIRES_REVIEW
    assert updated_verification.completed_at is not None
    assert updated_verification.field_match_summary["policy"]["decision"] == "requires_review"

    async with session_factory() as session:
        event_result = await session.execute(select(OutboxEventModel))
        events = event_result.scalars().all()
    assert [event.event_type for event in events] == [
        "user.kyc_submitted",
        "user.kyc_requires_review",
    ]


async def test_reconcile_pending_kyc_noops_when_provider_still_pending(
    session_factory: async_sessionmaker[AsyncSession],
    security: SecurityService,
) -> None:
    await create_pending_kyc_user(session_factory, security)
    service = KycService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        provider=PendingProvider(),
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        user = await uow.users.get_by_email("kyc-user@example.com")

    verification = await service.submit_identity_check(
        user_id=user.id,
        id_type=KycIdType.BVN,
        id_number="22222222221",
        first_name="Chibuoyim",
        last_name="Onuigwe",
        date_of_birth=date(1997, 5, 16),
        subject_consent=True,
    )

    completed = await service.reconcile_pending()

    assert completed == 0
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        updated_user = await uow.users.get(user.id)
        updated_verification = await uow.kyc_verifications.get(verification.id)

    assert updated_user.kyc_status is KycStatus.PENDING
    assert updated_verification.status is KycVerificationStatus.PENDING
    assert updated_verification.provider_status == verification.provider_status
    assert updated_verification.field_match_summary == verification.field_match_summary
    assert updated_verification.completed_at is None


async def test_youverify_webhook_completes_pending_kyc(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    security: SecurityService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "youverify_webhook_secret", "test-secret")
    await create_pending_kyc_user(session_factory, security)
    service = KycService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        provider=PendingThenVerifiedProvider(),
    )
    app.dependency_overrides[get_kyc_service] = lambda: service
    token = await login(client)
    submit_response = await client.post(
        "/api/v1/kyc/submit",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "id_type": "bvn",
            "id_number": "22222222221",
            "first_name": "Chibuoyim",
            "last_name": "Onuigwe",
            "date_of_birth": "1997-05-16",
            "subject_consent": True,
        },
    )
    assert submit_response.status_code == 201

    raw_payload = json.dumps(
        {
            "event": "identity.completed",
            "data": {
                "id": "pending-reference",
                "status": "verified",
                "validation": {
                    "matches": {
                        "firstName": True,
                        "lastName": True,
                        "dateOfBirth": True,
                    }
                },
            },
        },
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(b"test-secret", raw_payload, sha256).hexdigest()

    response = await client.post(
        "/api/v1/webhooks/kyc/youverify",
        content=raw_payload,
        headers={
            "content-type": "application/json",
            "x-youverify-signature": signature,
        },
    )

    assert response.status_code == 200
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        user = await uow.users.get_by_email("kyc-user@example.com")
        verification = await uow.kyc_verifications.get_latest_for_user(user.id)

    assert user.kyc_status is KycStatus.VERIFIED
    assert verification.status is KycVerificationStatus.VERIFIED
    assert verification.completed_at is not None


async def test_youverify_webhook_rejects_invalid_signature(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "youverify_webhook_secret", "test-secret")

    response = await client.post(
        "/api/v1/webhooks/kyc/youverify",
        json={"data": {"id": "pending-reference", "status": "verified"}},
        headers={"x-youverify-signature": "bad-signature"},
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "authorization_error"
