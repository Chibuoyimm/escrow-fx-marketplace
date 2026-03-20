"""Security helpers for password hashing and JWT handling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from jwt import InvalidTokenError
from pwdlib import PasswordHash

from app.domain.entities import User
from app.domain.exceptions import AuthenticationError
from app.infrastructure.config import settings
from app.schemas.auth import AccessTokenClaims

password_hash = PasswordHash.recommended()


class SecurityService:
    """Password hashing and token utilities."""

    def hash_password(self, password: str) -> str:
        """Hash a plain-text password."""
        return password_hash.hash(password)

    def verify_password(self, password: str, password_digest: str) -> bool:
        """Verify a plain-text password against a stored hash."""
        return password_hash.verify(password, password_digest)

    def issue_access_token(self, user: User) -> tuple[str, AccessTokenClaims]:
        """Create a signed access token for a user."""
        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=settings.access_token_expiry_minutes)
        claims = AccessTokenClaims(
            sub=user.email,
            user_id=user.id,
            role=user.role,
            iss=settings.jwt_issuer,
            iat=int(now.timestamp()),
            exp=int(expires_at.timestamp()),
        )
        token = jwt.encode(
            claims.model_dump(mode="json"),
            settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )
        return token, claims

    def decode_access_token(self, token: str) -> AccessTokenClaims:
        """Decode and validate an access token."""
        try:
            payload = jwt.decode(
                token,
                settings.jwt_secret_key,
                algorithms=[settings.jwt_algorithm],
                issuer=settings.jwt_issuer,
            )
        except InvalidTokenError as exc:
            raise AuthenticationError("Invalid authentication credentials.") from exc

        try:
            return AccessTokenClaims.model_validate(payload)
        except Exception as exc:
            raise AuthenticationError("Invalid authentication credentials.") from exc

