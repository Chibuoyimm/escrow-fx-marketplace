"""Authenticated account-management workflows."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from app.domain.account import normalize_international_phone
from app.domain.entities import AccountAuditEvent, User
from app.domain.enums import AccountAuditEventType, UserStatus
from app.domain.exceptions import AuthenticationError, ConflictError, InvariantViolationError
from app.infrastructure.security import SecurityService
from app.services._shared import UnitOfWorkFactory, build_uow, format_display_datetime, utc_now
from app.services.outbox import OutboxEventPublisher


def build_account_audit_event(
    *,
    subject_user_id: UUID,
    actor_user_id: UUID,
    event_type: AccountAuditEventType,
    occurred_at: datetime,
    metadata: dict[str, Any],
) -> AccountAuditEvent:
    """Build an append-only account audit event."""
    return AccountAuditEvent(
        id=uuid4(),
        subject_user_id=subject_user_id,
        actor_user_id=actor_user_id,
        event_type=event_type,
        occurred_at=occurred_at,
        metadata=metadata,
    )


class AccountService:
    """Application service for customer-owned account changes."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory | None = None,
        security: SecurityService | None = None,
        outbox_publisher: OutboxEventPublisher | None = None,
    ) -> None:
        self._uow_factory = uow_factory or build_uow
        self._security = security or SecurityService()
        self._outbox = outbox_publisher or OutboxEventPublisher()

    async def update_profile(self, *, user_id: UUID, phone: str | None) -> User:
        """Update the authenticated user's permitted profile fields."""
        current_time = utc_now()
        try:
            normalized_phone = normalize_international_phone(phone)
        except ValueError as exc:
            raise InvariantViolationError(str(exc)) from exc
        async with self._uow_factory() as uow:
            user = await uow.users.get_for_update(user_id)
            if user.status is not UserStatus.ACTIVE:
                raise AuthenticationError("Invalid authentication credentials.")
            if user.phone == normalized_phone:
                return user

            saved = await uow.users.update(
                replace(user, phone=normalized_phone, updated_at=current_time)
            )
            await uow.account_audit_events.add(
                build_account_audit_event(
                    subject_user_id=user.id,
                    actor_user_id=user.id,
                    event_type=AccountAuditEventType.PROFILE_UPDATED,
                    occurred_at=current_time,
                    metadata={"changed_fields": ["phone"]},
                )
            )
            await self._outbox.user_profile_updated(
                uow,
                user_id=user.id,
                email=user.email,
                changed_fields=["phone"],
                changed_at=current_time.isoformat(),
                changed_at_display=format_display_datetime(current_time),
            )
            await uow.commit()
            return saved

    async def deactivate(self, *, user_id: UUID, current_password: str) -> User:
        """Soft-deactivate an account after verifying its current password."""
        current_time = utc_now()
        async with self._uow_factory() as uow:
            user = await uow.users.get_for_update(user_id)
            if user.status is not UserStatus.ACTIVE:
                raise AuthenticationError("Invalid authentication credentials.")
            if not self._security.verify_password(current_password, user.password_hash):
                raise AuthenticationError("Invalid authentication credentials.")
            if (
                await uow.exchange_requests.has_actionable_for_creator(user.id, current_time)
                or await uow.exchange_offers.has_active_for_user(user.id, current_time)
                or await uow.trade_contracts.has_non_terminal_for_participant(user.id)
            ):
                raise ConflictError(
                    "Account cannot be deactivated while marketplace obligations are active."
                )

            saved = await uow.users.update(
                replace(user, status=UserStatus.INACTIVE, updated_at=current_time)
            )
            await uow.account_audit_events.add(
                build_account_audit_event(
                    subject_user_id=user.id,
                    actor_user_id=user.id,
                    event_type=AccountAuditEventType.SELF_DEACTIVATED,
                    occurred_at=current_time,
                    metadata={"status_from": user.status.value, "status_to": saved.status.value},
                )
            )
            await self._outbox.user_account_deactivated(
                uow,
                user_id=user.id,
                email=user.email,
                deactivated_at=current_time.isoformat(),
                deactivated_at_display=format_display_datetime(current_time),
            )
            await uow.commit()
            return saved


def get_account_service() -> AccountService:
    """Build the default account service."""
    return AccountService()
