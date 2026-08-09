"""Opt-in PostgreSQL concurrency regressions for replacement-style user writes."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.domain.entities import (
    EmailVerificationToken,
    KycVerification,
    OutboxEvent,
    PasswordResetToken,
    User,
)
from app.domain.enums import (
    KycIdType,
    KycProvider,
    KycStatus,
    KycVerificationStatus,
    OutboxEventStatus,
    UserStatus,
)
from app.domain.exceptions import IdempotencyConflictError, PreconditionFailedError
from app.infrastructure.config import settings
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.idempotency import IdempotencyReplay, IdempotencyRequest
from app.infrastructure.rate_limiting import MARKETPLACE_MUTATION, RateLimitService
from app.infrastructure.security import SecurityService
from app.integrations.youverify import KycProviderRequest, KycProviderResult
from app.models import Base
from app.models.exchange_request import ExchangeRequestModel
from app.models.idempotency_record import IdempotencyRecordModel
from app.models.outbox_event import OutboxEventModel
from app.models.rate_limit_bucket import RateLimitBucketModel
from app.services.auth import AuthService, hash_auth_token
from app.services.exchange_request import ExchangeRequestService
from app.services.kyc import KycService
from app.services.outbox import build_outbox_event
from tests.conftest import build_corridor, build_currency, build_user

pytestmark = pytest.mark.anyio


@pytest.fixture
async def postgres_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Use an explicitly dedicated PostgreSQL test database when configured."""
    database_url = os.environ.get("TEST_POSTGRES_DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_POSTGRES_DATABASE_URL to run PostgreSQL concurrency tests.")

    engine: AsyncEngine = create_async_engine(database_url, pool_pre_ping=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()


async def _create_user(factory: async_sessionmaker[AsyncSession], email: str) -> User:
    async with SqlAlchemyUnitOfWork(factory) as uow:
        user = await uow.users.add(build_user(email=email))
        await uow.commit()
        return user


async def _create_kyc_attempt(
    factory: async_sessionmaker[AsyncSession],
    *,
    status: KycVerificationStatus,
) -> tuple[User, KycVerification]:
    user = await _create_user(factory, f"kyc-terminal-race-{uuid4()}@example.com")
    async with SqlAlchemyUnitOfWork(factory) as uow:
        current_user = await uow.users.get_for_update(user.id)
        await uow.users.update(
            replace(
                current_user,
                kyc_status=(
                    KycStatus.REQUIRES_REVIEW
                    if status is KycVerificationStatus.REQUIRES_REVIEW
                    else KycStatus.PENDING
                ),
                updated_at=datetime.now(UTC),
            )
        )
        current_time = datetime.now(UTC)
        verification = await uow.kyc_verifications.add(
            KycVerification(
                id=uuid4(),
                user_id=user.id,
                provider=KycProvider.LOCAL,
                provider_reference_id=f"kyc-race-reference-{uuid4()}",
                id_type=KycIdType.BVN,
                masked_identifier="22*****2221",
                identifier_hash=f"kyc-race-hash-{uuid4()}",
                status=status,
                provider_status=status.value,
                field_match_summary={"id_type": "bvn"},
                review_events=[],
                rejection_reason=None,
                consented_at=current_time,
                submitted_at=current_time,
                completed_at=None,
                created_at=current_time,
                updated_at=current_time,
            )
        )
        await uow.commit()
    return user, verification


class VerifiedOnRetrieveProvider:
    """Provider double used to exercise reconciliation through KycService."""

    async def verify_identity(self, request: KycProviderRequest) -> KycProviderResult:
        raise AssertionError("This provider should only be used for reconciliation.")

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


async def test_password_write_preserves_concurrent_suspension(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user = await _create_user(postgres_session_factory, f"password-race-{uuid4()}@example.com")

    async with SqlAlchemyUnitOfWork(postgres_session_factory) as stale_uow:
        stale_snapshot = await stale_uow.users.get(user.id)

        async with SqlAlchemyUnitOfWork(postgres_session_factory) as suspending_uow:
            current = await suspending_uow.users.get_for_update(user.id)
            await suspending_uow.users.update(
                replace(current, status=UserStatus.SUSPENDED, updated_at=datetime.now(UTC))
            )
            await suspending_uow.commit()

        refreshed = await stale_uow.users.get_for_update(user.id)
        saved = await stale_uow.users.update(
            replace(
                refreshed,
                password_hash="new-password-hash",
                updated_at=datetime.now(UTC),
            )
        )
        await stale_uow.commit()

    assert stale_snapshot.status is UserStatus.ACTIVE
    assert saved.status is UserStatus.SUSPENDED
    assert saved.password_hash == "new-password-hash"


async def test_duplicate_exchange_request_idempotency_is_single_mutation_postgres(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user = await _create_user(postgres_session_factory, f"idempotency-race-{uuid4()}@example.com")
    async with SqlAlchemyUnitOfWork(postgres_session_factory) as uow:
        usd = await uow.currencies.add(build_currency(code="USD"))
        ngn = await uow.currencies.add(build_currency(code="NGN"))
        await uow.corridors.add(build_corridor(from_currency_id=usd.id, to_currency_id=ngn.id))
        await uow.commit()

    idempotency = IdempotencyRequest(
        principal_user_id=user.id,
        operation_scope="exchange-request.create",
        key_hash="a" * 64,
        request_fingerprint="b" * 64,
    )
    services = [
        ExchangeRequestService(uow_factory=lambda: SqlAlchemyUnitOfWork(postgres_session_factory))
        for _ in range(2)
    ]
    outcomes = await asyncio.gather(
        *(
            service.create_request(
                creator_user_id=user.id,
                from_currency_code="USD",
                to_currency_code="NGN",
                from_amount=Decimal("100"),
                preferred_rate=Decimal("1500"),
                min_rate=Decimal("1450"),
                idempotency=idempotency,
            )
            for service in services
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(outcome, IdempotencyReplay) for outcome in outcomes) == 1
    assert (
        sum(
            not isinstance(outcome, Exception) and not isinstance(outcome, IdempotencyReplay)
            for outcome in outcomes
        )
        == 1
    )
    async with postgres_session_factory() as session:
        request_count = await session.scalar(select(func.count()).select_from(ExchangeRequestModel))
        event_count = await session.scalar(select(func.count()).select_from(OutboxEventModel))
        idempotency_count = await session.scalar(
            select(func.count()).select_from(IdempotencyRecordModel)
        )
    assert request_count == 1
    assert event_count == 1
    assert idempotency_count == 1


async def test_concurrent_different_fingerprints_have_one_conflict_postgres(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user = await _create_user(
        postgres_session_factory,
        f"idempotency-fingerprint-race-{uuid4()}@example.com",
    )
    async with SqlAlchemyUnitOfWork(postgres_session_factory) as uow:
        usd = await uow.currencies.add(build_currency(code="USD"))
        ngn = await uow.currencies.add(build_currency(code="NGN"))
        await uow.corridors.add(build_corridor(from_currency_id=usd.id, to_currency_id=ngn.id))
        await uow.commit()

    requests = [
        IdempotencyRequest(
            principal_user_id=user.id,
            operation_scope="exchange-request.create",
            key_hash="c" * 64,
            request_fingerprint="d" * 64,
        ),
        IdempotencyRequest(
            principal_user_id=user.id,
            operation_scope="exchange-request.create",
            key_hash="c" * 64,
            request_fingerprint="e" * 64,
        ),
    ]
    services = [
        ExchangeRequestService(uow_factory=lambda: SqlAlchemyUnitOfWork(postgres_session_factory))
        for _ in requests
    ]
    outcomes = await asyncio.gather(
        *(
            service.create_request(
                creator_user_id=user.id,
                from_currency_code="USD",
                to_currency_code="NGN",
                from_amount=Decimal("100"),
                preferred_rate=Decimal("1500"),
                min_rate=Decimal("1450"),
                idempotency=idempotency,
            )
            for service, idempotency in zip(services, requests, strict=True)
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(outcome, IdempotencyConflictError) for outcome in outcomes) == 1
    assert (
        sum(
            not isinstance(outcome, Exception) and not isinstance(outcome, IdempotencyReplay)
            for outcome in outcomes
        )
        == 1
    )
    async with postgres_session_factory() as session:
        request_count = await session.scalar(select(func.count()).select_from(ExchangeRequestModel))
        event_count = await session.scalar(select(func.count()).select_from(OutboxEventModel))
        idempotency_count = await session.scalar(
            select(func.count()).select_from(IdempotencyRecordModel)
        )
    assert request_count == 1
    assert event_count == 1
    assert idempotency_count == 1


async def test_rolled_back_idempotency_claim_can_be_reclaimed_postgres(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user = await _create_user(
        postgres_session_factory,
        f"idempotency-rollback-recovery-{uuid4()}@example.com",
    )
    async with SqlAlchemyUnitOfWork(postgres_session_factory) as uow:
        usd = await uow.currencies.add(build_currency(code="USD"))
        ngn = await uow.currencies.add(build_currency(code="NGN"))
        await uow.corridors.add(build_corridor(from_currency_id=usd.id, to_currency_id=ngn.id))
        await uow.commit()

    idempotency = IdempotencyRequest(
        principal_user_id=user.id,
        operation_scope="exchange-request.create",
        key_hash="f" * 64,
        request_fingerprint="g" * 64,
    )
    async with SqlAlchemyUnitOfWork(postgres_session_factory) as failed_uow:
        await failed_uow.idempotency_records.claim(
            principal_user_id=idempotency.principal_user_id,
            operation_scope=idempotency.operation_scope,
            key_hash=idempotency.key_hash,
            request_fingerprint=idempotency.request_fingerprint,
            now=datetime.now(UTC),
        )
        await failed_uow.rollback()

    service = ExchangeRequestService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(postgres_session_factory)
    )
    outcome = await service.create_request(
        creator_user_id=user.id,
        from_currency_code="USD",
        to_currency_code="NGN",
        from_amount=Decimal("100"),
        preferred_rate=Decimal("1500"),
        min_rate=Decimal("1450"),
        idempotency=idempotency,
    )

    assert not isinstance(outcome, IdempotencyReplay)
    async with postgres_session_factory() as session:
        request_count = await session.scalar(select(func.count()).select_from(ExchangeRequestModel))
        event_count = await session.scalar(select(func.count()).select_from(OutboxEventModel))
        idempotency_count = await session.scalar(
            select(func.count()).select_from(IdempotencyRecordModel)
        )
    assert request_count == 1
    assert event_count == 1
    assert idempotency_count == 1


async def test_kyc_write_preserves_concurrent_deactivation(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user = await _create_user(postgres_session_factory, f"kyc-race-{uuid4()}@example.com")

    async with SqlAlchemyUnitOfWork(postgres_session_factory) as stale_uow:
        stale_snapshot = await stale_uow.users.get(user.id)

        async with SqlAlchemyUnitOfWork(postgres_session_factory) as deactivating_uow:
            current = await deactivating_uow.users.get_for_update(user.id)
            await deactivating_uow.users.update(
                replace(current, status=UserStatus.INACTIVE, updated_at=datetime.now(UTC))
            )
            await deactivating_uow.commit()

        refreshed = await stale_uow.users.get_for_update(user.id)
        saved = await stale_uow.users.update(
            replace(
                refreshed,
                kyc_status=KycStatus.VERIFIED,
                updated_at=datetime.now(UTC),
            )
        )
        await stale_uow.commit()

    assert stale_snapshot.status is UserStatus.ACTIVE
    assert saved.status is UserStatus.INACTIVE
    assert saved.kyc_status is KycStatus.VERIFIED


async def test_stale_outbox_finalizer_cannot_overwrite_reclaimed_lease(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user = await _create_user(postgres_session_factory, f"outbox-race-{uuid4()}@example.com")
    event: OutboxEvent
    async with SqlAlchemyUnitOfWork(postgres_session_factory) as uow:
        event = await uow.outbox_events.add(
            build_outbox_event(
                event_type="exchange_request.created",
                aggregate_type="exchange_request",
                aggregate_id=uuid4(),
                recipient_user_id=user.id,
                payload={},
            )
        )
        await uow.commit()

    first_now = datetime.now(UTC)
    first_deadline = first_now + timedelta(minutes=5)
    async with SqlAlchemyUnitOfWork(postgres_session_factory) as uow:
        first_claim = await uow.outbox_events.claim_due_for_dispatch(
            now=first_now,
            processing_deadline=first_deadline,
            limit=1,
        )
        await uow.commit()

    second_now = first_deadline + timedelta(seconds=1)
    second_deadline = second_now + timedelta(minutes=5)
    async with SqlAlchemyUnitOfWork(postgres_session_factory) as uow:
        second_claim = await uow.outbox_events.claim_due_for_dispatch(
            now=second_now,
            processing_deadline=second_deadline,
            limit=1,
        )
        await uow.commit()

    assert first_claim[0].id == event.id
    assert second_claim[0].id == event.id
    assert first_claim[0].next_attempt_at == first_deadline
    assert second_claim[0].next_attempt_at == second_deadline

    async with SqlAlchemyUnitOfWork(postgres_session_factory) as uow:
        stale = await uow.outbox_events.mark_failed(
            event_id=event.id,
            status=OutboxEventStatus.DEAD,
            attempt_count=99,
            last_error="stale worker",
            next_attempt_at=None,
            now=second_now,
            expected_processing_deadline=first_deadline,
        )
        await uow.commit()

    assert stale is None

    async with SqlAlchemyUnitOfWork(postgres_session_factory) as uow:
        current = await uow.outbox_events.list_admin()

    assert current[0].status is OutboxEventStatus.PROCESSING
    assert current[0].next_attempt_at == second_deadline

    async with SqlAlchemyUnitOfWork(postgres_session_factory) as uow:
        delivered = await uow.outbox_events.mark_delivered(
            event_id=event.id,
            expected_processing_deadline=second_deadline,
            now=second_now,
        )
        await uow.commit()

    assert delivered is not None
    assert delivered.status is OutboxEventStatus.DELIVERED


async def test_rate_limit_bucket_is_atomic_under_postgres_concurrency(
    postgres_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent workers cannot allow more requests than the configured limit."""
    user = await _create_user(postgres_session_factory, f"rate-limit-race-{uuid4()}@example.com")
    monkeypatch.setattr(
        settings,
        "rate_limit_policy_overrides",
        {f"{MARKETPLACE_MUTATION}.user": {"limit": 3, "window_seconds": 60}},
    )
    services = [
        RateLimitService(uow_factory=lambda: SqlAlchemyUnitOfWork(postgres_session_factory))
        for _ in range(10)
    ]

    decisions = await asyncio.gather(
        *(
            service.enforce(
                policy_name=MARKETPLACE_MUTATION,
                identities={"user": str(user.id)},
            )
            for service in services
        )
    )

    assert sum(not decision.limited for decision in decisions) == 3
    assert sum(decision.limited for decision in decisions) == 7
    async with postgres_session_factory() as session:
        bucket = await session.scalar(
            select(RateLimitBucketModel).where(
                RateLimitBucketModel.policy_name == MARKETPLACE_MUTATION
            )
        )
    assert bucket is not None
    assert bucket.request_count == 4


async def test_email_verification_token_is_single_use_under_concurrency(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user = await _create_user(postgres_session_factory, f"email-token-race-{uuid4()}@example.com")
    current_time = datetime.now(UTC)
    raw_token = f"email-token-{uuid4()}"
    async with SqlAlchemyUnitOfWork(postgres_session_factory) as uow:
        await uow.email_verification_tokens.add(
            EmailVerificationToken(
                id=uuid4(),
                user_id=user.id,
                token_hash=hash_auth_token(raw_token),
                expires_at=current_time + timedelta(hours=1),
                consumed_at=None,
                created_at=current_time,
                updated_at=current_time,
            )
        )
        await uow.commit()

    services = [
        AuthService(uow_factory=lambda: SqlAlchemyUnitOfWork(postgres_session_factory))
        for _ in range(2)
    ]
    outcomes = await asyncio.gather(
        *(service.verify_email(raw_token) for service in services),
        return_exceptions=True,
    )

    assert sum(isinstance(outcome, User) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, PreconditionFailedError) for outcome in outcomes) == 1


async def test_password_reset_token_is_single_use_under_concurrency(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user = await _create_user(postgres_session_factory, f"reset-token-race-{uuid4()}@example.com")
    current_time = datetime.now(UTC)
    raw_token = f"reset-token-{uuid4()}"
    async with SqlAlchemyUnitOfWork(postgres_session_factory) as uow:
        await uow.password_reset_tokens.add(
            PasswordResetToken(
                id=uuid4(),
                user_id=user.id,
                token_hash=hash_auth_token(raw_token),
                expires_at=current_time + timedelta(hours=1),
                consumed_at=None,
                created_at=current_time,
                updated_at=current_time,
            )
        )
        await uow.commit()

    security = SecurityService()
    services = [
        AuthService(
            uow_factory=lambda: SqlAlchemyUnitOfWork(postgres_session_factory),
            security=security,
        )
        for _ in range(2)
    ]
    outcomes = await asyncio.gather(
        *(
            service.reset_password(token=raw_token, password=f"NewPass-{uuid4()}!")
            for service in services
        ),
        return_exceptions=True,
    )

    assert sum(outcome is None for outcome in outcomes) == 1
    assert sum(isinstance(outcome, PreconditionFailedError) for outcome in outcomes) == 1


async def test_competing_kyc_admin_decisions_have_one_terminal_winner(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user, verification = await _create_kyc_attempt(
        postgres_session_factory,
        status=KycVerificationStatus.REQUIRES_REVIEW,
    )
    services = [
        KycService(uow_factory=lambda: SqlAlchemyUnitOfWork(postgres_session_factory))
        for _ in range(2)
    ]
    outcomes = await asyncio.gather(
        services[0].approve_review(
            verification_id=verification.id,
            reviewer_user_id=user.id,
        ),
        services[1].reject_review(
            verification_id=verification.id,
            reviewer_user_id=user.id,
            reason="Concurrent rejection.",
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(outcome, KycVerification) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, PreconditionFailedError) for outcome in outcomes) == 1
    async with SqlAlchemyUnitOfWork(postgres_session_factory) as uow:
        current = await uow.kyc_verifications.get(verification.id)
    assert current.status in {
        KycVerificationStatus.VERIFIED,
        KycVerificationStatus.REJECTED,
    }

    async with postgres_session_factory() as session:
        event_result = await session.execute(select(OutboxEventModel))
        events = event_result.scalars().all()
    assert len(events) == 1
    assert events[0].event_type in {"user.kyc_verified", "user.kyc_rejected"}


async def test_webhook_and_reconciliation_have_one_kyc_terminal_winner(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, verification = await _create_kyc_attempt(
        postgres_session_factory,
        status=KycVerificationStatus.PENDING,
    )
    service = KycService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(postgres_session_factory),
        provider=VerifiedOnRetrieveProvider(),
    )
    payload: dict[str, object] = {
        "data": {
            "id": verification.provider_reference_id,
            "status": "verified",
            "validation": {
                "matches": {
                    "firstName": True,
                    "lastName": True,
                    "dateOfBirth": True,
                }
            },
        }
    }

    outcomes = await asyncio.gather(
        service.process_youverify_webhook(payload),
        service.reconcile_pending(limit=1),
        return_exceptions=True,
    )

    assert all(not isinstance(outcome, Exception) for outcome in outcomes)
    async with SqlAlchemyUnitOfWork(postgres_session_factory) as uow:
        current = await uow.kyc_verifications.get(verification.id)
    assert current.status is KycVerificationStatus.VERIFIED

    async with postgres_session_factory() as session:
        event_result = await session.execute(select(OutboxEventModel))
        events = event_result.scalars().all()
    assert [event.event_type for event in events] == ["user.kyc_verified"]
