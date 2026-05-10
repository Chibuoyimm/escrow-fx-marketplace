from __future__ import annotations

from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.entities import OutboxEvent
from app.domain.enums import OutboxEventStatus
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from app.integrations.knock import KnockNotificationProvider
from app.services.notification_dispatcher import (
    LoggingNotificationProvider,
    NotificationDispatchService,
    build_notification_provider,
)
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


class FakeKnockWorkflows:
    """Test double for the Knock workflow SDK resource."""

    def __init__(self, *, should_fail: bool = False) -> None:
        self.calls: list[dict[str, object]] = []
        self.should_fail = should_fail

    async def trigger(
        self,
        key: str,
        *,
        recipients: object,
        data: dict[str, object],
        idempotency_key: str | None = None,
    ) -> object:
        if self.should_fail:
            raise RuntimeError("knock workflow trigger failed")
        self.calls.append(
            {
                "key": key,
                "recipients": recipients,
                "data": data,
                "idempotency_key": idempotency_key,
            }
        )
        return None


class FakeKnockUsers:
    """Test double for the Knock user SDK resource."""

    def __init__(self, *, should_fail: bool = False) -> None:
        self.calls: list[dict[str, object]] = []
        self.should_fail = should_fail

    async def update(
        self,
        user_id: str,
        *,
        email: str,
        name: str,
        phone_number: str | None = None,
        idempotency_key: str | None = None,
    ) -> object:
        if self.should_fail:
            raise RuntimeError("knock user upsert failed")
        self.calls.append(
            {
                "user_id": user_id,
                "email": email,
                "name": name,
                "phone_number": phone_number,
                "idempotency_key": idempotency_key,
            }
        )
        return None


class FakeKnockClient:
    """Test double for the Knock SDK client."""

    def __init__(
        self,
        *,
        users_should_fail: bool = False,
        workflows_should_fail: bool = False,
    ) -> None:
        self.users = FakeKnockUsers(should_fail=users_should_fail)
        self.workflows = FakeKnockWorkflows(should_fail=workflows_should_fail)


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


async def add_outbox_event_without_recipient(
    session_factory: async_sessionmaker[AsyncSession],
) -> OutboxEvent:
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        event = await uow.outbox_events.add(
            build_outbox_event(
                event_type="marketplace_expiry.completed",
                aggregate_type="marketplace_expiry",
                aggregate_id=uuid4(),
                recipient_user_id=None,
                payload={"expired_requests": 1},
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


async def test_knock_provider_triggers_matching_workflow_with_rendering_data(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        user = await uow.users.add(build_user(email="recipient@example.com"))
        event = await uow.outbox_events.add(
            build_outbox_event(
                event_type="exchange_request.created",
                aggregate_type="exchange_request",
                aggregate_id=uuid4(),
                recipient_user_id=user.id,
                payload={
                    "request_id": "request-123",
                    "from_amount": "250000",
                    "from_currency_code": "NGN",
                },
            )
        )
        await uow.commit()

    client = FakeKnockClient()
    provider = KnockNotificationProvider(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        client=client,
    )

    await provider.send(event)

    assert client.users.calls == [
        {
            "user_id": str(user.id),
            "email": "recipient@example.com",
            "name": "recipient",
            "phone_number": user.phone,
            "idempotency_key": f"{event.id}:recipient-upsert",
        }
    ]
    assert len(client.workflows.calls) == 1
    call = client.workflows.calls[0]
    recipients = cast(list[dict[str, object]], call["recipients"])
    data = cast(dict[str, object], call["data"])
    assert call["key"] == "exchange-request-created"
    assert call["idempotency_key"] == str(event.id)
    assert isinstance(recipients, list)
    assert recipients[0]["id"] == str(user.id)
    assert recipients[0]["email"] == "recipient@example.com"
    assert data["USER_NAME"] == "recipient"
    assert data["REQUEST_ID"] == "request-123"
    assert data["REQUEST_URL"] == "http://localhost:8000/api/v1/exchange-requests/request-123"
    assert data["FROM_AMOUNT"] == "250000"
    assert data["FROM_CURRENCY_CODE"] == "NGN"
    assert data["BOARD_URL"] == "http://localhost:8000/api/v1/exchange-requests/board"
    assert data["CREATE_REQUEST_URL"] == "http://localhost:8000/api/v1/exchange-requests"
    assert data["EVENT_ID"] == str(event.id)
    assert data["EVENT_TYPE"] == "exchange_request.created"
    assert data["AGGREGATE_TYPE"] == "exchange_request"
    assert data["RECIPIENT_EMAIL"] == "recipient@example.com"
    assert "request_id" not in data
    assert "event_id" not in data
    assert "template_variables" not in data


async def test_knock_provider_skips_events_without_recipient(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event = await add_outbox_event_without_recipient(session_factory)
    client = FakeKnockClient()
    provider = KnockNotificationProvider(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        client=client,
    )

    await provider.send(event)

    assert client.users.calls == []
    assert client.workflows.calls == []


async def test_knock_provider_adds_trade_and_offer_aliases_and_safe_payload_values(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        user = await uow.users.add(build_user(email="trade-recipient@example.com"))
        await uow.commit()
    event = build_outbox_event(
        event_type="trade_contract.locked",
        aggregate_type="trade_contract",
        aggregate_id=uuid4(),
        recipient_user_id=user.id,
        payload={
            "trade_contract_id": "trade-123",
            "accepted_offer_id": "offer-456",
            "request_id": "request-789",
            "metadata": {"nested": uuid4()},
            "items": [uuid4(), "kept"],
        },
    )

    client = FakeKnockClient()
    provider = KnockNotificationProvider(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        client=client,
    )

    await provider.send(event)

    call = client.workflows.calls[0]
    data = cast(dict[str, object], call["data"])
    assert call["key"] == "trade-contract-locked"
    assert data["TRADE_CONTRACT_ID"] == "trade-123"
    assert data["TRADE_ID"] == "trade-123"
    assert data["ACCEPTED_OFFER_ID"] == "offer-456"
    assert data["OFFER_ID"] == "offer-456"
    assert data["REQUEST_URL"] == "http://localhost:8000/api/v1/exchange-requests/request-789"
    assert data["TRADE_URL"] == "http://localhost:8000/api/v1/trades/trade-123"
    assert isinstance(data["METADATA"], dict)
    metadata = cast(dict[str, object], data["METADATA"])
    assert isinstance(metadata["nested"], str)
    assert isinstance(data["ITEMS"], list)
    items = cast(list[object], data["ITEMS"])
    assert isinstance(items[0], str)
    assert items[1] == "kept"


async def test_knock_user_upsert_failure_schedules_retry(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event = await add_outbox_event(session_factory, email="knock-upsert-failure@example.com")
    client = FakeKnockClient(users_should_fail=True)
    provider = KnockNotificationProvider(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        client=client,
    )
    service = NotificationDispatchService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        provider=provider,
        max_attempts=3,
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
    assert "knock user upsert failed" in events[0].last_error
    assert client.workflows.calls == []


async def test_knock_workflow_trigger_failure_schedules_retry(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event = await add_outbox_event(session_factory, email="knock-trigger-failure@example.com")
    client = FakeKnockClient(workflows_should_fail=True)
    provider = KnockNotificationProvider(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        client=client,
    )
    service = NotificationDispatchService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        provider=provider,
        max_attempts=3,
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
    assert "knock workflow trigger failed" in events[0].last_error
    assert len(client.users.calls) == 1


async def test_build_notification_provider_returns_logging_provider_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.notification_dispatcher.settings.notification_provider",
        "logging",
    )

    provider = build_notification_provider()

    assert isinstance(provider, LoggingNotificationProvider)


async def test_build_notification_provider_rejects_unknown_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.notification_dispatcher.settings.notification_provider",
        "carrier-pigeon",
    )

    with pytest.raises(RuntimeError, match="Unsupported notification provider"):
        build_notification_provider()


async def test_knock_provider_requires_api_key_when_building_sdk_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.integrations.knock.settings.knock_api_key", None)

    with pytest.raises(RuntimeError, match="APP_KNOCK_API_KEY"):
        KnockNotificationProvider()
