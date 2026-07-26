"""Authentication and RBAC service layer."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from urllib.parse import urlencode
from uuid import UUID, uuid4

from app.domain.account import normalize_international_phone
from app.domain.auth import AuthenticatedPrincipal
from app.domain.entities import EmailVerificationToken, PasswordResetToken, User
from app.domain.enums import KycStatus, RiskLevel, UserRole, UserStatus
from app.domain.exceptions import (
    AuthenticationError,
    ConflictError,
    InvariantViolationError,
    NotFoundError,
    PreconditionFailedError,
)
from app.infrastructure.config import settings
from app.infrastructure.database.unit_of_work import AbstractUnitOfWork
from app.infrastructure.security import SecurityService
from app.schemas.auth import AccessTokenResponse
from app.services._shared import (
    UnitOfWorkFactory,
    build_uow,
    format_display_datetime,
    utc_now,
)
from app.services.outbox import OutboxEventPublisher

AUTH_TOKEN_BYTES = 32
EMAIL_VERIFICATION_FAILED_DETAIL = "Email verification link is invalid or expired."
PASSWORD_RESET_FAILED_DETAIL = "Password reset link is invalid or expired."


def normalize_country_code(country: str) -> str:
    """Normalize and validate an ISO-style country code."""
    normalized_country = country.strip().upper()
    if len(normalized_country) != 2 or not normalized_country.isalpha():
        raise InvariantViolationError("Country must be a two-letter ISO-style country code.")
    return normalized_country


def normalize_email(email: str) -> str:
    """Normalize an email address."""
    return email.lower().strip()


def hash_auth_token(token: str) -> str:
    """Hash a raw auth token for storage and lookup."""
    return sha256(token.encode("utf-8")).hexdigest()


def format_auth_datetime(value: datetime) -> str:
    """Format an auth-related timestamp for customer-facing emails."""
    return format_display_datetime(value)


class AuthService:
    """Application service for authentication and user identity."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory | None = None,
        security: SecurityService | None = None,
        outbox_publisher: OutboxEventPublisher | None = None,
    ) -> None:
        self._uow_factory = uow_factory or build_uow
        self._security = security or SecurityService()
        self._outbox = outbox_publisher or OutboxEventPublisher()

    def _frontend_token_url(self, base_url: str, token: str) -> str:
        """Build a frontend URL carrying a single-use token."""
        base_url = base_url.rstrip("/")
        return f"{base_url}?{urlencode({'token': token})}"

    def _verification_url(self, token: str) -> str:
        return self._frontend_token_url(settings.email_verification_frontend_url, token)

    def _password_reset_url(self, token: str) -> str:
        return self._frontend_token_url(settings.password_reset_frontend_url, token)

    def issue_access_token(self, user: User) -> AccessTokenResponse:
        """Issue an access token response for an authenticated user."""
        token, claims = self._security.issue_access_token(user)
        return AccessTokenResponse(
            access_token=token,
            expires_in_seconds=claims.exp - claims.iat,
        )

    def _new_auth_token(self, expiry_minutes: int) -> tuple[str, datetime, datetime]:
        """Create a fresh raw token with its timestamps."""
        raw_token = token_urlsafe(AUTH_TOKEN_BYTES)
        current_time = utc_now()
        expires_at = current_time + timedelta(minutes=expiry_minutes)
        return raw_token, current_time, expires_at

    async def _queue_email_verification(self, uow: AbstractUnitOfWork, user: User) -> None:
        raw_token, current_time, expires_at = self._new_auth_token(
            settings.email_verification_token_expiry_minutes
        )
        await uow.email_verification_tokens.add(
            EmailVerificationToken(
                id=uuid4(),
                user_id=user.id,
                token_hash=hash_auth_token(raw_token),
                expires_at=expires_at,
                consumed_at=None,
                created_at=current_time,
                updated_at=current_time,
            )
        )
        await self._outbox.user_email_verification_requested(
            uow,
            user_id=user.id,
            email=user.email,
            verify_email_url=self._verification_url(raw_token),
            expires_at=expires_at.isoformat(),
            expires_at_display=format_auth_datetime(expires_at),
        )

    async def _queue_password_reset(self, uow: AbstractUnitOfWork, user: User) -> None:
        raw_token, current_time, expires_at = self._new_auth_token(
            settings.password_reset_token_expiry_minutes
        )
        await uow.password_reset_tokens.add(
            PasswordResetToken(
                id=uuid4(),
                user_id=user.id,
                token_hash=hash_auth_token(raw_token),
                expires_at=expires_at,
                consumed_at=None,
                created_at=current_time,
                updated_at=current_time,
            )
        )
        await self._outbox.user_password_reset_requested(
            uow,
            user_id=user.id,
            email=user.email,
            reset_password_url=self._password_reset_url(raw_token),
            expires_at=expires_at.isoformat(),
            expires_at_display=format_auth_datetime(expires_at),
        )

    async def register_user(
        self,
        *,
        email: str,
        password: str,
        country: str,
        phone: str | None,
    ) -> User:
        """Register a new customer user."""
        normalized_email = normalize_email(email)
        normalized_country = normalize_country_code(country)
        try:
            normalized_phone = normalize_international_phone(phone)
        except ValueError as exc:
            raise InvariantViolationError(str(exc)) from exc
        async with self._uow_factory() as uow:
            try:
                await uow.users.get_by_email(normalized_email)
            except NotFoundError:
                pass
            else:
                raise ConflictError(f"A user with email '{normalized_email}' already exists.")

            created = await uow.users.add(
                User(
                    id=uuid4(),
                    email=normalized_email,
                    password_hash=self._security.hash_password(password),
                    phone=normalized_phone,
                    country=normalized_country,
                    role=UserRole.CUSTOMER,
                    status=UserStatus.ACTIVE,
                    kyc_status=KycStatus.PENDING,
                    risk_level=RiskLevel.LOW,
                    email_verified_at=None,
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
            )
            await self._queue_email_verification(uow, created)
            await uow.commit()
            return created

    async def login_user(self, *, email: str, password: str) -> AccessTokenResponse:
        """Authenticate a user and issue an access token."""
        normalized_email = normalize_email(email)
        async with self._uow_factory() as uow:
            try:
                user = await uow.users.get_by_email(normalized_email)
            except NotFoundError as exc:
                raise AuthenticationError("Invalid authentication credentials.") from exc

        if user.status is not UserStatus.ACTIVE:
            raise AuthenticationError("Invalid authentication credentials.")
        if not self._security.verify_password(password, user.password_hash):
            raise AuthenticationError("Invalid authentication credentials.")
        if user.email_verified_at is None:
            raise PreconditionFailedError("Email verification is required before login.")

        return self.issue_access_token(user)

    async def authenticate_token(self, token: str) -> AuthenticatedPrincipal:
        """Resolve the current principal from a bearer token."""
        claims = self._security.decode_access_token(token)
        async with self._uow_factory() as uow:
            try:
                user = await uow.users.get(claims.user_id)
            except NotFoundError as exc:
                raise AuthenticationError("Invalid authentication credentials.") from exc

        if user.status is not UserStatus.ACTIVE:
            raise AuthenticationError("Invalid authentication credentials.")
        if user.email_verified_at is None:
            raise AuthenticationError("Invalid authentication credentials.")

        return AuthenticatedPrincipal(user_id=user.id, email=user.email, role=user.role)

    async def verify_email(self, token: str) -> User:
        """Verify a user's email address using a single-use token."""
        current_time = utc_now()
        async with self._uow_factory() as uow:
            verification = await uow.email_verification_tokens.consume(
                hash_auth_token(token),
                current_time,
            )
            if verification is None:
                raise PreconditionFailedError(EMAIL_VERIFICATION_FAILED_DETAIL)

            user = await uow.users.get_for_update(verification.user_id)
            saved = user
            if user.email_verified_at is None:
                saved = await uow.users.update(
                    replace(
                        user,
                        email_verified_at=current_time,
                        updated_at=current_time,
                    )
                )

            await uow.commit()
            return saved

    async def resend_email_verification(self, *, email: str) -> None:
        """Queue another verification email without revealing whether an account exists."""
        normalized_email = normalize_email(email)
        async with self._uow_factory() as uow:
            try:
                user = await uow.users.get_by_email(normalized_email)
            except NotFoundError:
                return

            if user.email_verified_at is not None:
                return

            await self._queue_email_verification(uow, user)
            await uow.commit()

    async def forgot_password(self, *, email: str) -> None:
        """Queue a password reset email without revealing whether an account exists."""
        normalized_email = normalize_email(email)
        async with self._uow_factory() as uow:
            try:
                user = await uow.users.get_by_email(normalized_email)
            except NotFoundError:
                return

            if user.status is not UserStatus.ACTIVE:
                return

            await self._queue_password_reset(uow, user)
            await uow.commit()

    async def reset_password(self, *, token: str, password: str) -> None:
        """Reset a user's password using a single-use token."""
        current_time = utc_now()
        async with self._uow_factory() as uow:
            reset_token = await uow.password_reset_tokens.consume(
                hash_auth_token(token),
                current_time,
            )
            if reset_token is None:
                raise PreconditionFailedError(PASSWORD_RESET_FAILED_DETAIL)

            user = await uow.users.get_for_update(reset_token.user_id)
            if user.status is not UserStatus.ACTIVE:
                raise PreconditionFailedError(PASSWORD_RESET_FAILED_DETAIL)

            await uow.users.update(
                replace(
                    user,
                    password_hash=self._security.hash_password(password),
                    updated_at=current_time,
                )
            )
            await self._outbox.user_password_reset_completed(
                uow,
                user_id=user.id,
                email=user.email,
                completed_at=current_time.isoformat(),
                completed_at_display=format_auth_datetime(current_time),
            )
            await uow.commit()

    async def change_password(
        self,
        *,
        user_id: UUID,
        current_password: str,
        new_password: str,
    ) -> None:
        """Change the password for an authenticated user."""
        if current_password == new_password:
            raise InvariantViolationError("New password must be different from current password.")

        current_time = utc_now()
        async with self._uow_factory() as uow:
            user = await uow.users.get(user_id)
            if user.status is not UserStatus.ACTIVE:
                raise AuthenticationError("Invalid authentication credentials.")
            if not self._security.verify_password(current_password, user.password_hash):
                raise AuthenticationError("Invalid authentication credentials.")

            user = await uow.users.get_for_update(user_id)
            if user.status is not UserStatus.ACTIVE:
                raise AuthenticationError("Invalid authentication credentials.")
            if not self._security.verify_password(current_password, user.password_hash):
                raise AuthenticationError("Invalid authentication credentials.")

            await uow.users.update(
                replace(
                    user,
                    password_hash=self._security.hash_password(new_password),
                    updated_at=current_time,
                )
            )
            await self._outbox.user_password_changed(
                uow,
                user_id=user.id,
                email=user.email,
                changed_at=current_time.isoformat(),
                changed_at_display=format_auth_datetime(current_time),
            )
            await uow.commit()

    async def get_user_by_id(self, user_id: UUID) -> User:
        """Fetch a user profile by identifier."""
        async with self._uow_factory() as uow:
            return await uow.users.get(user_id)

    async def create_admin(
        self,
        *,
        email: str,
        password: str,
        country: str,
        phone: str | None = None,
    ) -> User:
        """Create an admin user, or promote one if it already exists."""
        normalized_email = email.lower().strip()
        normalized_country = normalize_country_code(country)
        try:
            normalized_phone = normalize_international_phone(phone)
        except ValueError as exc:
            raise InvariantViolationError(str(exc)) from exc
        async with self._uow_factory() as uow:
            try:
                user = await uow.users.get_by_email(normalized_email)
            except NotFoundError:
                admin = await uow.users.add(
                    User(
                        id=uuid4(),
                        email=normalized_email,
                        password_hash=self._security.hash_password(password),
                        phone=normalized_phone,
                        country=normalized_country,
                        role=UserRole.ADMIN,
                        status=UserStatus.ACTIVE,
                        kyc_status=KycStatus.VERIFIED,
                        risk_level=RiskLevel.LOW,
                        email_verified_at=utc_now(),
                        created_at=utc_now(),
                        updated_at=utc_now(),
                    )
                )
                await uow.commit()
                return admin

            user = await uow.users.get_for_update(user.id)
            promoted = replace(
                user,
                password_hash=self._security.hash_password(password),
                role=UserRole.ADMIN,
                kyc_status=KycStatus.VERIFIED,
                email_verified_at=user.email_verified_at or utc_now(),
                updated_at=utc_now(),
            )
            saved = await uow.users.update(promoted)
            await uow.commit()
            return saved

    async def promote_admin(self, *, email: str, password: str | None = None) -> User:
        """Promote an existing user to admin."""
        normalized_email = email.lower().strip()
        async with self._uow_factory() as uow:
            user = await uow.users.get_by_email(normalized_email)
            user = await uow.users.get_for_update(user.id)
            updated = replace(
                user,
                role=UserRole.ADMIN,
                kyc_status=KycStatus.VERIFIED,
                email_verified_at=user.email_verified_at or utc_now(),
                password_hash=self._security.hash_password(password)
                if password is not None
                else user.password_hash,
                updated_at=utc_now(),
            )
            saved = await uow.users.update(updated)
            await uow.commit()
            return saved


def get_auth_service() -> AuthService:
    """Build the default auth service."""
    return AuthService()
