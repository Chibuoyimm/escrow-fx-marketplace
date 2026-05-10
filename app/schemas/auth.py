"""Authentication request and response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.domain.entities import User
from app.domain.enums import KycStatus, RiskLevel, UserRole, UserStatus


class RegisterUserRequest(BaseModel):
    """Payload for registering a user."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    country: str = Field(min_length=2, max_length=2, pattern=r"^[A-Z]{2}$")
    phone: str | None = Field(default=None, min_length=7, max_length=32)

    @field_validator("country", mode="before")
    @classmethod
    def normalize_country(cls, value: object) -> object:
        """Normalize country codes before validation."""
        if isinstance(value, str):
            return value.strip().upper()
        return value


class LoginRequest(BaseModel):
    """Payload for logging in a user."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class VerifyEmailRequest(BaseModel):
    """Payload for verifying a user's email address."""

    token: str = Field(min_length=20, max_length=512)


class ResendEmailVerificationRequest(BaseModel):
    """Payload for requesting another verification email."""

    email: EmailStr


class ForgotPasswordRequest(BaseModel):
    """Payload for requesting a password reset email."""

    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Payload for resetting a password with a single-use token."""

    token: str = Field(min_length=20, max_length=512)
    password: str = Field(min_length=8, max_length=128)


class MessageResponse(BaseModel):
    """Simple message response."""

    message: str


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
    email_verified_at: datetime | None
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


class EmailVerificationResponse(AccessTokenResponse):
    """Response returned after successful email verification."""

    user: CurrentUserResponse
