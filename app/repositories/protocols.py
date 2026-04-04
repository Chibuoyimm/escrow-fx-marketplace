"""Repository protocol definitions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.entities import (
    Corridor,
    CorridorDetails,
    CorridorRail,
    Currency,
    ExchangeOffer,
    ExchangeOfferDetails,
    ExchangeRequest,
    ExchangeRequestDetails,
    TradeContract,
    TradeContractDetails,
    User,
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


class TradeContractRepositoryProtocol(ABC):
    """Trade contract repository contract."""

    @abstractmethod
    async def add(self, trade_contract: TradeContract) -> TradeContract:
        """Persist a trade contract."""

    @abstractmethod
    async def get_for_participant(self, trade_id: UUID, user_id: UUID) -> TradeContractDetails:
        """Fetch a trade contract visible to a participant."""
