from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.domain.entities import (
    Corridor,
    CorridorRail,
    Currency,
    ExchangeOffer,
    ExchangeRequest,
    TradeContract,
    User,
)
from app.domain.enums import (
    CorridorStatus,
    CurrencyStatus,
    ExchangeOfferStatus,
    ExchangeRequestStatus,
    FlowType,
    KycStatus,
    RailStatus,
    RiskLevel,
    TradeContractStatus,
    UserRole,
    UserStatus,
)
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from app.models import Base


def assert_canonical_mutation_lock_order(
    calls: list[tuple[str, UUID]],
) -> None:
    """Assert users, requests, and offers were locked in canonical order."""
    rank = {"user": 0, "request": 1, "offer": 2}
    assert calls
    assert [rank[resource] for resource, _ in calls] == sorted(
        rank[resource] for resource, _ in calls
    )
    for resource in rank:
        ids = [resource_id for name, resource_id in calls if name == resource]
        assert ids == sorted(ids, key=lambda value: value.int)


def record_field(record: object, name: str) -> object:
    """Read a captured log record field without duplicating test helpers."""
    return getattr(record, "__dict__", {}).get(name)


@pytest.fixture
def mutation_lock_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, UUID]]:
    """Record concrete repository row-lock calls made by a service operation."""
    from app.repositories.sqlalchemy import (
        SqlAlchemyExchangeOfferRepository,
        SqlAlchemyExchangeRequestRepository,
        SqlAlchemyUserRepository,
    )

    calls: list[tuple[str, UUID]] = []
    original_user = SqlAlchemyUserRepository.get_for_update
    original_request = SqlAlchemyExchangeRequestRepository.get_for_update
    original_offer = SqlAlchemyExchangeOfferRepository.get_for_update

    async def record_user(repository: Any, user_id: UUID) -> User:
        calls.append(("user", user_id))
        return await original_user(repository, user_id)

    async def record_request(repository: Any, request_id: UUID) -> ExchangeRequest:
        calls.append(("request", request_id))
        return await original_request(repository, request_id)

    async def record_offer(repository: Any, offer_id: UUID) -> ExchangeOffer:
        calls.append(("offer", offer_id))
        return await original_offer(repository, offer_id)

    monkeypatch.setattr(SqlAlchemyUserRepository, "get_for_update", record_user)
    monkeypatch.setattr(SqlAlchemyExchangeRequestRepository, "get_for_update", record_request)
    monkeypatch.setattr(SqlAlchemyExchangeOfferRepository, "get_for_update", record_offer)
    return calls


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def app_log_capture(
    caplog: pytest.LogCaptureFixture,
) -> Generator[pytest.LogCaptureFixture]:
    """Attach pytest's capture handler directly to the isolated app logger."""
    from app.infrastructure.application_logging import configure_logging

    configure_logging()
    app_logger = logging.getLogger("app")
    app_logger.addHandler(caplog.handler)
    try:
        yield caplog
    finally:
        app_logger.removeHandler(caplog.handler)


@pytest.fixture(autouse=True)
def rate_limit_service_override(
    request: pytest.FixtureRequest,
) -> Any:
    """Keep API rate-limit tests isolated in each test database."""
    if "client" not in request.fixturenames:
        yield
        return
    session_factory = request.getfixturevalue("session_factory")
    from app.infrastructure.rate_limiting import RateLimitService, get_rate_limit_service
    from app.main import app

    previous = app.dependency_overrides.get(get_rate_limit_service)
    app.dependency_overrides[get_rate_limit_service] = lambda: RateLimitService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    try:
        yield
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_rate_limit_service, None)
        else:
            app.dependency_overrides[get_rate_limit_service] = previous


@pytest.fixture
async def async_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def session_factory(async_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def session(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with session_factory() as db_session:
        yield db_session
        await db_session.rollback()


def now() -> datetime:
    return datetime.now(UTC)


def build_user(
    *,
    email: str = "user@example.com",
    user_id: UUID | None = None,
    password_hash: str = "hashed-password",
    status: UserStatus = UserStatus.ACTIVE,
    kyc_status: KycStatus = KycStatus.VERIFIED,
    email_verified: bool = True,
) -> User:
    current_time = now()
    return User(
        id=user_id or uuid4(),
        email=email,
        password_hash=password_hash,
        phone="+2348000000000",
        country="NG",
        role=UserRole.CUSTOMER,
        status=status,
        kyc_status=kyc_status,
        risk_level=RiskLevel.LOW,
        email_verified_at=current_time if email_verified else None,
        created_at=current_time,
        updated_at=current_time,
    )


def build_currency(
    *,
    code: str,
    status: CurrencyStatus = CurrencyStatus.ACTIVE,
    currency_id: UUID | None = None,
) -> Currency:
    return Currency(
        id=currency_id or uuid4(),
        code=code,
        minor_unit=2,
        status=status,
        min_amount=Decimal("1.00"),
        max_amount=Decimal("1000000.00"),
        created_at=now(),
        updated_at=now(),
    )


def build_corridor(
    *,
    from_currency_id: UUID,
    to_currency_id: UUID,
    status: CorridorStatus = CorridorStatus.ACTIVE,
    corridor_id: UUID | None = None,
) -> Corridor:
    return Corridor(
        id=corridor_id or uuid4(),
        from_currency_id=from_currency_id,
        to_currency_id=to_currency_id,
        status=status,
        funding_sla_minutes=30,
        fee_model_name="default",
        created_at=now(),
        updated_at=now(),
    )


def build_corridor_rail(
    *,
    corridor_id: UUID,
    priority_order: int = 1,
    status: RailStatus = RailStatus.ACTIVE,
    rail_id: UUID | None = None,
) -> CorridorRail:
    return CorridorRail(
        id=rail_id or uuid4(),
        corridor_id=corridor_id,
        flow_type=FlowType.FUNDING,
        priority_order=priority_order,
        provider="paystack",
        method="bank_transfer",
        status=status,
        created_at=now(),
        updated_at=now(),
    )


def build_exchange_request(
    *,
    creator_user_id: UUID,
    from_currency_id: UUID,
    to_currency_id: UUID,
    request_id: UUID | None = None,
    relisted_from_request_id: UUID | None = None,
    from_amount: Decimal = Decimal("100.00"),
    preferred_rate: Decimal = Decimal("1500.00"),
    min_rate: Decimal | None = Decimal("1450.00"),
    status: ExchangeRequestStatus = ExchangeRequestStatus.REQUEST_OPEN,
    expires_at: datetime | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> ExchangeRequest:
    created = created_at or now()
    return ExchangeRequest(
        id=request_id or uuid4(),
        relisted_from_request_id=relisted_from_request_id,
        creator_user_id=creator_user_id,
        from_currency_id=from_currency_id,
        to_currency_id=to_currency_id,
        from_amount=from_amount,
        preferred_rate=preferred_rate,
        min_rate=min_rate,
        status=status,
        expires_at=expires_at or (created + timedelta(hours=1)),
        created_at=created,
        updated_at=updated_at or created,
    )


def build_exchange_offer(
    *,
    request_id: UUID,
    offer_user_id: UUID,
    offer_id: UUID | None = None,
    offered_rate: Decimal = Decimal("1495.00"),
    status: ExchangeOfferStatus = ExchangeOfferStatus.ACTIVE,
    expires_at: datetime | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> ExchangeOffer:
    created = created_at or now()
    return ExchangeOffer(
        id=offer_id or uuid4(),
        request_id=request_id,
        offer_user_id=offer_user_id,
        offered_rate=offered_rate,
        status=status,
        expires_at=expires_at or (created + timedelta(hours=1)),
        created_at=created,
        updated_at=updated_at or created,
    )


def build_trade_contract(
    *,
    request_id: UUID,
    accepted_offer_id: UUID,
    trade_id: UUID | None = None,
    agreed_rate: Decimal = Decimal("1490.00"),
    reference_rate_snapshot: Decimal | None = None,
    from_amount: Decimal = Decimal("100.00"),
    to_amount: Decimal = Decimal("149000.00"),
    funding_deadline_at: datetime | None = None,
    status: TradeContractStatus = TradeContractStatus.TERMS_LOCKED,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> TradeContract:
    created = created_at or now()
    return TradeContract(
        id=trade_id or uuid4(),
        request_id=request_id,
        accepted_offer_id=accepted_offer_id,
        agreed_rate=agreed_rate,
        reference_rate_snapshot=reference_rate_snapshot,
        from_amount=from_amount,
        to_amount=to_amount,
        funding_deadline_at=funding_deadline_at or (created + timedelta(minutes=30)),
        status=status,
        created_at=created,
        updated_at=updated_at or created,
    )
