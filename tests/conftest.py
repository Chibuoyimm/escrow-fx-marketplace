from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.domain.entities import Corridor, CorridorRail, Currency, User
from app.domain.enums import (
    CorridorStatus,
    CurrencyStatus,
    FlowType,
    KycStatus,
    RailStatus,
    RiskLevel,
    UserRole,
    UserStatus,
)
from app.models import Base


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


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


def build_user(*, email: str = "user@example.com", user_id: UUID | None = None) -> User:
    return User(
        id=user_id or uuid4(),
        email=email,
        phone="+2348000000000",
        country="NG",
        role=UserRole.CUSTOMER,
        status=UserStatus.ACTIVE,
        kyc_status=KycStatus.VERIFIED,
        risk_level=RiskLevel.LOW,
        created_at=now(),
        updated_at=now(),
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
