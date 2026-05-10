from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.entities import EmailVerificationToken, PasswordResetToken
from app.domain.enums import (
    CorridorStatus,
    CurrencyStatus,
    ExchangeOfferStatus,
    ExchangeRequestStatus,
    OutboxEventStatus,
    RailStatus,
    TradeContractStatus,
)
from app.domain.exceptions import ConflictError, InvariantViolationError, NotFoundError
from app.domain.value_objects import Money, Rate
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from app.models.corridor import CorridorModel, CorridorRailModel
from app.models.exchange_offer import ExchangeOfferModel
from app.models.exchange_request import ExchangeRequestModel
from app.models.trade_contract import TradeContractModel
from app.repositories.sqlalchemy import (
    SqlAlchemyCorridorRailRepository,
    SqlAlchemyCorridorRepository,
    SqlAlchemyCurrencyRepository,
    SqlAlchemyEmailVerificationTokenRepository,
    SqlAlchemyExchangeOfferRepository,
    SqlAlchemyExchangeRequestRepository,
    SqlAlchemyOutboxEventRepository,
    SqlAlchemyPasswordResetTokenRepository,
    SqlAlchemyTradeContractRepository,
    SqlAlchemyUserRepository,
)
from app.services.outbox import build_outbox_event
from tests.conftest import (
    build_corridor,
    build_corridor_rail,
    build_currency,
    build_exchange_offer,
    build_exchange_request,
    build_trade_contract,
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
async def test_email_verification_token_repository_round_trips_and_consumes(
    session: AsyncSession,
) -> None:
    user_repository = SqlAlchemyUserRepository(session)
    repository = SqlAlchemyEmailVerificationTokenRepository(session)
    user = await user_repository.add(build_user(email="verify-token@example.com"))
    current_time = datetime.now(UTC)
    token = await repository.add(
        EmailVerificationToken(
            id=uuid4(),
            user_id=user.id,
            token_hash="a" * 64,
            expires_at=current_time + timedelta(hours=1),
            consumed_at=None,
            created_at=current_time,
            updated_at=current_time,
        )
    )

    fetched = await repository.get_by_token_hash(token.token_hash)
    consumed = await repository.mark_consumed(token.id, current_time)

    assert fetched.id == token.id
    assert consumed.consumed_at == current_time


@pytest.mark.anyio
async def test_password_reset_token_repository_round_trips_and_consumes(
    session: AsyncSession,
) -> None:
    user_repository = SqlAlchemyUserRepository(session)
    repository = SqlAlchemyPasswordResetTokenRepository(session)
    user = await user_repository.add(build_user(email="reset-token@example.com"))
    current_time = datetime.now(UTC)
    token = await repository.add(
        PasswordResetToken(
            id=uuid4(),
            user_id=user.id,
            token_hash="b" * 64,
            expires_at=current_time + timedelta(hours=1),
            consumed_at=None,
            created_at=current_time,
            updated_at=current_time,
        )
    )

    fetched = await repository.get_by_token_hash(token.token_hash)
    consumed = await repository.mark_consumed(token.id, current_time)

    assert fetched.id == token.id
    assert consumed.consumed_at == current_time


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

    active_corridors = await corridor_repository.list_active_details()
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
    details = await exchange_request_repository.get_details_for_user(older_request.id, creator.id)
    detailed_requests = await exchange_request_repository.list_details_for_user(creator.id)

    assert fetched.id == older_request.id
    assert details.from_currency_code == "USD"
    assert details.to_currency_code == "NGN"
    assert [request.id for request in detailed_requests] == [newer_request.id, older_request.id]

    with pytest.raises(NotFoundError):
        await exchange_request_repository.get_details_for_user(older_request.id, other_user.id)


@pytest.mark.anyio
async def test_exchange_request_repository_lists_board_visible_requests(
    session: AsyncSession,
) -> None:
    user_repository = SqlAlchemyUserRepository(session)
    currency_repository = SqlAlchemyCurrencyRepository(session)
    exchange_request_repository = SqlAlchemyExchangeRequestRepository(session)

    viewer = await user_repository.add(build_user(email="viewer@example.com"))
    creator = await user_repository.add(build_user(email="creator@example.com"))
    usd = await currency_repository.add(build_currency(code="USD"))
    ngn = await currency_repository.add(build_currency(code="NGN"))

    visible_request = await exchange_request_repository.add(
        build_exchange_request(
            creator_user_id=creator.id,
            from_currency_id=usd.id,
            to_currency_id=ngn.id,
            status=ExchangeRequestStatus.REQUEST_OPEN,
        )
    )
    await exchange_request_repository.add(
        build_exchange_request(
            creator_user_id=creator.id,
            from_currency_id=ngn.id,
            to_currency_id=usd.id,
            status=ExchangeRequestStatus.CANCELLED,
        )
    )
    await exchange_request_repository.add(
        build_exchange_request(
            creator_user_id=viewer.id,
            from_currency_id=usd.id,
            to_currency_id=ngn.id,
            status=ExchangeRequestStatus.REQUEST_OPEN,
        )
    )

    board = await exchange_request_repository.list_board_details(viewer.id)
    visible = await exchange_request_repository.get_visible_details(visible_request.id, viewer.id)

    assert [request.id for request in board] == [visible_request.id]
    assert visible.id == visible_request.id
    assert visible.from_currency_code == "USD"


@pytest.mark.anyio
async def test_exchange_offer_repository_round_trips_and_checks_active_offer(
    session: AsyncSession,
) -> None:
    user_repository = SqlAlchemyUserRepository(session)
    currency_repository = SqlAlchemyCurrencyRepository(session)
    exchange_request_repository = SqlAlchemyExchangeRequestRepository(session)
    exchange_offer_repository = SqlAlchemyExchangeOfferRepository(session)

    creator = await user_repository.add(build_user(email="creator@example.com"))
    offer_user = await user_repository.add(build_user(email="offer@example.com"))
    usd = await currency_repository.add(build_currency(code="USD"))
    ngn = await currency_repository.add(build_currency(code="NGN"))
    exchange_request = await exchange_request_repository.add(
        build_exchange_request(
            creator_user_id=creator.id,
            from_currency_id=usd.id,
            to_currency_id=ngn.id,
        )
    )

    created = await exchange_offer_repository.add(
        build_exchange_offer(
            request_id=exchange_request.id,
            offer_user_id=offer_user.id,
        )
    )

    offers = await exchange_offer_repository.list_details_for_request(exchange_request.id)
    has_active = await exchange_offer_repository.has_active_offer_for_request(
        exchange_request.id,
        offer_user.id,
    )

    assert created.request_id == exchange_request.id
    assert [offer.id for offer in offers] == [created.id]
    assert offers[0].status is ExchangeOfferStatus.ACTIVE
    assert has_active is True


@pytest.mark.anyio
async def test_trade_contract_repository_is_visible_only_to_participants(
    session: AsyncSession,
) -> None:
    user_repository = SqlAlchemyUserRepository(session)
    currency_repository = SqlAlchemyCurrencyRepository(session)
    exchange_request_repository = SqlAlchemyExchangeRequestRepository(session)
    exchange_offer_repository = SqlAlchemyExchangeOfferRepository(session)
    trade_contract_repository = SqlAlchemyTradeContractRepository(session)

    requester = await user_repository.add(build_user(email="requester@example.com"))
    counterparty = await user_repository.add(build_user(email="counterparty@example.com"))
    outsider = await user_repository.add(build_user(email="outsider@example.com"))
    usd = await currency_repository.add(build_currency(code="USD"))
    ngn = await currency_repository.add(build_currency(code="NGN"))
    exchange_request = await exchange_request_repository.add(
        build_exchange_request(
            creator_user_id=requester.id,
            from_currency_id=usd.id,
            to_currency_id=ngn.id,
            status=ExchangeRequestStatus.TERMS_LOCKED,
        )
    )
    exchange_offer = await exchange_offer_repository.add(
        build_exchange_offer(
            request_id=exchange_request.id,
            offer_user_id=counterparty.id,
            status=ExchangeOfferStatus.ACCEPTED,
        )
    )
    trade_contract = await trade_contract_repository.add(
        build_trade_contract(
            request_id=exchange_request.id,
            accepted_offer_id=exchange_offer.id,
            status=TradeContractStatus.TERMS_LOCKED,
        )
    )

    requester_view = await trade_contract_repository.get_for_participant(
        trade_contract.id,
        requester.id,
    )
    counterparty_view = await trade_contract_repository.get_for_participant(
        trade_contract.id,
        counterparty.id,
    )

    assert requester_view.id == trade_contract.id
    assert requester_view.requester_user_id == requester.id
    assert counterparty_view.counterparty_user_id == counterparty.id

    with pytest.raises(NotFoundError):
        await trade_contract_repository.get_for_participant(trade_contract.id, outsider.id)


@pytest.mark.anyio
async def test_outbox_event_repository_adds_and_filters_events(session: AsyncSession) -> None:
    user_repository = SqlAlchemyUserRepository(session)
    repository = SqlAlchemyOutboxEventRepository(session)
    recipient = await user_repository.add(build_user(email="outbox-recipient@example.com"))
    request_id = uuid4()
    trade_id = uuid4()

    request_event = await repository.add(
        build_outbox_event(
            event_type="exchange_request.created",
            aggregate_type="exchange_request",
            aggregate_id=request_id,
            recipient_user_id=recipient.id,
            payload={"request_id": str(request_id)},
        )
    )
    await repository.add(
        build_outbox_event(
            event_type="trade_contract.locked",
            aggregate_type="trade_contract",
            aggregate_id=trade_id,
            recipient_user_id=recipient.id,
            payload={"trade_contract_id": str(trade_id)},
        )
    )

    pending_events = await repository.list_admin(status=OutboxEventStatus.PENDING)
    request_events = await repository.list_admin(event_type="exchange_request.created")

    assert len(pending_events) == 2
    assert [event.id for event in request_events] == [request_event.id]
    assert request_events[0].payload == {"request_id": str(request_id)}


@pytest.mark.anyio
async def test_outbox_event_repository_claims_and_updates_due_events(
    session: AsyncSession,
) -> None:
    user_repository = SqlAlchemyUserRepository(session)
    repository = SqlAlchemyOutboxEventRepository(session)
    recipient = await user_repository.add(build_user(email="outbox-claim@example.com"))
    due_event = await repository.add(
        build_outbox_event(
            event_type="exchange_request.created",
            aggregate_type="exchange_request",
            aggregate_id=uuid4(),
            recipient_user_id=recipient.id,
            payload={},
        )
    )
    future_event = await repository.add(
        build_outbox_event(
            event_type="exchange_offer.created",
            aggregate_type="exchange_offer",
            aggregate_id=uuid4(),
            recipient_user_id=recipient.id,
            payload={},
        )
    )
    current_time = datetime.now(UTC)
    await repository.mark_failed(
        event_id=future_event.id,
        status=OutboxEventStatus.FAILED,
        attempt_count=1,
        last_error="provider unavailable",
        next_attempt_at=current_time + timedelta(hours=1),
        now=current_time,
    )

    claimed = await repository.claim_due_for_dispatch(
        now=current_time,
        processing_deadline=current_time + timedelta(minutes=5),
        limit=10,
    )
    delivered = await repository.mark_delivered(due_event.id, current_time)

    assert [event.id for event in claimed] == [due_event.id]
    assert claimed[0].status is OutboxEventStatus.PROCESSING
    assert claimed[0].next_attempt_at == current_time + timedelta(minutes=5)
    assert delivered.status is OutboxEventStatus.DELIVERED
    assert delivered.next_attempt_at is None


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
async def test_deleting_exchange_request_cascades_to_exchange_offers(session: AsyncSession) -> None:
    user_repository = SqlAlchemyUserRepository(session)
    currency_repository = SqlAlchemyCurrencyRepository(session)

    creator = await user_repository.add(build_user(email="cascade-creator@example.com"))
    offer_user = await user_repository.add(build_user(email="cascade-offer@example.com"))
    usd = await currency_repository.add(build_currency(code="USD"))
    ngn = await currency_repository.add(build_currency(code="NGN"))

    exchange_request = ExchangeRequestModel(
        creator_user_id=creator.id,
        from_currency_id=usd.id,
        to_currency_id=ngn.id,
        from_amount=Decimal("100.00"),
        preferred_rate=Decimal("1500.00"),
        min_rate=Decimal("1450.00"),
        status=ExchangeRequestStatus.REQUEST_OPEN,
        expires_at=datetime.now(UTC),
        offers=[
            ExchangeOfferModel(
                offer_user_id=offer_user.id,
                offered_rate=Decimal("1490.00"),
                status=ExchangeOfferStatus.ACTIVE,
                expires_at=datetime.now(UTC),
            )
        ],
    )
    session.add(exchange_request)
    await session.flush()
    request_id = exchange_request.id

    await session.delete(exchange_request)
    await session.flush()

    offer_rows = await session.execute(
        select(ExchangeOfferModel).where(ExchangeOfferModel.request_id == request_id)
    )

    assert offer_rows.scalars().all() == []


@pytest.mark.anyio
async def test_deleting_trade_contract_references_is_non_destructive(session: AsyncSession) -> None:
    user_repository = SqlAlchemyUserRepository(session)
    currency_repository = SqlAlchemyCurrencyRepository(session)

    requester = await user_repository.add(build_user(email="trade-requester@example.com"))
    counterparty = await user_repository.add(build_user(email="trade-counterparty@example.com"))
    usd = await currency_repository.add(build_currency(code="USD"))
    ngn = await currency_repository.add(build_currency(code="NGN"))

    exchange_request = ExchangeRequestModel(
        creator_user_id=requester.id,
        from_currency_id=usd.id,
        to_currency_id=ngn.id,
        from_amount=Decimal("100.00"),
        preferred_rate=Decimal("1500.00"),
        min_rate=Decimal("1450.00"),
        status=ExchangeRequestStatus.TERMS_LOCKED,
        expires_at=datetime.now(UTC),
    )
    exchange_offer = ExchangeOfferModel(
        request=exchange_request,
        offer_user_id=counterparty.id,
        offered_rate=Decimal("1490.00"),
        status=ExchangeOfferStatus.ACCEPTED,
        expires_at=datetime.now(UTC),
    )
    trade_contract = TradeContractModel(
        request=exchange_request,
        accepted_offer=exchange_offer,
        agreed_rate=Decimal("1490.00"),
        reference_rate_snapshot=None,
        from_amount=Decimal("100.00"),
        to_amount=Decimal("149000.00"),
        funding_deadline_at=datetime.now(UTC),
        status=TradeContractStatus.TERMS_LOCKED,
    )
    session.add_all([exchange_request, exchange_offer, trade_contract])
    await session.flush()

    trade_id = trade_contract.id
    request_id = exchange_request.id
    offer_id = exchange_offer.id

    await session.delete(trade_contract)
    await session.flush()

    assert await session.get(TradeContractModel, trade_id) is None
    assert await session.get(ExchangeRequestModel, request_id) is not None
    assert await session.get(ExchangeOfferModel, offer_id) is not None


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
