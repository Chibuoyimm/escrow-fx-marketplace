"""Helpers for recording outbox events."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from app.domain.entities import OutboxEvent
from app.domain.enums import OutboxEventStatus
from app.infrastructure.database.unit_of_work import AbstractUnitOfWork
from app.services._shared import utc_now

SYSTEM_AGGREGATE_ID = UUID("00000000-0000-0000-0000-000000000000")


def build_outbox_event(
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: UUID,
    recipient_user_id: UUID | None,
    payload: dict[str, Any],
) -> OutboxEvent:
    """Build a pending outbox event."""
    current_time = utc_now()
    return OutboxEvent(
        id=uuid4(),
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        recipient_user_id=recipient_user_id,
        payload=payload,
        status=OutboxEventStatus.PENDING,
        attempt_count=0,
        next_attempt_at=current_time,
        last_error=None,
        created_at=current_time,
        updated_at=current_time,
    )


class OutboxEventPublisher:
    """Centralized application-layer publisher for outbox events."""

    async def _add(
        self,
        uow: AbstractUnitOfWork,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: UUID,
        recipient_user_id: UUID | None,
        payload: dict[str, Any],
    ) -> OutboxEvent:
        return await uow.outbox_events.add(
            build_outbox_event(
                event_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                recipient_user_id=recipient_user_id,
                payload=payload,
            )
        )

    async def user_email_verification_requested(
        self,
        uow: AbstractUnitOfWork,
        *,
        user_id: UUID,
        email: str,
        verify_email_url: str,
        expires_at: str,
        expires_at_display: str,
    ) -> OutboxEvent:
        return await self._add(
            uow,
            event_type="user.email_verification_requested",
            aggregate_type="user",
            aggregate_id=user_id,
            recipient_user_id=user_id,
            payload={
                "user_id": str(user_id),
                "email": email,
                "verify_email_url": verify_email_url,
                "expires_at": expires_at,
                "expires_at_display": expires_at_display,
            },
        )

    async def user_password_reset_requested(
        self,
        uow: AbstractUnitOfWork,
        *,
        user_id: UUID,
        email: str,
        reset_password_url: str,
        expires_at: str,
        expires_at_display: str,
    ) -> OutboxEvent:
        return await self._add(
            uow,
            event_type="user.password_reset_requested",
            aggregate_type="user",
            aggregate_id=user_id,
            recipient_user_id=user_id,
            payload={
                "user_id": str(user_id),
                "email": email,
                "reset_password_url": reset_password_url,
                "expires_at": expires_at,
                "expires_at_display": expires_at_display,
            },
        )

    async def user_password_reset_completed(
        self,
        uow: AbstractUnitOfWork,
        *,
        user_id: UUID,
        email: str,
        completed_at: str,
        completed_at_display: str,
    ) -> OutboxEvent:
        return await self._add(
            uow,
            event_type="user.password_reset_completed",
            aggregate_type="user",
            aggregate_id=user_id,
            recipient_user_id=user_id,
            payload={
                "user_id": str(user_id),
                "email": email,
                "completed_at": completed_at,
                "completed_at_display": completed_at_display,
            },
        )

    async def user_password_changed(
        self,
        uow: AbstractUnitOfWork,
        *,
        user_id: UUID,
        email: str,
        changed_at: str,
        changed_at_display: str,
    ) -> OutboxEvent:
        return await self._add(
            uow,
            event_type="user.password_changed",
            aggregate_type="user",
            aggregate_id=user_id,
            recipient_user_id=user_id,
            payload={
                "user_id": str(user_id),
                "email": email,
                "changed_at": changed_at,
                "changed_at_display": changed_at_display,
            },
        )

    async def user_kyc_submitted(
        self,
        uow: AbstractUnitOfWork,
        *,
        user_id: UUID,
        verification_id: UUID,
        id_type: str,
        provider: str,
    ) -> OutboxEvent:
        return await self._add(
            uow,
            event_type="user.kyc_submitted",
            aggregate_type="kyc_verification",
            aggregate_id=verification_id,
            recipient_user_id=user_id,
            payload={
                "user_id": str(user_id),
                "kyc_verification_id": str(verification_id),
                "id_type": id_type,
                "provider": provider,
            },
        )

    async def user_kyc_verified(
        self,
        uow: AbstractUnitOfWork,
        *,
        user_id: UUID,
        verification_id: UUID,
        id_type: str,
        provider: str,
    ) -> OutboxEvent:
        return await self._add(
            uow,
            event_type="user.kyc_verified",
            aggregate_type="kyc_verification",
            aggregate_id=verification_id,
            recipient_user_id=user_id,
            payload={
                "user_id": str(user_id),
                "kyc_verification_id": str(verification_id),
                "id_type": id_type,
                "provider": provider,
            },
        )

    async def user_kyc_requires_review(
        self,
        uow: AbstractUnitOfWork,
        *,
        user_id: UUID,
        verification_id: UUID,
        id_type: str,
        provider: str,
        reason: str,
    ) -> OutboxEvent:
        return await self._add(
            uow,
            event_type="user.kyc_requires_review",
            aggregate_type="kyc_verification",
            aggregate_id=verification_id,
            recipient_user_id=user_id,
            payload={
                "user_id": str(user_id),
                "kyc_verification_id": str(verification_id),
                "id_type": id_type,
                "provider": provider,
                "reason": reason,
            },
        )

    async def user_kyc_rejected(
        self,
        uow: AbstractUnitOfWork,
        *,
        user_id: UUID,
        verification_id: UUID,
        id_type: str,
        provider: str,
        reason: str,
    ) -> OutboxEvent:
        return await self._add(
            uow,
            event_type="user.kyc_rejected",
            aggregate_type="kyc_verification",
            aggregate_id=verification_id,
            recipient_user_id=user_id,
            payload={
                "user_id": str(user_id),
                "kyc_verification_id": str(verification_id),
                "id_type": id_type,
                "provider": provider,
                "reason": reason,
            },
        )

    async def exchange_request_created(
        self,
        uow: AbstractUnitOfWork,
        *,
        request_id: UUID,
        creator_user_id: UUID,
        from_currency_code: str,
        to_currency_code: str,
        from_amount: str,
    ) -> OutboxEvent:
        return await self._add(
            uow,
            event_type="exchange_request.created",
            aggregate_type="exchange_request",
            aggregate_id=request_id,
            recipient_user_id=creator_user_id,
            payload={
                "request_id": str(request_id),
                "creator_user_id": str(creator_user_id),
                "from_currency_code": from_currency_code,
                "to_currency_code": to_currency_code,
                "from_amount": from_amount,
            },
        )

    async def exchange_request_cancelled(
        self,
        uow: AbstractUnitOfWork,
        *,
        request_id: UUID,
        requester_user_id: UUID,
    ) -> OutboxEvent:
        return await self._add(
            uow,
            event_type="exchange_request.cancelled",
            aggregate_type="exchange_request",
            aggregate_id=request_id,
            recipient_user_id=requester_user_id,
            payload={
                "request_id": str(request_id),
                "requester_user_id": str(requester_user_id),
            },
        )

    async def exchange_request_expired(
        self,
        uow: AbstractUnitOfWork,
        *,
        request_id: UUID,
        creator_user_id: UUID,
    ) -> OutboxEvent:
        return await self._add(
            uow,
            event_type="exchange_request.expired",
            aggregate_type="exchange_request",
            aggregate_id=request_id,
            recipient_user_id=creator_user_id,
            payload={"request_id": str(request_id)},
        )

    async def exchange_request_reopened(
        self,
        uow: AbstractUnitOfWork,
        *,
        request_id: UUID,
        creator_user_id: UUID,
    ) -> OutboxEvent:
        return await self._add(
            uow,
            event_type="exchange_request.reopened",
            aggregate_type="exchange_request",
            aggregate_id=request_id,
            recipient_user_id=creator_user_id,
            payload={"request_id": str(request_id)},
        )

    async def exchange_offer_created(
        self,
        uow: AbstractUnitOfWork,
        *,
        offer_id: UUID,
        request_id: UUID,
        offer_user_id: UUID,
        recipient_user_id: UUID,
        offered_rate: str,
    ) -> OutboxEvent:
        return await self._add(
            uow,
            event_type="exchange_offer.created",
            aggregate_type="exchange_offer",
            aggregate_id=offer_id,
            recipient_user_id=recipient_user_id,
            payload={
                "offer_id": str(offer_id),
                "request_id": str(request_id),
                "offer_user_id": str(offer_user_id),
                "offered_rate": offered_rate,
            },
        )

    async def exchange_offer_withdrawn(
        self,
        uow: AbstractUnitOfWork,
        *,
        offer_id: UUID,
        request_id: UUID,
        offer_user_id: UUID,
        recipient_user_id: UUID,
    ) -> OutboxEvent:
        return await self._add(
            uow,
            event_type="exchange_offer.withdrawn",
            aggregate_type="exchange_offer",
            aggregate_id=offer_id,
            recipient_user_id=recipient_user_id,
            payload={
                "offer_id": str(offer_id),
                "request_id": str(request_id),
                "offer_user_id": str(offer_user_id),
            },
        )

    async def exchange_offer_rejected(
        self,
        uow: AbstractUnitOfWork,
        *,
        offer_id: UUID,
        request_id: UUID,
        recipient_user_id: UUID,
        requester_user_id: UUID | None = None,
        reason: str | None = None,
    ) -> OutboxEvent:
        payload: dict[str, Any] = {
            "offer_id": str(offer_id),
            "request_id": str(request_id),
        }
        if requester_user_id is not None:
            payload["requester_user_id"] = str(requester_user_id)
        if reason is not None:
            payload["reason"] = reason
        return await self._add(
            uow,
            event_type="exchange_offer.rejected",
            aggregate_type="exchange_offer",
            aggregate_id=offer_id,
            recipient_user_id=recipient_user_id,
            payload=payload,
        )

    async def exchange_offer_accepted(
        self,
        uow: AbstractUnitOfWork,
        *,
        offer_id: UUID,
        request_id: UUID,
        offer_user_id: UUID,
        trade_contract_id: UUID,
    ) -> OutboxEvent:
        return await self._add(
            uow,
            event_type="exchange_offer.accepted",
            aggregate_type="exchange_offer",
            aggregate_id=offer_id,
            recipient_user_id=offer_user_id,
            payload={
                "offer_id": str(offer_id),
                "request_id": str(request_id),
                "trade_contract_id": str(trade_contract_id),
            },
        )

    async def exchange_offer_expired(
        self,
        uow: AbstractUnitOfWork,
        *,
        offer_id: UUID,
        request_id: UUID,
        offer_user_id: UUID,
    ) -> OutboxEvent:
        return await self._add(
            uow,
            event_type="exchange_offer.expired",
            aggregate_type="exchange_offer",
            aggregate_id=offer_id,
            recipient_user_id=offer_user_id,
            payload={
                "offer_id": str(offer_id),
                "request_id": str(request_id),
            },
        )

    async def trade_contract_locked(
        self,
        uow: AbstractUnitOfWork,
        *,
        trade_contract_id: UUID,
        request_id: UUID,
        accepted_offer_id: UUID,
        recipient_user_id: UUID,
    ) -> OutboxEvent:
        return await self._add(
            uow,
            event_type="trade_contract.locked",
            aggregate_type="trade_contract",
            aggregate_id=trade_contract_id,
            recipient_user_id=recipient_user_id,
            payload={
                "trade_contract_id": str(trade_contract_id),
                "request_id": str(request_id),
                "accepted_offer_id": str(accepted_offer_id),
            },
        )

    async def trade_contract_cancelled(
        self,
        uow: AbstractUnitOfWork,
        *,
        trade_contract_id: UUID,
        request_id: UUID,
        recipient_user_id: UUID,
        reason: str,
    ) -> OutboxEvent:
        return await self._add(
            uow,
            event_type="trade_contract.cancelled",
            aggregate_type="trade_contract",
            aggregate_id=trade_contract_id,
            recipient_user_id=recipient_user_id,
            payload={
                "trade_contract_id": str(trade_contract_id),
                "request_id": str(request_id),
                "reason": reason,
            },
        )

    async def marketplace_expiry_completed(
        self,
        uow: AbstractUnitOfWork,
        *,
        expired_requests: int,
        expired_offers: int,
        reopened_requests: int,
        cancelled_trades: int,
    ) -> OutboxEvent:
        return await self._add(
            uow,
            event_type="marketplace_expiry.completed",
            aggregate_type="marketplace_expiry",
            aggregate_id=SYSTEM_AGGREGATE_ID,
            recipient_user_id=None,
            payload={
                "expired_requests": expired_requests,
                "expired_offers": expired_offers,
                "reopened_requests": reopened_requests,
                "cancelled_trades": cancelled_trades,
            },
        )
