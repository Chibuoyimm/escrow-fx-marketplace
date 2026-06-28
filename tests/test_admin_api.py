from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.entities import KycVerification
from app.domain.enums import (
    CurrencyStatus,
    ExchangeOfferStatus,
    ExchangeRequestStatus,
    KycIdType,
    KycProvider,
    KycStatus,
    KycVerificationStatus,
    OutboxEventStatus,
    TradeContractStatus,
    UserRole,
    UserStatus,
)
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.security import SecurityService
from app.main import app
from app.models.outbox_event import OutboxEventModel
from app.services.admin import AdminService, get_admin_service
from app.services.auth import AuthService, get_auth_service
from app.services.kyc import KycService, get_kyc_service
from app.services.outbox import build_outbox_event
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
def kyc_service(session_factory: async_sessionmaker[AsyncSession]) -> KycService:
    return KycService(uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory))


@pytest.fixture
async def client(
    auth_service: AuthService,
    admin_service: AdminService,
    kyc_service: KycService,
) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    app.dependency_overrides[get_admin_service] = lambda: admin_service
    app.dependency_overrides[get_kyc_service] = lambda: kyc_service
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


async def seed_review_kyc_verification(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    email: str = "review-user@example.com",
) -> tuple[str, str]:
    security = SecurityService()
    now = datetime.now(UTC)
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        user = await uow.users.add(
            build_user(
                email=email,
                password_hash=security.hash_password(PASSWORD),
                kyc_status=KycStatus.REQUIRES_REVIEW,
            )
        )
        verification = await uow.kyc_verifications.add(
            KycVerification(
                id=uuid4(),
                user_id=user.id,
                provider=KycProvider.LOCAL,
                provider_reference_id=f"review-{uuid4()}",
                id_type=KycIdType.BVN,
                masked_identifier="22*****2221",
                identifier_hash="hashed-identifier",
                status=KycVerificationStatus.REQUIRES_REVIEW,
                provider_status="verified",
                field_match_summary={
                    "first_name": True,
                    "policy": {
                        "decision": "requires_review",
                        "reason": "required_identity_fields_incomplete",
                        "required_matches": {
                            "first_name": True,
                            "last_name": None,
                            "date_of_birth": None,
                        },
                    },
                },
                review_events=[],
                rejection_reason=None,
                consented_at=now,
                submitted_at=now,
                completed_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        await uow.commit()

    return str(user.id), str(verification.id)


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


async def test_admin_lists_outbox_events_with_filters(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    admin_headers, _ = await create_user_and_token(
        session_factory,
        auth_service,
        email="event-admin@example.com",
        role=UserRole.ADMIN,
    )
    seeded = await seed_admin_marketplace_data(session_factory)

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        pending_event = await uow.outbox_events.add(
            build_outbox_event(
                event_type="exchange_offer.created",
                aggregate_type="exchange_offer",
                aggregate_id=UUID(seeded["active_offer_id"]),
                recipient_user_id=None,
                payload={"offer_id": seeded["active_offer_id"]},
            )
        )
        failed_event = await uow.outbox_events.add(
            build_outbox_event(
                event_type="trade_contract.locked",
                aggregate_type="trade_contract",
                aggregate_id=UUID(seeded["trade_id"]),
                recipient_user_id=None,
                payload={"trade_contract_id": seeded["trade_id"]},
            )
        )
        failed_event = replace(
            failed_event,
            status=OutboxEventStatus.FAILED,
            last_error="provider timeout",
        )
        assert uow.session is not None
        await uow.session.execute(
            update(OutboxEventModel)
            .where(OutboxEventModel.id == failed_event.id)
            .values(status=failed_event.status, last_error=failed_event.last_error)
        )
        await uow.commit()

    all_response = await client.get("/api/v1/admin/events", headers=admin_headers)
    pending_response = await client.get(
        "/api/v1/admin/events?status=pending",
        headers=admin_headers,
    )
    type_response = await client.get(
        "/api/v1/admin/events?event_type=trade_contract.locked",
        headers=admin_headers,
    )

    assert all_response.status_code == 200
    assert {event["id"] for event in all_response.json()} == {
        str(pending_event.id),
        str(failed_event.id),
    }
    assert pending_response.status_code == 200
    assert [event["id"] for event in pending_response.json()] == [str(pending_event.id)]
    assert type_response.status_code == 200
    assert [event["id"] for event in type_response.json()] == [str(failed_event.id)]
    assert type_response.json()[0]["last_error"] == "provider timeout"


async def test_admin_lists_and_gets_kyc_verifications(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    admin_headers, _ = await create_user_and_token(
        session_factory,
        auth_service,
        email="kyc-admin@example.com",
        role=UserRole.ADMIN,
    )
    _, verification_id = await seed_review_kyc_verification(session_factory)

    list_response = await client.get(
        "/api/v1/admin/kyc?status=requires_review",
        headers=admin_headers,
    )
    get_response = await client.get(
        f"/api/v1/admin/kyc/{verification_id}",
        headers=admin_headers,
    )

    assert list_response.status_code == 200
    assert [verification["id"] for verification in list_response.json()] == [verification_id]
    assert get_response.status_code == 200
    assert get_response.json()["id"] == verification_id
    assert get_response.json()["status"] == "requires_review"
    assert get_response.json()["review_events"] == []


async def test_admin_can_add_review_note(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    admin_headers, admin_user_id = await create_user_and_token(
        session_factory,
        auth_service,
        email="note-kyc-admin@example.com",
        role=UserRole.ADMIN,
    )
    _, verification_id = await seed_review_kyc_verification(session_factory)

    response = await client.post(
        f"/api/v1/admin/kyc/{verification_id}/notes",
        headers=admin_headers,
        json={"note": "Customer confirmed missing surname on the upstream record."},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "requires_review"
    assert response.json()["review_events"] == [
        {
            "event_type": "note",
            "reviewer_user_id": str(UUID(admin_user_id)),
            "created_at": response.json()["review_events"][0]["created_at"],
            "decision": None,
            "reason": None,
            "note": "Customer confirmed missing surname on the upstream record.",
        }
    ]


async def test_admin_can_approve_review_kyc(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    admin_headers, admin_user_id = await create_user_and_token(
        session_factory,
        auth_service,
        email="approve-kyc-admin@example.com",
        role=UserRole.ADMIN,
    )
    user_id, verification_id = await seed_review_kyc_verification(session_factory)

    response = await client.post(
        f"/api/v1/admin/kyc/{verification_id}/approve",
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "verified"
    assert response.json()["review_events"] == [
        {
            "event_type": "decision",
            "reviewer_user_id": str(UUID(admin_user_id)),
            "created_at": response.json()["review_events"][0]["created_at"],
            "decision": "verified",
            "reason": None,
            "note": None,
        }
    ]

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        user = await uow.users.get(UUID(user_id))
        verification = await uow.kyc_verifications.get(UUID(verification_id))
    assert user.kyc_status is KycStatus.VERIFIED
    assert verification.status is KycVerificationStatus.VERIFIED
    assert verification.provider_status == "approved_by_admin"

    async with session_factory() as session:
        event_result = await session.execute(select(OutboxEventModel))
        events = event_result.scalars().all()
    assert [event.event_type for event in events] == ["user.kyc_verified"]


async def test_admin_can_reject_review_kyc(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    admin_headers, admin_user_id = await create_user_and_token(
        session_factory,
        auth_service,
        email="reject-kyc-admin@example.com",
        role=UserRole.ADMIN,
    )
    user_id, verification_id = await seed_review_kyc_verification(session_factory)

    response = await client.post(
        f"/api/v1/admin/kyc/{verification_id}/reject",
        headers=admin_headers,
        json={"reason": "Documents did not match the identity record."},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["rejection_reason"] == "Documents did not match the identity record."
    assert response.json()["review_events"] == [
        {
            "event_type": "decision",
            "reviewer_user_id": str(UUID(admin_user_id)),
            "created_at": response.json()["review_events"][0]["created_at"],
            "decision": "rejected",
            "reason": "Documents did not match the identity record.",
            "note": None,
        }
    ]

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        user = await uow.users.get(UUID(user_id))
        verification = await uow.kyc_verifications.get(UUID(verification_id))
    assert user.kyc_status is KycStatus.REJECTED
    assert verification.status is KycVerificationStatus.REJECTED
    assert verification.provider_status == "rejected_by_admin"

    async with session_factory() as session:
        event_result = await session.execute(select(OutboxEventModel))
        events = event_result.scalars().all()
    assert [event.event_type for event in events] == ["user.kyc_rejected"]


async def test_admin_cannot_review_non_review_kyc(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    admin_headers, _ = await create_user_and_token(
        session_factory,
        auth_service,
        email="bad-review-admin@example.com",
        role=UserRole.ADMIN,
    )
    user_id, verification_id = await seed_review_kyc_verification(session_factory)

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        verification = await uow.kyc_verifications.get(UUID(verification_id))
        await uow.kyc_verifications.update(
            replace(
                verification,
                status=KycVerificationStatus.VERIFIED,
                provider_status="verified",
                updated_at=datetime.now(UTC),
            )
        )
        user = await uow.users.get(UUID(user_id))
        await uow.users.update(
            replace(
                user,
                kyc_status=KycStatus.VERIFIED,
                updated_at=datetime.now(UTC),
            )
        )
        await uow.commit()

    response = await client.post(
        f"/api/v1/admin/kyc/{verification_id}/approve",
        headers=admin_headers,
    )

    assert response.status_code == 412
    assert response.json()["error_code"] == "precondition_failed"
