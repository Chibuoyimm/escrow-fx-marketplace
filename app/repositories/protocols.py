"""Repository protocol definitions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.entities import Corridor, CorridorRail, Currency, User


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
    async def list_active(self) -> list[Corridor]:
        """List active corridors."""


class CorridorRailRepositoryProtocol(ABC):
    """Corridor rail repository contract."""

    @abstractmethod
    async def add(self, rail: CorridorRail) -> CorridorRail:
        """Persist a corridor rail."""

    @abstractmethod
    async def list_for_corridor(self, corridor_id: UUID) -> list[CorridorRail]:
        """List corridor rails by corridor."""
