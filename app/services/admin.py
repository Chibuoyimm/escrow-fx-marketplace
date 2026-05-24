"""Admin read service layer."""

from __future__ import annotations

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
    ExchangeOfferStatus,
    ExchangeRequestStatus,
    KycVerificationStatus,
    OutboxEventStatus,
    TradeContractStatus,
    UserStatus,
)
from app.services._shared import UnitOfWorkFactory, build_uow


class AdminService:
    """Application service for read-only admin marketplace inspection."""

    def __init__(self, uow_factory: UnitOfWorkFactory | None = None) -> None:
        self._uow_factory = uow_factory or build_uow

    async def list_users(self, status: UserStatus | None = None) -> list[User]:
        """List users for admin inspection."""
        async with self._uow_factory() as uow:
            return await uow.users.list_all(status)

    async def list_exchange_requests(
        self,
        status: ExchangeRequestStatus | None = None,
    ) -> list[ExchangeRequestDetails]:
        """List exchange requests for admin inspection."""
        async with self._uow_factory() as uow:
            return await uow.exchange_requests.list_admin_details(status)

    async def list_exchange_offers(
        self,
        status: ExchangeOfferStatus | None = None,
    ) -> list[ExchangeOfferDetails]:
        """List exchange offers for admin inspection."""
        async with self._uow_factory() as uow:
            return await uow.exchange_offers.list_admin_details(status)

    async def list_trades(
        self,
        status: TradeContractStatus | None = None,
    ) -> list[TradeContractDetails]:
        """List trade contracts for admin inspection."""
        async with self._uow_factory() as uow:
            return await uow.trade_contracts.list_admin_details(status)

    async def list_events(
        self,
        *,
        status: OutboxEventStatus | None = None,
        event_type: str | None = None,
    ) -> list[OutboxEvent]:
        """List outbox events for admin inspection."""
        async with self._uow_factory() as uow:
            return await uow.outbox_events.list_admin(status, event_type)

    async def list_kyc_verifications(
        self,
        status: KycVerificationStatus | None = None,
    ) -> list[KycVerification]:
        """List KYC verification attempts for admin inspection."""
        async with self._uow_factory() as uow:
            return await uow.kyc_verifications.list_admin(status)

    async def get_kyc_verification(self, verification_id: str) -> KycVerification:
        """Fetch a KYC verification attempt for admin inspection."""
        async with self._uow_factory() as uow:
            return await uow.kyc_verifications.get(UUID(verification_id))


def get_admin_service() -> AdminService:
    """Build the default admin service."""
    return AdminService()
