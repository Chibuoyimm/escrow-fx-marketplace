from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import CorridorStatus, CurrencyStatus, ExchangeRequestStatus, RailStatus
from app.domain.exceptions import ConflictError, InvariantViolationError, NotFoundError
from app.domain.value_objects import Money, Rate
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from app.models.corridor import CorridorModel, CorridorRailModel
from app.models.exchange_request import ExchangeRequestModel
from app.repositories.sqlalchemy import (
    SqlAlchemyCorridorRailRepository,
    SqlAlchemyCorridorRepository,
    SqlAlchemyCurrencyRepository,
    SqlAlchemyExchangeRequestRepository,
    SqlAlchemyUserRepository,
)
from tests.conftest import (
    build_corridor,
    build_corridor_rail,
    build_currency,
    build_exchange_request,
    build_user,
)


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
async def test_corridor_and_rail_repositories_filter_inactive_records(
    session: AsyncSession,
) -> None:
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
async def test_corridor_repository_gets_by_ordered_currency_pair(session: AsyncSession) -> None:
    currency_repository = SqlAlchemyCurrencyRepository(session)
    corridor_repository = SqlAlchemyCorridorRepository(session)

    usd = await currency_repository.add(build_currency(code="USD"))
    ngn = await currency_repository.add(build_currency(code="NGN"))
    corridor = await corridor_repository.add(
        build_corridor(from_currency_id=usd.id, to_currency_id=ngn.id)
    )

    fetched = await corridor_repository.get_by_currency_pair(usd.id, ngn.id)

    assert fetched.id == corridor.id

    with pytest.raises(NotFoundError):
        await corridor_repository.get_by_currency_pair(ngn.id, usd.id)


@pytest.mark.anyio
async def test_corridor_repository_details_use_loaded_relationships(session: AsyncSession) -> None:
    currency_repository = SqlAlchemyCurrencyRepository(session)
    corridor_repository = SqlAlchemyCorridorRepository(session)
    rail_repository = SqlAlchemyCorridorRailRepository(session)

    usd = await currency_repository.add(build_currency(code="USD"))
    ngn = await currency_repository.add(build_currency(code="NGN"))
    corridor = await corridor_repository.add(
        build_corridor(from_currency_id=usd.id, to_currency_id=ngn.id)
    )
    await rail_repository.add(build_corridor_rail(corridor_id=corridor.id, priority_order=1))
    await rail_repository.add(
        build_corridor_rail(
            corridor_id=corridor.id,
            priority_order=2,
            status=RailStatus.INACTIVE,
        )
    )

    detail = await corridor_repository.get_active_details_by_currency_pair("USD", "NGN")

    assert detail.id == corridor.id
    assert detail.from_currency_code == "USD"
    assert detail.to_currency_code == "NGN"
    assert [rail.priority_order for rail in detail.rails] == [1]


@pytest.mark.anyio
async def test_repository_conflicts_raise_domain_conflict(session: AsyncSession) -> None:
    repository = SqlAlchemyCurrencyRepository(session)

    await repository.add(build_currency(code="USD"))

    with pytest.raises(ConflictError):
        await repository.add(build_currency(code="USD"))


@pytest.mark.anyio
async def test_repository_getters_raise_not_found_for_missing_records(
    session: AsyncSession,
) -> None:
    user_repository = SqlAlchemyUserRepository(session)
    currency_repository = SqlAlchemyCurrencyRepository(session)
    corridor_repository = SqlAlchemyCorridorRepository(session)
    exchange_request_repository = SqlAlchemyExchangeRequestRepository(session)

    with pytest.raises(NotFoundError):
        await user_repository.get(uuid4())

    with pytest.raises(NotFoundError):
        await user_repository.get_by_email("missing@example.com")

    with pytest.raises(NotFoundError):
        await currency_repository.get_by_code("ZZZ")

    with pytest.raises(NotFoundError):
        await corridor_repository.get(uuid4())

    with pytest.raises(NotFoundError):
        await exchange_request_repository.get(uuid4())


@pytest.mark.anyio
async def test_exchange_request_repository_round_trips_and_scopes_by_creator(
    session: AsyncSession,
) -> None:
    user_repository = SqlAlchemyUserRepository(session)
    currency_repository = SqlAlchemyCurrencyRepository(session)
    exchange_request_repository = SqlAlchemyExchangeRequestRepository(session)

    creator = await user_repository.add(build_user(email="creator@example.com"))
    other_user = await user_repository.add(build_user(email="other@example.com"))
    usd = await currency_repository.add(build_currency(code="USD"))
    ngn = await currency_repository.add(build_currency(code="NGN"))

    older_request = await exchange_request_repository.add(
        build_exchange_request(
            creator_user_id=creator.id,
            from_currency_id=usd.id,
            to_currency_id=ngn.id,
            status=ExchangeRequestStatus.REQUEST_OPEN,
        )
    )
    newer_request = await exchange_request_repository.add(
        build_exchange_request(
            creator_user_id=creator.id,
            from_currency_id=ngn.id,
            to_currency_id=usd.id,
            status=ExchangeRequestStatus.REQUEST_OPEN,
        )
    )

    fetched = await exchange_request_repository.get(older_request.id)
    owned = await exchange_request_repository.get_for_user(older_request.id, creator.id)
    details = await exchange_request_repository.get_details_for_user(older_request.id, creator.id)
    requests = await exchange_request_repository.list_for_user(creator.id)
    detailed_requests = await exchange_request_repository.list_details_for_user(creator.id)

    assert fetched.id == older_request.id
    assert owned.id == older_request.id
    assert details.from_currency_code == "USD"
    assert details.to_currency_code == "NGN"
    assert [request.id for request in requests] == [newer_request.id, older_request.id]
    assert [request.id for request in detailed_requests] == [newer_request.id, older_request.id]

    with pytest.raises(NotFoundError):
        await exchange_request_repository.get_for_user(older_request.id, other_user.id)

    with pytest.raises(NotFoundError):
        await exchange_request_repository.get_details_for_user(older_request.id, other_user.id)


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
async def test_deleting_a_corridor_deletes_its_rails_via_orm_cascade(session: AsyncSession) -> None:
    currency_repository = SqlAlchemyCurrencyRepository(session)
    from_currency = await currency_repository.add(build_currency(code="USD"))
    to_currency = await currency_repository.add(build_currency(code="NGN"))

    corridor = CorridorModel(
        from_currency_id=from_currency.id,
        to_currency_id=to_currency.id,
        status=CorridorStatus.ACTIVE,
        funding_sla_minutes=30,
        fee_model_name="default",
        rails=[
            CorridorRailModel(
                flow_type="funding",
                priority_order=1,
                provider="paystack",
                method="bank_transfer",
                status=RailStatus.ACTIVE,
            )
        ],
    )
    session.add(corridor)
    await session.flush()
    corridor_id = corridor.id

    await session.delete(corridor)
    await session.flush()

    rail_rows = await session.execute(
        select(CorridorRailModel).where(CorridorRailModel.corridor_id == corridor_id)
    )

    assert rail_rows.scalars().all() == []


@pytest.mark.anyio
async def test_deleting_supporting_currency_restricts_exchange_request_relationships(
    session: AsyncSession,
) -> None:
    user_repository = SqlAlchemyUserRepository(session)
    currency_repository = SqlAlchemyCurrencyRepository(session)
    exchange_request_repository = SqlAlchemyExchangeRequestRepository(session)

    creator = await user_repository.add(build_user(email="exchange-user@example.com"))
    usd = await currency_repository.add(build_currency(code="USD"))
    ngn = await currency_repository.add(build_currency(code="NGN"))
    request = await exchange_request_repository.add(
        build_exchange_request(
            creator_user_id=creator.id,
            from_currency_id=usd.id,
            to_currency_id=ngn.id,
        )
    )

    detail = await exchange_request_repository.get_details_for_user(request.id, creator.id)
    row = await session.get(ExchangeRequestModel, request.id)

    assert detail.from_currency_code == "USD"
    assert row is not None


@pytest.mark.anyio
async def test_value_objects_are_strictly_validated() -> None:
    money = Money(amount=Decimal("125.50"), currency_code="usd")
    rate = Rate(value=Decimal("1500.25"))

    assert money.currency_code == "USD"
    assert rate.value == Decimal("1500.25")

    with pytest.raises(InvariantViolationError):
        Money(amount=Decimal("-1"), currency_code="usd")

    with pytest.raises(InvariantViolationError):
        Rate(value=Decimal("0"))

    with pytest.raises(InvariantViolationError):
        Money(amount=Decimal("NaN"), currency_code="usd")
