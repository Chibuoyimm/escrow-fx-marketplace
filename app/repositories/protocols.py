"""Repository protocol definitions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from app.domain.entities import (
    Corridor,
    CorridorDetails,
    CorridorRail,
    Currency,
    EmailVerificationToken,
    ExchangeOffer,
    ExchangeOfferDetails,
    ExchangeRequest,
    ExchangeRequestDetails,
    KycVerification,
    OutboxEvent,
    PasswordResetToken,
    TradeContract,
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


class UserRepositoryProtocol(ABC):
    """User repository contract."""

    @abstractmethod
    async def add(self, user: User) -> User:
        """Persist a user."""

    @abstractmethod
    async def get(self, user_id: UUID) -> User:
        """Fetch a user by identifier."""

    @abstractmethod
    async def get_by_email(self, email: str) -> User:
        """Fetch a user by email address."""

    @abstractmethod
    async def update(self, user: User) -> User:
        """Persist changes to an existing user."""

    @abstractmethod
    async def list_all(self, status: UserStatus | None = None) -> list[User]:
        """List users, optionally filtered by status."""


class EmailVerificationTokenRepositoryProtocol(ABC):
    """Email verification token repository contract."""

    @abstractmethod
    async def add(self, token: EmailVerificationToken) -> EmailVerificationToken:
        """Persist an email verification token."""

    @abstractmethod
    async def get_by_token_hash(self, token_hash: str) -> EmailVerificationToken:
        """Fetch an email verification token by hashed token."""

    @abstractmethod
    async def mark_consumed(self, token_id: UUID, now: datetime) -> EmailVerificationToken:
        """Mark a token as consumed."""


class PasswordResetTokenRepositoryProtocol(ABC):
    """Password reset token repository contract."""

    @abstractmethod
    async def add(self, token: PasswordResetToken) -> PasswordResetToken:
        """Persist a password reset token."""

    @abstractmethod
    async def get_by_token_hash(self, token_hash: str) -> PasswordResetToken:
        """Fetch a password reset token by hashed token."""

    @abstractmethod
    async def mark_consumed(self, token_id: UUID, now: datetime) -> PasswordResetToken:
        """Mark a password reset token as consumed."""


class KycVerificationRepositoryProtocol(ABC):
    """KYC verification repository contract."""

    @abstractmethod
    async def add(self, verification: KycVerification) -> KycVerification:
        """Persist a KYC verification attempt."""

    @abstractmethod
    async def get(self, verification_id: UUID) -> KycVerification:
        """Fetch a KYC verification attempt by identifier."""

    @abstractmethod
    async def get_latest_for_user(self, user_id: UUID) -> KycVerification:
        """Fetch the latest KYC verification attempt for a user."""

    @abstractmethod
    async def get_by_provider_reference(
        self,
        provider_reference_id: str,
    ) -> KycVerification:
        """Fetch a KYC verification attempt by provider reference."""

    @abstractmethod
    async def list_by_status(
        self,
        status: KycVerificationStatus,
        *,
        limit: int,
    ) -> list[KycVerification]:
        """List KYC verification attempts by status."""

    @abstractmethod
    async def list_submitted_since(
        self,
        *,
        user_id: UUID,
        since: datetime,
        limit: int,
    ) -> list[KycVerification]:
        """List a user's KYC attempts submitted since a point in time."""

    @abstractmethod
    async def list_admin(
        self,
        status: KycVerificationStatus | None = None,
    ) -> list[KycVerification]:
        """List KYC verification attempts for admin inspection."""

    @abstractmethod
    async def update(self, verification: KycVerification) -> KycVerification:
        """Persist changes to an existing KYC verification attempt."""


class CurrencyRepositoryProtocol(ABC):
    """Currency repository contract."""

    @abstractmethod
    async def add(self, currency: Currency) -> Currency:
        """Persist a currency."""

    @abstractmethod
    async def get_by_code(self, code: str) -> Currency:
        """Fetch a currency by code."""

    @abstractmethod
    async def list_active(self) -> list[Currency]:
        """List active currencies."""


class CorridorRepositoryProtocol(ABC):
    """Corridor repository contract."""

    @abstractmethod
    async def add(self, corridor: Corridor) -> Corridor:
        """Persist a corridor."""

    @abstractmethod
    async def get(self, corridor_id: UUID) -> Corridor:
        """Fetch a corridor by identifier."""

    @abstractmethod
    async def get_by_currency_pair(self, from_currency_id: UUID, to_currency_id: UUID) -> Corridor:
        """Fetch a corridor by ordered currency pair."""

    @abstractmethod
    async def list_active_details(self) -> list[CorridorDetails]:
        """List active corridors as read models."""

    @abstractmethod
    async def get_active_details(self, corridor_id: UUID) -> CorridorDetails:
        """Fetch an active corridor read model by identifier."""

    @abstractmethod
    async def get_active_details_by_currency_pair(
        self,
        from_currency_code: str,
        to_currency_code: str,
    ) -> CorridorDetails:
        """Fetch an active corridor read model by ordered currency pair."""


class CorridorRailRepositoryProtocol(ABC):
    """Corridor rail repository contract."""

    @abstractmethod
    async def add(self, rail: CorridorRail) -> CorridorRail:
        """Persist a corridor rail."""

    @abstractmethod
    async def list_for_corridor(self, corridor_id: UUID) -> list[CorridorRail]:
        """List corridor rails by corridor."""


class ExchangeRequestRepositoryProtocol(ABC):
    """Exchange request repository contract."""

    @abstractmethod
    async def add(self, exchange_request: ExchangeRequest) -> ExchangeRequest:
        """Persist an exchange request."""

    @abstractmethod
    async def update(self, exchange_request: ExchangeRequest) -> ExchangeRequest:
        """Persist changes to an existing exchange request."""

    @abstractmethod
    async def get(self, request_id: UUID) -> ExchangeRequest:
        """Fetch an exchange request by identifier."""

    @abstractmethod
    async def get_details_for_user(self, request_id: UUID, user_id: UUID) -> ExchangeRequestDetails:
        """Fetch a user's exchange request read model by identifier."""

    @abstractmethod
    async def list_details_for_user(self, user_id: UUID) -> list[ExchangeRequestDetails]:
        """List exchange request read models for a user."""

    @abstractmethod
    async def list_board_details(self, viewer_user_id: UUID) -> list[ExchangeRequestDetails]:
        """List board-visible exchange request read models for a viewer."""

    @abstractmethod
    async def get_visible_details(
        self,
        request_id: UUID,
        viewer_user_id: UUID,
    ) -> ExchangeRequestDetails:
        """Fetch an exchange request read model visible to a viewer."""

    @abstractmethod
    async def list_admin_details(
        self,
        status: ExchangeRequestStatus | None = None,
    ) -> list[ExchangeRequestDetails]:
        """List exchange request read models for admin inspection."""

    @abstractmethod
    async def list_due_for_expiry(self, now: datetime) -> list[ExchangeRequest]:
        """List open or pending exchange requests whose deadline has passed."""

    @abstractmethod
    async def expire_due(self, now: datetime) -> int:
        """Expire open or pending exchange requests whose deadline has passed."""

    @abstractmethod
    async def list_pending_without_active_offers(self) -> list[ExchangeRequest]:
        """List pending requests that no longer have active offers."""

    @abstractmethod
    async def reopen_pending_without_active_offers(self, now: datetime) -> int:
        """Reopen pending requests that no longer have active offers."""


class ExchangeOfferRepositoryProtocol(ABC):
    """Exchange offer repository contract."""

    @abstractmethod
    async def add(self, exchange_offer: ExchangeOffer) -> ExchangeOffer:
        """Persist an exchange offer."""

    @abstractmethod
    async def update(self, exchange_offer: ExchangeOffer) -> ExchangeOffer:
        """Persist changes to an existing exchange offer."""

    @abstractmethod
    async def get(self, offer_id: UUID) -> ExchangeOffer:
        """Fetch an exchange offer by identifier."""

    @abstractmethod
    async def list_for_request(self, request_id: UUID) -> list[ExchangeOffer]:
        """List exchange offers for a request."""

    @abstractmethod
    async def list_details_for_request(self, request_id: UUID) -> list[ExchangeOfferDetails]:
        """List exchange offer read models for a request."""

    @abstractmethod
    async def has_active_offer_for_request(self, request_id: UUID, user_id: UUID) -> bool:
        """Check whether a user already has an active offer on a request."""

    @abstractmethod
    async def list_admin_details(
        self,
        status: ExchangeOfferStatus | None = None,
    ) -> list[ExchangeOfferDetails]:
        """List exchange offer read models for admin inspection."""

    @abstractmethod
    async def list_due_for_expiry(self, now: datetime) -> list[ExchangeOffer]:
        """List active exchange offers whose deadline or parent request has expired."""

    @abstractmethod
    async def expire_due(self, now: datetime) -> int:
        """Expire active exchange offers whose deadline or parent request has expired."""


class TradeContractRepositoryProtocol(ABC):
    """Trade contract repository contract."""

    @abstractmethod
    async def add(self, trade_contract: TradeContract) -> TradeContract:
        """Persist a trade contract."""

    @abstractmethod
    async def get(self, trade_id: UUID) -> TradeContract:
        """Fetch a trade contract by identifier."""

    @abstractmethod
    async def get_for_participant(self, trade_id: UUID, user_id: UUID) -> TradeContractDetails:
        """Fetch a trade contract visible to a participant."""

    @abstractmethod
    async def list_for_participant(self, user_id: UUID) -> list[TradeContractDetails]:
        """List trade contracts visible to a participant."""

    @abstractmethod
    async def list_admin_details(
        self,
        status: TradeContractStatus | None = None,
    ) -> list[TradeContractDetails]:
        """List trade contract read models for admin inspection."""

    @abstractmethod
    async def list_due_unfunded_details(self, now: datetime) -> list[TradeContractDetails]:
        """List unfunded locked trades whose funding deadline has passed."""

    @abstractmethod
    async def cancel_due_unfunded(self, now: datetime) -> int:
        """Cancel terms-locked trades whose funding deadline has passed."""


class OutboxEventRepositoryProtocol(ABC):
    """Outbox event repository contract."""

    @abstractmethod
    async def add(self, event: OutboxEvent) -> OutboxEvent:
        """Persist an outbox event."""

    @abstractmethod
    async def list_admin(
        self,
        status: OutboxEventStatus | None = None,
        event_type: str | None = None,
    ) -> list[OutboxEvent]:
        """List outbox events for admin inspection."""

    @abstractmethod
    async def claim_due_for_dispatch(
        self,
        *,
        now: datetime,
        processing_deadline: datetime,
        limit: int,
    ) -> list[OutboxEvent]:
        """Claim due outbox events for dispatch."""

    @abstractmethod
    async def mark_delivered(self, event_id: UUID, now: datetime) -> OutboxEvent:
        """Mark an outbox event as delivered."""

    @abstractmethod
    async def mark_failed(
        self,
        *,
        event_id: UUID,
        status: OutboxEventStatus,
        attempt_count: int,
        last_error: str,
        next_attempt_at: datetime | None,
        now: datetime,
    ) -> OutboxEvent:
        """Mark an outbox event as failed and scheduled for retry."""
