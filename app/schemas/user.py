"""Schemas for authenticated user account management."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from app.domain.account import normalize_international_phone
from app.domain.enums import UserStatus


class UpdateProfileRequest(BaseModel):
    """Permitted mutable profile fields."""

    model_config = ConfigDict(extra="forbid")

    phone: str | None = None

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_phone_whitespace(cls, value: object) -> object:
        """Apply the shared provider-neutral phone normalization."""
        if isinstance(value, str):
            try:
                return normalize_international_phone(value)
            except ValueError as exc:
                raise PydanticCustomError("phone_format", str(exc)) from exc
        return value


class DeactivateAccountRequest(BaseModel):
    """Confirmation required to deactivate the current account."""

    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=8, max_length=128)


class AdminUserStatusUpdateRequest(BaseModel):
    """Status transitions available to an administrator."""

    model_config = ConfigDict(extra="forbid")

    status: UserStatus
