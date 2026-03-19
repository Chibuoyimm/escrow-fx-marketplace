"""Unit-of-work abstractions for transactional operations."""

from __future__ import annotations

from abc import ABC, abstractmethod

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.protocols import (
    CorridorRailRepositoryProtocol,
    CorridorRepositoryProtocol,
    CurrencyRepositoryProtocol,
    UserRepositoryProtocol,
)
from app.repositories.sqlalchemy import (
    SqlAlchemyCorridorRailRepository,
    SqlAlchemyCorridorRepository,
    SqlAlchemyCurrencyRepository,
    SqlAlchemyUserRepository,
)


class AbstractUnitOfWork(ABC):
    """Transaction boundary for application services."""

    users: UserRepositoryProtocol
    currencies: CurrencyRepositoryProtocol
    corridors: CorridorRepositoryProtocol
    corridor_rails: CorridorRailRepositoryProtocol

    @abstractmethod
    async def __aenter__(self) -> AbstractUnitOfWork:
        """Enter the transaction boundary."""

    @abstractmethod
    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        """Exit the transaction boundary."""

    @abstractmethod
    async def commit(self) -> None:
        """Persist the current transaction."""

    @abstractmethod
    async def rollback(self) -> None:
        """Roll back the current transaction."""


class SqlAlchemyUnitOfWork(AbstractUnitOfWork):
    """SQLAlchemy-backed unit of work."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self.session: AsyncSession | None = None

    async def __aenter__(self) -> SqlAlchemyUnitOfWork:
        self.session = self._session_factory()
        self.users = SqlAlchemyUserRepository(self.session)
        self.currencies = SqlAlchemyCurrencyRepository(self.session)
        self.corridors = SqlAlchemyCorridorRepository(self.session)
        self.corridor_rails = SqlAlchemyCorridorRailRepository(self.session)
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.session is None:
            return
        if exc is not None:
            await self.rollback()
        await self.session.close()

    async def commit(self) -> None:
        if self.session is None:
            raise RuntimeError("Unit of work has not been entered.")
        await self.session.commit()

    async def rollback(self) -> None:
        if self.session is None:
            raise RuntimeError("Unit of work has not been entered.")
        await self.session.rollback()
