"""Repository protocol definitions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.domain.entities import (
    AccountAuditEvent,
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
from app.infrastructure.pagination import Cursor


class UserRepositoryProtocol(ABC):
    """User repository contract."""

    @abstractmethod
    async def add(self, user: User) -> User:
        """Persist a user."""

    @abstractmethod
    async def get(self, user_id: UUID) -> User:
        """Fetch a user by identifier."""

    @abstractmethod
    async def get_for_update(self, user_id: UUID) -> User:
        """Fetch a user while locking the row for a transactional mutation."""

    @abstractmethod
    async def get_by_email(self, email: str) -> User:
        """Fetch a user by email address."""

    @abstractmethod
    async def update(self, user: User) -> User:
        """Persist changes to an existing user."""

    @abstractmethod
    async def list_all(self, status: UserStatus | None = None) -> list[User]:
        """List users, optionally filtered by status."""

    @abstractmethod
    async def list_page(
        self,
        *,
        status: UserStatus | None = None,
        cursor: Cursor | None = None,
        limit: int = 50,
    ) -> tuple[list[User], Cursor | None]:
        """List users with cursor pagination."""


class AccountAuditEventRepositoryProtocol(ABC):
    """Append-only account audit history contract."""

    @abstractmethod
    async def add(self, event: AccountAuditEvent) -> AccountAuditEvent:
        """Persist an account audit event."""

    @abstractmethod
    async def list_for_subject(self, subject_user_id: UUID) -> list[AccountAuditEvent]:
        """List account audit events for a subject user."""


class EmailVerificationTokenRepositoryProtocol(ABC):
    """Email verification token repository contract."""

    @abstractmethod
    async def add(self, token: EmailVerificationToken) -> EmailVerificationToken:
        """Persist an email verification token."""

    @abstractmethod
    async def get_by_token_hash(self, token_hash: str) -> EmailVerificationToken:
        """Fetch an email verification token by hashed token."""

    @abstractmethod
    async def consume(
        self,
        token_hash: str,
        now: datetime,
    ) -> EmailVerificationToken | None:
        """Atomically consume an unexpired, unused email verification token."""


class PasswordResetTokenRepositoryProtocol(ABC):
    """Password reset token repository contract."""

    @abstractmethod
    async def add(self, token: PasswordResetToken) -> PasswordResetToken:
        """Persist a password reset token."""

    @abstractmethod
    async def get_by_token_hash(self, token_hash: str) -> PasswordResetToken:
        """Fetch a password reset token by hashed token."""

    @abstractmethod
    async def consume(
        self,
        token_hash: str,
        now: datetime,
    ) -> PasswordResetToken | None:
        """Atomically consume an unexpired, unused password reset token."""


class KycVerificationRepositoryProtocol(ABC):
    """KYC verification repository contract."""

    @abstractmethod
    async def add(self, verification: KycVerification) -> KycVerification:
        """Persist a KYC verification attempt."""

    @abstractmethod
    async def get(self, verification_id: UUID) -> KycVerification:
        """Fetch a KYC verification attempt by identifier."""

    @abstractmethod
    async def get_for_update(self, verification_id: UUID) -> KycVerification:
        """Fetch a KYC verification attempt while locking its row."""

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
    async def list_admin_page(
        self,
        *,
        status: KycVerificationStatus | None = None,
        cursor: Cursor | None = None,
        limit: int = 50,
    ) -> tuple[list[KycVerification], Cursor | None]:
        """List KYC verification attempts with cursor pagination."""

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
    async def get(self, currency_id: UUID) -> Currency:
        """Fetch a currency by identifier."""

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
    async def get_for_update(self, request_id: UUID) -> ExchangeRequest:
        """Fetch an exchange request with an explicit row lock."""

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
    async def list_admin_details_page(
        self,
        *,
        statuses: tuple[ExchangeRequestStatus, ...] | None = None,
        cursor: Cursor | None = None,
        limit: int = 50,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[list[ExchangeRequestDetails], Cursor | None]:
        """List exchange requests for admins with cursor pagination."""

    @abstractmethod
    @abstractmethod
    async def expire_due(self, now: datetime) -> list[ExchangeRequest]:
        """Atomically expire due requests and return only changed rows."""

    @abstractmethod
    async def reopen_pending_without_active_offers(self, now: datetime) -> list[ExchangeRequest]:
        """Atomically reopen pending requests and return only changed rows."""

    @abstractmethod
    async def has_any_offers(self, request_id: UUID) -> bool:
        """Return whether any historical offer exists for a request."""

    @abstractmethod
    async def has_relisted_successor(self, request_id: UUID) -> bool:
        """Return whether a request already has a direct relisted successor."""

    @abstractmethod
    async def has_actionable_for_creator(self, user_id: UUID, now: datetime) -> bool:
        """Return whether a user owns a non-expired open or pending request."""

    @abstractmethod
    async def list_board_details_page(
        self,
        viewer_user_id: UUID,
        *,
        cursor: Cursor | None = None,
        limit: int = 50,
        statuses: tuple[ExchangeRequestStatus, ...] | None = None,
        from_currency_code: str | None = None,
        to_currency_code: str | None = None,
        min_amount: Decimal | None = None,
        max_amount: Decimal | None = None,
        min_preferred_rate: Decimal | None = None,
        max_preferred_rate: Decimal | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[list[ExchangeRequestDetails], Cursor | None]:
        """List board requests with cursor pagination and filters."""

    @abstractmethod
    async def list_details_for_user_page(
        self,
        user_id: UUID,
        *,
        cursor: Cursor | None = None,
        limit: int = 50,
        statuses: tuple[ExchangeRequestStatus, ...] | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[list[ExchangeRequestDetails], Cursor | None]:
        """List a user's requests with cursor pagination and filters."""


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
    async def get_for_update(self, offer_id: UUID) -> ExchangeOffer:
        """Fetch an exchange offer with an explicit row lock."""

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
    async def has_active_for_user(self, user_id: UUID, now: datetime) -> bool:
        """Return whether a user owns any non-expired active offer."""

    @abstractmethod
    async def get_visible_details(
        self, offer_id: UUID, viewer_user_id: UUID
    ) -> ExchangeOfferDetails:
        """Fetch an offer only for its owner or parent request creator."""

    @abstractmethod
    async def list_details_for_request_page(
        self,
        request_id: UUID,
        *,
        cursor: Cursor | None = None,
        limit: int = 50,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[list[ExchangeOfferDetails], Cursor | None]:
        """List request offers with cursor pagination."""

    @abstractmethod
    async def list_details_for_user_page(
        self,
        user_id: UUID,
        *,
        cursor: Cursor | None = None,
        limit: int = 50,
        statuses: tuple[ExchangeOfferStatus, ...] | None = None,
        min_offered_rate: Decimal | None = None,
        max_offered_rate: Decimal | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[list[ExchangeOfferDetails], Cursor | None]:
        """List a user's offers with cursor pagination and filters."""

    @abstractmethod
    async def list_admin_details_page(
        self,
        *,
        statuses: tuple[ExchangeOfferStatus, ...] | None = None,
        cursor: Cursor | None = None,
        limit: int = 50,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[list[ExchangeOfferDetails], Cursor | None]:
        """List exchange offers for admins with cursor pagination."""

    @abstractmethod
    async def expire_due(self, now: datetime) -> list[ExchangeOffer]:
        """Atomically expire due offers and return only changed rows."""


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
    async def list_for_participant_page(
        self,
        user_id: UUID,
        *,
        cursor: Cursor | None = None,
        limit: int = 50,
        statuses: tuple[TradeContractStatus, ...] | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[list[TradeContractDetails], Cursor | None]:
        """List participant trades with cursor pagination and filters."""

    @abstractmethod
    async def list_admin_details_page(
        self,
        *,
        statuses: tuple[TradeContractStatus, ...] | None = None,
        cursor: Cursor | None = None,
        limit: int = 50,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[list[TradeContractDetails], Cursor | None]:
        """List trades for admins with cursor pagination."""

    @abstractmethod
    async def cancel_due_unfunded(self, now: datetime) -> list[TradeContractDetails]:
        """Atomically cancel due trades and return only changed rows."""

    @abstractmethod
    async def has_non_terminal_for_participant(self, user_id: UUID) -> bool:
        """Return whether a user participates in any non-terminal trade."""


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
    async def list_admin_page(
        self,
        *,
        status: OutboxEventStatus | None = None,
        event_type: str | None = None,
        cursor: Cursor | None = None,
        limit: int = 50,
    ) -> tuple[list[OutboxEvent], Cursor | None]:
        """List outbox events with cursor pagination."""

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
    async def mark_delivered(
        self,
        *,
        event_id: UUID,
        expected_processing_deadline: datetime,
        now: datetime,
    ) -> OutboxEvent | None:
        """Finalize delivery only if this worker still owns the processing lease."""

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
        expected_processing_deadline: datetime,
    ) -> OutboxEvent | None:
        """Finalize failure only if this worker still owns the processing lease."""
