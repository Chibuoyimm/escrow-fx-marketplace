from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.entities import OutboxEvent
from app.domain.enums import OutboxEventStatus
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from app.services.notification_dispatcher import NotificationDispatchService
from app.services.outbox import build_outbox_event
from tests.conftest import build_user

pytestmark = pytest.mark.anyio


class RecordingProvider:
    """Test provider that records sent events."""

    def __init__(self) -> None:
        self.sent: list[OutboxEvent] = []

    async def send(self, event: OutboxEvent) -> None:
        self.sent.append(event)


class FailingProvider:
    """Test provider that always fails."""

    async def send(self, event: OutboxEvent) -> None:
        raise RuntimeError(f"cannot deliver {event.id}")


async def add_outbox_event(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    email: str,
    event_type: str = "exchange_request.created",
) -> OutboxEvent:
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        user = await uow.users.add(build_user(email=email))
        event = await uow.outbox_events.add(
            build_outbox_event(
                event_type=event_type,
                aggregate_type="exchange_request",
                aggregate_id=uuid4(),
                recipient_user_id=user.id,
                payload={"user_id": str(user.id)},
            )
        )
        await uow.commit()
        return event


async def test_notification_dispatcher_marks_successful_events_delivered(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event = await add_outbox_event(session_factory, email="dispatch-success@example.com")
    provider = RecordingProvider()
    service = NotificationDispatchService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        provider=provider,
    )

    result = await service.dispatch_due()

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        events = await uow.outbox_events.list_admin()

    assert result.claimed == 1
    assert result.delivered == 1
    assert result.failed == 0
    assert [sent.id for sent in provider.sent] == [event.id]
    assert events[0].status is OutboxEventStatus.DELIVERED
    assert events[0].last_error is None


async def test_notification_dispatcher_marks_failures_for_retry(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event = await add_outbox_event(session_factory, email="dispatch-failure@example.com")
    service = NotificationDispatchService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        provider=FailingProvider(),
        max_attempts=3,
        retry_base_seconds=10,
        retry_max_seconds=60,
    )
    result = await service.dispatch_due()

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        events = await uow.outbox_events.list_admin()

    assert result.claimed == 1
    assert result.delivered == 0
    assert result.failed == 1
    assert events[0].id == event.id
    assert events[0].status is OutboxEventStatus.FAILED
    assert events[0].attempt_count == 1
    assert events[0].last_error is not None
    assert "cannot deliver" in events[0].last_error
    assert events[0].next_attempt_at is not None
    assert events[0].next_attempt_at > events[0].updated_at


async def test_notification_dispatcher_marks_exhausted_failures_dead(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event = await add_outbox_event(session_factory, email="dispatch-dead@example.com")
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        await uow.outbox_events.mark_failed(
            event_id=event.id,
            status=OutboxEventStatus.FAILED,
            attempt_count=2,
            last_error="previous failure",
            next_attempt_at=None,
            now=event.created_at,
        )
        await uow.commit()
    service = NotificationDispatchService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        provider=FailingProvider(),
        max_attempts=3,
    )

    result = await service.dispatch_due()

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        events = await uow.outbox_events.list_admin()

    assert result.claimed == 1
    assert result.delivered == 0
    assert result.failed == 1
    assert events[0].status is OutboxEventStatus.DEAD
    assert events[0].attempt_count == 3
    assert events[0].next_attempt_at is None

    second_result = await service.dispatch_due()

    assert second_result.claimed == 0
    assert second_result.delivered == 0
    assert second_result.failed == 0


async def test_notification_dispatcher_respects_batch_limit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first = await add_outbox_event(
        session_factory,
        email="dispatch-limit-one@example.com",
        event_type="exchange_request.created",
    )
    second = await add_outbox_event(
        session_factory,
        email="dispatch-limit-two@example.com",
        event_type="exchange_offer.created",
    )
    provider = RecordingProvider()
    service = NotificationDispatchService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        provider=provider,
    )

    result = await service.dispatch_due(limit=1)

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        events = await uow.outbox_events.list_admin(status=OutboxEventStatus.PENDING)

    assert result.claimed == 1
    assert result.delivered == 1
    assert result.failed == 0
    assert [sent.id for sent in provider.sent] == [first.id]
    assert [event.id for event in events] == [second.id]
