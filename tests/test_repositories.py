from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import CorridorStatus, CurrencyStatus, RailStatus
from app.domain.exceptions import ConflictError, NotFoundError
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from app.repositories.sqlalchemy import (
    SqlAlchemyCorridorRailRepository,
    SqlAlchemyCorridorRepository,
    SqlAlchemyCurrencyRepository,
    SqlAlchemyUserRepository,
)
from tests.conftest import build_corridor, build_corridor_rail, build_currency, build_user


@pytest.mark.anyio
async def test_user_repository_round_trips_users(session: AsyncSession) -> None:
    repository = SqlAlchemyUserRepository(session)
    user = build_user()

    created = await repository.add(user)
    fetched = await repository.get_by_email(user.email)

    assert created.email == user.email
    assert fetched.id == user.id


@pytest.mark.anyio
async def test_currency_repository_lists_only_active_records(session: AsyncSession) -> None:
    repository = SqlAlchemyCurrencyRepository(session)
    active = build_currency(code="USD", status=CurrencyStatus.ACTIVE)
    inactive = build_currency(code="GBP", status=CurrencyStatus.INACTIVE)

    await repository.add(active)
    await repository.add(inactive)

    currencies = await repository.list_active()

    assert [currency.code for currency in currencies] == ["USD"]


@pytest.mark.anyio
async def test_corridor_and_rail_repositories_filter_inactive_records(session: AsyncSession) -> None:
    currency_repository = SqlAlchemyCurrencyRepository(session)
    corridor_repository = SqlAlchemyCorridorRepository(session)
    rail_repository = SqlAlchemyCorridorRailRepository(session)

    from_currency = await currency_repository.add(build_currency(code="USD"))
    to_currency = await currency_repository.add(build_currency(code="NGN"))
    corridor = await corridor_repository.add(
        build_corridor(from_currency_id=from_currency.id, to_currency_id=to_currency.id)
    )
    inactive_corridor = await corridor_repository.add(
        build_corridor(
            from_currency_id=to_currency.id,
            to_currency_id=from_currency.id,
            status=CorridorStatus.INACTIVE,
        )
    )

    await rail_repository.add(build_corridor_rail(corridor_id=corridor.id, priority_order=1))
    await rail_repository.add(
        build_corridor_rail(
            corridor_id=corridor.id,
            priority_order=2,
            status=RailStatus.INACTIVE,
        )
    )

    active_corridors = await corridor_repository.list_active()
    active_rails = await rail_repository.list_for_corridor(corridor.id)

    assert [entry.id for entry in active_corridors] == [corridor.id]
    assert inactive_corridor.id not in [entry.id for entry in active_corridors]
    assert [entry.priority_order for entry in active_rails] == [1]


@pytest.mark.anyio
async def test_repository_conflicts_raise_domain_conflict(session: AsyncSession) -> None:
    repository = SqlAlchemyCurrencyRepository(session)

    await repository.add(build_currency(code="USD"))

    with pytest.raises(ConflictError):
        await repository.add(build_currency(code="USD"))


@pytest.mark.anyio
async def test_unit_of_work_rolls_back_when_an_error_occurs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user = build_user(email="rollback@example.com")

    with pytest.raises(RuntimeError):
        async with SqlAlchemyUnitOfWork(session_factory) as uow:
            await uow.users.add(user)
            raise RuntimeError("boom")

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        with pytest.raises(NotFoundError):
            await uow.users.get_by_email(user.email)


@pytest.mark.anyio
async def test_value_objects_are_strictly_validated() -> None:
    from decimal import Decimal

    from app.domain.exceptions import InvariantViolationError
    from app.domain.value_objects import Money, Rate

    money = Money(amount=Decimal("125.50"), currency_code="usd")
    rate = Rate(value=Decimal("1500.25"))

    assert money.currency_code == "USD"
    assert rate.value == Decimal("1500.25")

    with pytest.raises(InvariantViolationError):
        Money(amount=Decimal("-1"), currency_code="usd")
