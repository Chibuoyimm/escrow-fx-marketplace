"""Authentication and authorization domain types."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.domain.enums import UserRole


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """The authenticated API principal."""

    user_id: UUID
    email: str
    role: UserRole

