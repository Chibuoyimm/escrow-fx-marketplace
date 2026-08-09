from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.entities import OutboxEvent, User
from app.domain.enums import (
    AccountAuditEventType,
    ExchangeOfferStatus,
    ExchangeRequestStatus,
    OutboxEventStatus,
    TradeContractStatus,
    UserRole,
    UserStatus,
)
from app.domain.exceptions import AuthorizationError
from app.infrastructure.config import settings
from app.infrastructure.database.unit_of_work import AbstractUnitOfWork, SqlAlchemyUnitOfWork
from app.infrastructure.rate_limiting import ACCOUNT_MUTATION
from app.infrastructure.security import SecurityService
from app.main import app
from app.models.account_audit_event import AccountAuditEventModel
from app.models.exchange_offer import ExchangeOfferModel
from app.models.exchange_request import ExchangeRequestModel
from app.models.outbox_event import OutboxEventModel
from app.models.user import UserModel
from app.services.account import AccountService, get_account_service
from app.services.admin import AdminService, get_admin_service
from app.services.auth import AuthService, get_auth_service
from app.services.outbox import OutboxEventPublisher
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
def account_service(session_factory: async_sessionmaker[AsyncSession]) -> AccountService:
    return AccountService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        security=SecurityService(),
    )


@pytest.fixture
def admin_service(session_factory: async_sessionmaker[AsyncSession]) -> AdminService:
    return AdminService(uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory))


@pytest.fixture
async def client(
    auth_service: AuthService,
    account_service: AccountService,
    admin_service: AdminService,
) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    app.dependency_overrides[get_account_service] = lambda: account_service
    app.dependency_overrides[get_admin_service] = lambda: admin_service
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as test_client:
        yield test_client
    app.dependency_overrides.clear()


async def create_user(
    session_factory: async_sessionmaker[AsyncSession],
    auth_service: AuthService,
    *,
    email: str,
    role: UserRole = UserRole.CUSTOMER,
    status: UserStatus = UserStatus.ACTIVE,
    issue_token: bool = True,
) -> tuple[UUID, dict[str, str]]:
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
        return user.id, {}
    token = await auth_service.login_user(email=email, password=PASSWORD)
    return user.id, {"Authorization": f"Bearer {token.access_token}"}


async def account_events(
    session_factory: async_sessionmaker[AsyncSession],
    subject_user_id: UUID,
) -> list[AccountAuditEventModel]:
    async with session_factory() as session:
        result = await session.execute(
            select(AccountAuditEventModel)
            .where(AccountAuditEventModel.subject_user_id == subject_user_id)
            .order_by(AccountAuditEventModel.occurred_at.asc())
        )
        return list(result.scalars().all())


async def outbox_events(
    session_factory: async_sessionmaker[AsyncSession],
    subject_user_id: UUID,
) -> list[OutboxEventModel]:
    async with session_factory() as session:
        result = await session.execute(
            select(OutboxEventModel)
            .where(OutboxEventModel.recipient_user_id == subject_user_id)
            .order_by(OutboxEventModel.created_at.asc())
        )
        return list(result.scalars().all())


async def seed_account_obligation(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    user_id: UUID,
    obligation: str,
    trade_status: TradeContractStatus = TradeContractStatus.TERMS_LOCKED,
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        other = await uow.users.add(build_user(email=f"{obligation}-other@example.com"))
        source = await uow.currencies.add(build_currency(code="USD"))
        destination = await uow.currencies.add(build_currency(code="NGN"))
        request_owner_id = (
            user_id
            if obligation in {"request_open", "request_pending", "trade_requester"}
            else other.id
        )
        request_status = {
            "request_open": ExchangeRequestStatus.REQUEST_OPEN,
            "request_pending": ExchangeRequestStatus.OFFER_PENDING,
            "active_offer": ExchangeRequestStatus.OFFER_PENDING,
            "trade_requester": ExchangeRequestStatus.TERMS_LOCKED,
            "trade_counterparty": ExchangeRequestStatus.TERMS_LOCKED,
        }[obligation]
        exchange_request = await uow.exchange_requests.add(
            build_exchange_request(
                creator_user_id=request_owner_id,
                from_currency_id=source.id,
                to_currency_id=destination.id,
                status=request_status,
            )
        )
        if obligation in {"active_offer", "trade_requester", "trade_counterparty"}:
            offer = await uow.exchange_offers.add(
                build_exchange_offer(
                    request_id=exchange_request.id,
                    offer_user_id=(
                        user_id
                        if obligation in {"active_offer", "trade_counterparty"}
                        else other.id
                    ),
                    status=(
                        ExchangeOfferStatus.ACTIVE
                        if obligation == "active_offer"
                        else ExchangeOfferStatus.ACCEPTED
                    ),
                )
            )
            if obligation in {"trade_requester", "trade_counterparty"}:
                await uow.trade_contracts.add(
                    build_trade_contract(
                        request_id=exchange_request.id,
                        accepted_offer_id=offer.id,
                        status=trade_status,
                    )
                )
        await uow.commit()


async def test_profile_update_normalizes_phone_and_records_security_history(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id, headers = await create_user(session_factory, auth_service, email="profile@example.com")

    response = await client.patch(
        "/api/v1/users/me",
        headers=headers,
        json={"phone": " +234 800 123 4567 "},
    )

    assert response.status_code == 200
    assert response.json()["phone"] == "+2348001234567"
    audit = await account_events(session_factory, user_id)
    events = await outbox_events(session_factory, user_id)
    assert len(audit) == 1
    assert audit[0].event_type is AccountAuditEventType.PROFILE_UPDATED
    assert audit[0].actor_user_id == user_id
    assert audit[0].metadata_json == {"changed_fields": ["phone"]}
    assert len(events) == 1
    assert events[0].event_type == "user.profile_updated"
    assert "phone" not in events[0].payload


async def test_profile_mutation_uses_the_authenticated_account_limit(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, headers = await create_user(
        session_factory,
        auth_service,
        email="rate-limited-profile@example.com",
    )
    monkeypatch.setattr(
        settings,
        "rate_limit_policy_overrides",
        {f"{ACCOUNT_MUTATION}.user": {"limit": 1, "window_seconds": 60}},
    )

    first = await client.patch(
        "/api/v1/users/me",
        headers=headers,
        json={"phone": "+2348111111111"},
    )
    second = await client.patch(
        "/api/v1/users/me",
        headers=headers,
        json={"phone": "+2348222222222"},
    )

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error_code"] == "rate_limited"


async def test_profile_noop_and_invalid_payloads_are_safe(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id, headers = await create_user(
        session_factory, auth_service, email="profile-noop@example.com"
    )
    first = await client.patch(
        "/api/v1/users/me", headers=headers, json={"phone": "+2348111111111"}
    )
    second = await client.patch(
        "/api/v1/users/me", headers=headers, json={"phone": "+234 811 111 1111"}
    )
    empty = await client.patch("/api/v1/users/me", headers=headers, json={})
    extra = await client.patch(
        "/api/v1/users/me",
        headers=headers,
        json={"country": "US"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert empty.status_code == 422
    assert empty.json()["error_code"] == "invariant_violation"
    assert extra.status_code == 422
    assert extra.json()["error_code"] == "validation_error"
    assert len(await account_events(session_factory, user_id)) == 1
    assert len(await outbox_events(session_factory, user_id)) == 1


@pytest.mark.parametrize(
    "phone",
    [
        "call-me",
        "+234-800-123-4567",
        "++2348001234567",
        "123456",
        "1" * 16,
    ],
)
async def test_profile_update_rejects_invalid_international_phone_shapes(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
    phone: str,
) -> None:
    user_id, headers = await create_user(
        session_factory,
        auth_service,
        email=f"invalid-phone-{abs(hash(phone))}@example.com",
    )

    response = await client.patch("/api/v1/users/me", headers=headers, json={"phone": phone})

    assert response.status_code == 422
    assert await account_events(session_factory, user_id) == []
    assert await outbox_events(session_factory, user_id) == []


async def test_profile_update_allows_explicit_phone_clearing(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id, headers = await create_user(
        session_factory, auth_service, email="clear-phone@example.com"
    )

    response = await client.patch("/api/v1/users/me", headers=headers, json={"phone": None})

    assert response.status_code == 200
    assert response.json()["phone"] is None
    assert len(await account_events(session_factory, user_id)) == 1


async def test_deactivation_requires_password_soft_deactivates_and_invalidates_token(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id, headers = await create_user(
        session_factory, auth_service, email="deactivate@example.com"
    )
    wrong = await client.post(
        "/api/v1/users/me/deactivate",
        headers=headers,
        json={"current_password": "WrongPass123!"},
    )
    successful = await client.post(
        "/api/v1/users/me/deactivate",
        headers=headers,
        json={"current_password": PASSWORD},
    )
    old_token = await client.get("/api/v1/users/me", headers=headers)
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "deactivate@example.com", "password": PASSWORD},
    )

    assert wrong.status_code == 401
    assert successful.status_code == 200
    assert successful.json()["status"] == UserStatus.INACTIVE
    assert old_token.status_code == 401
    assert login.status_code == 401
    audit = await account_events(session_factory, user_id)
    events = await outbox_events(session_factory, user_id)
    assert [event.event_type for event in audit] == [AccountAuditEventType.SELF_DEACTIVATED]
    assert audit[0].metadata_json == {"status_from": "active", "status_to": "inactive"}
    assert [event.event_type for event in events] == ["user.account_deactivated"]


@pytest.mark.parametrize(
    "obligation",
    [
        "request_open",
        "request_pending",
        "active_offer",
        "trade_requester",
        "trade_counterparty",
    ],
)
async def test_deactivation_rejects_actionable_marketplace_obligations(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
    obligation: str,
) -> None:
    user_id, headers = await create_user(
        session_factory,
        auth_service,
        email=f"{obligation}-owner@example.com",
    )
    await seed_account_obligation(
        session_factory,
        user_id=user_id,
        obligation=obligation,
    )

    response = await client.post(
        "/api/v1/users/me/deactivate",
        headers=headers,
        json={"current_password": PASSWORD},
    )

    assert response.status_code == 409
    assert response.json()["error_code"] == "conflict"
    async with session_factory() as session:
        user = await session.get(UserModel, user_id)
        assert user is not None
        assert user.status is UserStatus.ACTIVE
    assert await account_events(session_factory, user_id) == []
    assert await outbox_events(session_factory, user_id) == []


@pytest.mark.parametrize("obligation", ["request_open", "active_offer"])
async def test_deactivation_ignores_expired_marketplace_obligations_before_reconciliation(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
    obligation: str,
) -> None:
    user_id, headers = await create_user(
        session_factory,
        auth_service,
        email=f"expired-{obligation}-owner@example.com",
    )
    await seed_account_obligation(
        session_factory,
        user_id=user_id,
        obligation=obligation,
    )
    expired_at = datetime.now(UTC) - timedelta(seconds=1)
    async with session_factory() as session:
        if obligation == "request_open":
            await session.execute(
                update(ExchangeRequestModel)
                .where(ExchangeRequestModel.creator_user_id == user_id)
                .values(expires_at=expired_at)
            )
        else:
            await session.execute(
                update(ExchangeOfferModel)
                .where(ExchangeOfferModel.offer_user_id == user_id)
                .values(expires_at=expired_at)
            )
        await session.commit()

    response = await client.post(
        "/api/v1/users/me/deactivate",
        headers=headers,
        json={"current_password": PASSWORD},
    )

    assert response.status_code == 200
    assert response.json()["status"] == UserStatus.INACTIVE


@pytest.mark.parametrize(
    "trade_status",
    [
        TradeContractStatus.AWAITING_DUAL_FUNDING,
        TradeContractStatus.ONE_LEG_FUNDED,
        TradeContractStatus.DUAL_FUNDED,
        TradeContractStatus.RELEASING,
        TradeContractStatus.EXPIRED_REFUNDING,
        TradeContractStatus.DISPUTED,
    ],
)
async def test_deactivation_rejects_every_other_non_terminal_trade_status(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
    trade_status: TradeContractStatus,
) -> None:
    user_id, headers = await create_user(
        session_factory,
        auth_service,
        email=f"non-terminal-{trade_status.value}@example.com",
    )
    await seed_account_obligation(
        session_factory,
        user_id=user_id,
        obligation="trade_requester",
        trade_status=trade_status,
    )

    response = await client.post(
        "/api/v1/users/me/deactivate",
        headers=headers,
        json={"current_password": PASSWORD},
    )

    assert response.status_code == 409


@pytest.mark.parametrize(
    "trade_status",
    [TradeContractStatus.SETTLED, TradeContractStatus.CANCELLED],
)
async def test_deactivation_allows_only_terminal_trade_history(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
    trade_status: TradeContractStatus,
) -> None:
    user_id, headers = await create_user(
        session_factory,
        auth_service,
        email=f"terminal-{trade_status.value}@example.com",
    )
    await seed_account_obligation(
        session_factory,
        user_id=user_id,
        obligation="trade_requester",
        trade_status=trade_status,
    )

    response = await client.post(
        "/api/v1/users/me/deactivate",
        headers=headers,
        json={"current_password": PASSWORD},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "inactive"


async def test_admin_can_suspend_and_reactivate_but_operations_cannot(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target_id, target_headers = await create_user(
        session_factory, auth_service, email="status-target@example.com"
    )
    admin_id, admin_headers = await create_user(
        session_factory,
        auth_service,
        email="status-admin@example.com",
        role=UserRole.ADMIN,
    )
    _, operations_headers = await create_user(
        session_factory,
        auth_service,
        email="status-operations@example.com",
        role=UserRole.OPERATIONS,
    )

    forbidden = await client.patch(
        f"/api/v1/admin/users/{target_id}/status",
        headers=operations_headers,
        json={"status": "suspended"},
    )
    suspended = await client.patch(
        f"/api/v1/admin/users/{target_id}/status",
        headers=admin_headers,
        json={"status": "suspended"},
    )
    blocked_token = await client.get("/api/v1/users/me", headers=target_headers)
    reactivated = await client.patch(
        f"/api/v1/admin/users/{target_id}/status",
        headers=admin_headers,
        json={"status": "active"},
    )
    no_op = await client.patch(
        f"/api/v1/admin/users/{target_id}/status",
        headers=admin_headers,
        json={"status": "active"},
    )

    assert forbidden.status_code == 403
    assert suspended.status_code == 200
    assert suspended.json()["status"] == "suspended"
    assert blocked_token.status_code == 401
    assert reactivated.status_code == 200
    assert reactivated.json()["status"] == "active"
    assert no_op.status_code == 200
    audit = await account_events(session_factory, target_id)
    events = await outbox_events(session_factory, target_id)
    assert [event.event_type for event in audit] == [
        AccountAuditEventType.ADMIN_SUSPENDED,
        AccountAuditEventType.ADMIN_REACTIVATED,
    ]
    assert all(event.actor_user_id == admin_id for event in audit)
    assert [event.event_type for event in events] == [
        "user.account_suspended",
        "user.account_reactivated",
    ]
    assert all(event.status is OutboxEventStatus.PENDING for event in events)
    assert all("actor_user_id" not in event.payload for event in events)


async def test_admin_can_reactivate_inactive_user_but_cannot_change_own_status(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target_id, _ = await create_user(
        session_factory,
        auth_service,
        email="inactive-target@example.com",
        status=UserStatus.INACTIVE,
        issue_token=False,
    )
    admin_id, admin_headers = await create_user(
        session_factory,
        auth_service,
        email="self-status-admin@example.com",
        role=UserRole.ADMIN,
    )

    reactivated = await client.patch(
        f"/api/v1/admin/users/{target_id}/status",
        headers=admin_headers,
        json={"status": "active"},
    )
    self_change = await client.patch(
        f"/api/v1/admin/users/{admin_id}/status",
        headers=admin_headers,
        json={"status": "suspended"},
    )
    inactive_request = await client.patch(
        f"/api/v1/admin/users/{target_id}/status",
        headers=admin_headers,
        json={"status": "inactive"},
    )

    assert reactivated.status_code == 200
    assert self_change.status_code == 422
    assert inactive_request.status_code == 422
    assert len(await account_events(session_factory, target_id)) == 1


async def test_admin_suspension_preserves_marketplace_obligations(
    client: AsyncClient,
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target_id, _ = await create_user(
        session_factory, auth_service, email="suspension-obligation-target@example.com"
    )
    _, admin_headers = await create_user(
        session_factory,
        auth_service,
        email="suspension-obligation-admin@example.com",
        role=UserRole.ADMIN,
    )
    await seed_account_obligation(
        session_factory,
        user_id=target_id,
        obligation="request_pending",
    )

    response = await client.patch(
        f"/api/v1/admin/users/{target_id}/status",
        headers=admin_headers,
        json={"status": "suspended"},
    )

    assert response.status_code == 200
    async with session_factory() as session:
        result = await session.execute(
            select(ExchangeRequestModel).where(ExchangeRequestModel.creator_user_id == target_id)
        )
        requests = result.scalars().all()
        assert len(requests) == 1
        assert requests[0].status is ExchangeRequestStatus.OFFER_PENDING


class FailingOutboxPublisher(OutboxEventPublisher):
    """Force the transaction to fail after the account mutation is prepared."""

    async def user_profile_updated(
        self,
        uow: AbstractUnitOfWork,
        *,
        user_id: UUID,
        email: str,
        changed_fields: list[str],
        changed_at: str,
        changed_at_display: str,
    ) -> OutboxEvent:
        raise RuntimeError("provider unavailable")

    async def user_account_deactivated(
        self,
        uow: AbstractUnitOfWork,
        *,
        user_id: UUID,
        email: str,
        deactivated_at: str,
        deactivated_at_display: str,
    ) -> OutboxEvent:
        raise RuntimeError("provider unavailable")

    async def user_account_suspended(
        self,
        uow: AbstractUnitOfWork,
        *,
        user_id: UUID,
        email: str,
        changed_at: str,
        changed_at_display: str,
    ) -> OutboxEvent:
        raise RuntimeError("provider unavailable")

    async def user_account_reactivated(
        self,
        uow: AbstractUnitOfWork,
        *,
        user_id: UUID,
        email: str,
        changed_at: str,
        changed_at_display: str,
    ) -> OutboxEvent:
        raise RuntimeError("provider unavailable")


async def test_profile_update_rolls_back_user_and_audit_when_outbox_fails(
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id, _ = await create_user(
        session_factory, auth_service, email="profile-rollback@example.com"
    )
    service = AccountService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        security=SecurityService(),
        outbox_publisher=FailingOutboxPublisher(),
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await service.update_profile(user_id=user_id, phone="+2348111111111")

    async with session_factory() as session:
        user = await session.get(UserModel, user_id)
        assert user is not None
        assert user.phone == "+2348000000000"
    assert await account_events(session_factory, user_id) == []
    assert await outbox_events(session_factory, user_id) == []


async def test_deactivation_rolls_back_user_and_audit_when_outbox_fails(
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id, _ = await create_user(
        session_factory, auth_service, email="deactivate-rollback@example.com"
    )
    service = AccountService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        security=SecurityService(),
        outbox_publisher=FailingOutboxPublisher(),
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await service.deactivate(user_id=user_id, current_password=PASSWORD)

    async with session_factory() as session:
        user = await session.get(UserModel, user_id)
        assert user is not None
        assert user.status is UserStatus.ACTIVE
    assert await account_events(session_factory, user_id) == []
    assert await outbox_events(session_factory, user_id) == []


@pytest.mark.parametrize(
    ("initial_status", "target_status"),
    [
        (UserStatus.ACTIVE, UserStatus.SUSPENDED),
        (UserStatus.SUSPENDED, UserStatus.ACTIVE),
    ],
)
async def test_admin_status_rolls_back_user_and_audit_when_outbox_fails(
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
    initial_status: UserStatus,
    target_status: UserStatus,
) -> None:
    target_id, _ = await create_user(
        session_factory,
        auth_service,
        email=f"admin-rollback-target-{initial_status.value}@example.com",
        status=initial_status,
        issue_token=False,
    )
    admin_id, _ = await create_user(
        session_factory,
        auth_service,
        email="admin-rollback-actor@example.com",
        role=UserRole.ADMIN,
    )
    service = AdminService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        outbox_publisher=FailingOutboxPublisher(),
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await service.update_user_status(
            subject_user_id=target_id,
            actor_user_id=admin_id,
            status=target_status,
        )

    async with session_factory() as session:
        user = await session.get(UserModel, target_id)
        assert user is not None
        assert user.status is initial_status
    assert await account_events(session_factory, target_id) == []
    assert await outbox_events(session_factory, target_id) == []


@pytest.mark.parametrize(
    ("role", "status"),
    [
        (UserRole.CUSTOMER, UserStatus.ACTIVE),
        (UserRole.OPERATIONS, UserStatus.ACTIVE),
        (UserRole.ADMIN, UserStatus.INACTIVE),
    ],
)
async def test_admin_service_enforces_actor_authorization_without_route(
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
    role: UserRole,
    status: UserStatus,
) -> None:
    target_id, _ = await create_user(
        session_factory, auth_service, email=f"direct-target-{role}-{status}@example.com"
    )
    actor_id, _ = await create_user(
        session_factory,
        auth_service,
        email=f"direct-actor-{role}-{status}@example.com",
        role=role,
        status=status,
        issue_token=False,
    )
    service = AdminService(uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory))

    with pytest.raises(AuthorizationError, match="Administrator access"):
        await service.update_user_status(
            subject_user_id=target_id,
            actor_user_id=actor_id,
            status=UserStatus.SUSPENDED,
        )

    async with session_factory() as session:
        user = await session.get(UserModel, target_id)
        assert user is not None
        assert user.status is UserStatus.ACTIVE


async def test_admin_service_locks_actor_and_subject_in_uuid_order(
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_id, _ = await create_user(
        session_factory, auth_service, email="lock-order-target@example.com"
    )
    admin_id, _ = await create_user(
        session_factory,
        auth_service,
        email="lock-order-admin@example.com",
        role=UserRole.ADMIN,
    )
    from app.repositories.sqlalchemy import SqlAlchemyUserRepository

    locked_ids: list[UUID] = []
    original = SqlAlchemyUserRepository.get_for_update

    async def recording_get_for_update(
        repository: SqlAlchemyUserRepository,
        user_id: UUID,
    ) -> User:
        locked_ids.append(user_id)
        return await original(repository, user_id)

    monkeypatch.setattr(SqlAlchemyUserRepository, "get_for_update", recording_get_for_update)
    service = AdminService(uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory))

    await service.update_user_status(
        subject_user_id=target_id,
        actor_user_id=admin_id,
        status=UserStatus.SUSPENDED,
    )

    assert locked_ids == sorted((target_id, admin_id), key=lambda value: value.int)


async def test_account_audit_repository_is_append_only_and_ordered(
    auth_service: AuthService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id, _ = await create_user(
        session_factory, auth_service, email="audit-repository@example.com"
    )
    service = AccountService(uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory))
    await service.update_profile(user_id=user_id, phone="+2348111111111")
    events = await service.update_profile(user_id=user_id, phone="+2348222222222")

    assert events.phone == "+2348222222222"
    audit = await account_events(session_factory, user_id)
    assert len(audit) == 2
    assert audit[0].occurred_at <= audit[1].occurred_at
