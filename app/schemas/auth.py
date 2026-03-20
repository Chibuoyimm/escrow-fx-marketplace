"""Authentication request and response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.domain.entities import User
from app.domain.enums import KycStatus, RiskLevel, UserRole, UserStatus


class RegisterUserRequest(BaseModel):
    """Payload for registering a user."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    country: str = Field(min_length=2, max_length=2)
    phone: str | None = Field(default=None, min_length=7, max_length=32)


class LoginRequest(BaseModel):
    """Payload for logging in a user."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class AccessTokenClaims(BaseModel):
    """Claims stored in an access token."""

    sub: EmailStr
    user_id: UUID
    role: UserRole
    iss: str
    iat: int
    exp: int


class AccessTokenResponse(BaseModel):
    """Token response returned after authentication."""

    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: int


class UserResponseBase(BaseModel):
    """Shared user response fields."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    phone: str | None
    country: str
    role: UserRole
    status: UserStatus
    kyc_status: KycStatus
    risk_level: RiskLevel
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_user(cls, user: User) -> Self:
        """Build a response object from a domain user."""
        return cls.model_validate(user)


class RegisterUserResponse(UserResponseBase):
    """Response for user registration."""


class CurrentUserResponse(UserResponseBase):
    """Response for the authenticated user profile."""
