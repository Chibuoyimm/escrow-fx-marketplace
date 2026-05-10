"""Notification outbox dispatcher service."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from app.domain.entities import OutboxEvent
from app.domain.enums import OutboxEventStatus
from app.infrastructure.config import settings
from app.services._shared import UnitOfWorkFactory, build_uow, utc_now


class NotificationProvider(Protocol):
    """Provider contract for sending one outbox event."""

    async def send(self, event: OutboxEvent) -> None:
        """Send a notification for an outbox event."""


class LoggingNotificationProvider:
    """Development provider that records dispatch by printing the event."""

    async def send(self, event: OutboxEvent) -> None:
        """Pretend to deliver an event while keeping local dispatch usable."""
        print(
            "Notification dispatched: "
            f"{event.event_type} event_id={event.id} recipient_user_id={event.recipient_user_id}"
        )


@dataclass(frozen=True, slots=True)
class NotificationDispatchResult:
    """Summary of one notification dispatch pass."""

    claimed: int
    delivered: int
    failed: int


class NotificationDispatchService:
    """Dispatch pending outbox notification events."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory | None = None,
        provider: NotificationProvider | None = None,
        batch_size: int | None = None,
        processing_timeout_seconds: int | None = None,
        max_attempts: int | None = None,
        retry_base_seconds: int | None = None,
        retry_max_seconds: int | None = None,
    ) -> None:
        self._uow_factory = uow_factory or build_uow
        self._provider = provider or build_notification_provider(self._uow_factory)
        self._batch_size: int = int(batch_size or settings.notification_dispatch_batch_size)
        self._processing_timeout_seconds: int = int(
            processing_timeout_seconds or settings.notification_processing_timeout_seconds
        )
        self._max_attempts: int = int(max_attempts or settings.notification_max_attempts)
        self._retry_base_seconds: int = int(
            retry_base_seconds or settings.notification_retry_base_seconds
        )
        self._retry_max_seconds: int = int(
            retry_max_seconds or settings.notification_retry_max_seconds
        )

    async def dispatch_due(self, *, limit: int | None = None) -> NotificationDispatchResult:
        """Dispatch due outbox events and update their delivery state."""
        current_time = utc_now()
        batch_limit = limit or self._batch_size
        processing_deadline = current_time + timedelta(seconds=self._processing_timeout_seconds)

        async with self._uow_factory() as uow:
            events = await uow.outbox_events.claim_due_for_dispatch(
                now=current_time,
                processing_deadline=processing_deadline,
                limit=batch_limit,
            )
            await uow.commit()

        delivered = 0
        failed = 0
        for event in events:
            try:
                await self._provider.send(event)
            except Exception as exc:  # noqa: BLE001 - provider errors must not stop the batch.
                failed += 1
                await self._mark_failed(event, exc)
            else:
                delivered += 1
                await self._mark_delivered(event)

        return NotificationDispatchResult(
            claimed=len(events),
            delivered=delivered,
            failed=failed,
        )

    async def _mark_delivered(self, event: OutboxEvent) -> None:
        current_time = utc_now()
        async with self._uow_factory() as uow:
            await uow.outbox_events.mark_delivered(event.id, current_time)
            await uow.commit()

    async def _mark_failed(self, event: OutboxEvent, exc: Exception) -> None:
        current_time = utc_now()
        attempt_count = event.attempt_count + 1
        exhausted_retries = attempt_count >= self._max_attempts
        status = OutboxEventStatus.DEAD if exhausted_retries else OutboxEventStatus.FAILED
        next_attempt_at = (
            None
            if exhausted_retries
            else current_time + timedelta(seconds=self._retry_delay_seconds(attempt_count))
        )
        async with self._uow_factory() as uow:
            await uow.outbox_events.mark_failed(
                event_id=event.id,
                status=status,
                attempt_count=attempt_count,
                last_error=str(exc) or exc.__class__.__name__,
                next_attempt_at=next_attempt_at,
                now=current_time,
            )
            await uow.commit()

    def _retry_delay_seconds(self, attempt_count: int) -> int:
        delay: int = self._retry_base_seconds * (2 ** max(attempt_count - 1, 0))
        return min(delay, self._retry_max_seconds)


def get_notification_dispatch_service() -> NotificationDispatchService:
    """Build the default notification dispatch service."""
    return NotificationDispatchService()


def build_notification_provider(
    uow_factory: UnitOfWorkFactory | None = None,
) -> NotificationProvider:
    """Build the configured notification provider."""
    provider = settings.notification_provider.strip().lower()
    if provider in {"logging", "log"}:
        return LoggingNotificationProvider()
    if provider == "knock":
        from app.integrations.knock import KnockNotificationProvider

        return KnockNotificationProvider(uow_factory=uow_factory)
    raise RuntimeError(f"Unsupported notification provider '{settings.notification_provider}'.")


NotificationDispatchServiceFactory = Callable[[], NotificationDispatchService]
