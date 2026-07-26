"""Admin read service layer."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from enum import Enum
from uuid import UUID

from app.domain.entities import (
    ExchangeOfferDetails,
    ExchangeRequestDetails,
    KycVerification,
    OutboxEvent,
    TradeContractDetails,
    User,
)
from app.domain.enums import (
    AccountAuditEventType,
    ExchangeOfferStatus,
    ExchangeRequestStatus,
    KycVerificationStatus,
    OutboxEventStatus,
    TradeContractStatus,
    UserRole,
    UserStatus,
)
from app.domain.exceptions import AuthorizationError, InvariantViolationError
from app.infrastructure.pagination import (
    decode_cursor,
    encode_next_cursor,
    normalize_date_range,
)
from app.services._shared import (
    UnitOfWorkFactory,
    build_uow,
    format_display_datetime,
    lock_users_in_order,
    utc_now,
)
from app.services.account import build_account_audit_event
from app.services.outbox import OutboxEventPublisher


def resolve_status_filters[StatusT: Enum](
    status: StatusT | None,
    statuses: list[StatusT] | None,
) -> tuple[StatusT, ...] | None:
    """Resolve legacy singular and paginated repeated status filters."""
    if status is not None and statuses:
        raise InvariantViolationError("Use either status or statuses, not both.")
    if statuses:
        return tuple(statuses)
    return (status,) if status is not None else None


class AdminService:
    """Application service for admin inspection and account status management."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory | None = None,
        outbox_publisher: OutboxEventPublisher | None = None,
    ) -> None:
        self._uow_factory = uow_factory or build_uow
        self._outbox = outbox_publisher or OutboxEventPublisher()

    async def update_user_status(
        self,
        *,
        subject_user_id: UUID,
        actor_user_id: UUID,
        status: UserStatus,
    ) -> User:
        """Suspend or reactivate a user in the same transaction as its audit event."""
        if subject_user_id == actor_user_id:
            raise InvariantViolationError("Administrators cannot change their own account status.")
        if status is UserStatus.INACTIVE:
            raise InvariantViolationError("Use the account deactivation flow for inactive status.")

        current_time = utc_now()
        async with self._uow_factory() as uow:
            users = await lock_users_in_order(uow, (actor_user_id, subject_user_id))
            actor = users[actor_user_id]
            user = users[subject_user_id]
            if actor.status is not UserStatus.ACTIVE or actor.role is not UserRole.ADMIN:
                raise AuthorizationError("Administrator access is required.")
            if user.status is status:
                return user
            if status is UserStatus.SUSPENDED and user.status is not UserStatus.ACTIVE:
                raise InvariantViolationError("Only active users can be suspended.")
            if status is UserStatus.ACTIVE and user.status not in (
                UserStatus.SUSPENDED,
                UserStatus.INACTIVE,
            ):
                raise InvariantViolationError(
                    "Only suspended or inactive users can be reactivated."
                )

            saved = await uow.users.update(replace(user, status=status, updated_at=current_time))
            event_type = (
                AccountAuditEventType.ADMIN_SUSPENDED
                if status is UserStatus.SUSPENDED
                else AccountAuditEventType.ADMIN_REACTIVATED
            )
            await uow.account_audit_events.add(
                build_account_audit_event(
                    subject_user_id=user.id,
                    actor_user_id=actor_user_id,
                    event_type=event_type,
                    occurred_at=current_time,
                    metadata={"status_from": user.status.value, "status_to": status.value},
                )
            )
            if status is UserStatus.SUSPENDED:
                await self._outbox.user_account_suspended(
                    uow,
                    user_id=user.id,
                    email=user.email,
                    changed_at=current_time.isoformat(),
                    changed_at_display=format_display_datetime(current_time),
                )
            else:
                await self._outbox.user_account_reactivated(
                    uow,
                    user_id=user.id,
                    email=user.email,
                    changed_at=current_time.isoformat(),
                    changed_at_display=format_display_datetime(current_time),
                )
            await uow.commit()
            return saved

    async def list_users(self, status: UserStatus | None = None) -> list[User]:
        """List users for admin inspection."""
        async with self._uow_factory() as uow:
            return await uow.users.list_all(status)

    async def list_users_page(
        self, *, status: UserStatus | None = None, cursor: str | None = None, limit: int = 50
    ) -> tuple[list[User], str | None]:
        async with self._uow_factory() as uow:
            items, position = await uow.users.list_page(
                status=status, cursor=decode_cursor(cursor), limit=limit
            )
            return items, encode_next_cursor(position)

    async def list_exchange_requests_page(
        self,
        *,
        statuses: tuple[ExchangeRequestStatus, ...] | None = None,
        cursor: str | None = None,
        limit: int = 50,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[list[ExchangeRequestDetails], str | None]:
        created_from, created_to = normalize_date_range(created_from, created_to)
        async with self._uow_factory() as uow:
            items, position = await uow.exchange_requests.list_admin_details_page(
                statuses=statuses,
                cursor=decode_cursor(cursor),
                limit=limit,
                created_from=created_from,
                created_to=created_to,
            )
            return items, encode_next_cursor(position)

    async def list_exchange_offers_page(
        self,
        *,
        statuses: tuple[ExchangeOfferStatus, ...] | None = None,
        cursor: str | None = None,
        limit: int = 50,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[list[ExchangeOfferDetails], str | None]:
        created_from, created_to = normalize_date_range(created_from, created_to)
        async with self._uow_factory() as uow:
            items, position = await uow.exchange_offers.list_admin_details_page(
                statuses=statuses,
                cursor=decode_cursor(cursor),
                limit=limit,
                created_from=created_from,
                created_to=created_to,
            )
            return items, encode_next_cursor(position)

    async def list_trades_page(
        self,
        *,
        statuses: tuple[TradeContractStatus, ...] | None = None,
        cursor: str | None = None,
        limit: int = 50,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[list[TradeContractDetails], str | None]:
        created_from, created_to = normalize_date_range(created_from, created_to)
        async with self._uow_factory() as uow:
            items, position = await uow.trade_contracts.list_admin_details_page(
                statuses=statuses,
                cursor=decode_cursor(cursor),
                limit=limit,
                created_from=created_from,
                created_to=created_to,
            )
            return items, encode_next_cursor(position)

    async def list_events(
        self,
        *,
        status: OutboxEventStatus | None = None,
        event_type: str | None = None,
    ) -> list[OutboxEvent]:
        """List outbox events for admin inspection."""
        async with self._uow_factory() as uow:
            return await uow.outbox_events.list_admin(status, event_type)

    async def list_events_page(
        self,
        *,
        status: OutboxEventStatus | None = None,
        event_type: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[list[OutboxEvent], str | None]:
        async with self._uow_factory() as uow:
            items, position = await uow.outbox_events.list_admin_page(
                status=status,
                event_type=event_type,
                cursor=decode_cursor(cursor),
                limit=limit,
            )
            return items, encode_next_cursor(position)

    async def list_kyc_verifications(
        self,
        status: KycVerificationStatus | None = None,
    ) -> list[KycVerification]:
        """List KYC verification attempts for admin inspection."""
        async with self._uow_factory() as uow:
            return await uow.kyc_verifications.list_admin(status)

    async def list_kyc_verifications_page(
        self,
        *,
        status: KycVerificationStatus | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[list[KycVerification], str | None]:
        async with self._uow_factory() as uow:
            items, position = await uow.kyc_verifications.list_admin_page(
                status=status,
                cursor=decode_cursor(cursor),
                limit=limit,
            )
            return items, encode_next_cursor(position)

    async def get_kyc_verification(self, verification_id: str) -> KycVerification:
        """Fetch a KYC verification attempt for admin inspection."""
        async with self._uow_factory() as uow:
            return await uow.kyc_verifications.get(UUID(verification_id))


def get_admin_service() -> AdminService:
    """Build the default admin service."""
    return AdminService()
