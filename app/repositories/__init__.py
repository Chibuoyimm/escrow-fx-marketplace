"""Repository layer for data-access abstractions."""

from app.repositories.protocols import (
    CorridorRailRepositoryProtocol,
    CorridorRepositoryProtocol,
    CurrencyRepositoryProtocol,
    UserRepositoryProtocol,
)

__all__ = [
    "CorridorRailRepositoryProtocol",
    "CorridorRepositoryProtocol",
    "CurrencyRepositoryProtocol",
    "UserRepositoryProtocol",
]
