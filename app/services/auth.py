"""Authentication and RBAC service layer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.auth import AuthenticatedPrincipal
from app.domain.entities import User
from app.domain.enums import KycStatus, RiskLevel, UserRole, UserStatus
from app.domain.exceptions import AuthenticationError, ConflictError, NotFoundError
from app.infrastructure.database.session import AsyncSessionFactory
from app.infrastructure.database.unit_of_work import AbstractUnitOfWork, SqlAlchemyUnitOfWork
from app.infrastructure.security import SecurityService
from app.schemas.auth import AccessTokenResponse

UnitOfWorkFactory = Callable[[], AbstractUnitOfWork]


def utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)


def build_uow() -> AbstractUnitOfWork:
    """Build the default unit of work."""
    return SqlAlchemyUnitOfWork(AsyncSessionFactory)


class AuthService:
    """Application service for authentication and user identity."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory | None = None,
        security: SecurityService | None = None,
    ) -> None:
        self._uow_factory = uow_factory or build_uow
        self._security = security or SecurityService()

    async def register_user(
        self,
        *,
        email: str,
        password: str,
        country: str,
        phone: str | None,
    ) -> User:
        """Register a new customer user."""
        normalized_email = email.lower().strip()
        normalized_country = country.upper()
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
                    phone=phone,
                    country=normalized_country,
                    role=UserRole.CUSTOMER,
                    status=UserStatus.ACTIVE,
                    kyc_status=KycStatus.PENDING,
                    risk_level=RiskLevel.LOW,
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
            )
            await uow.commit()
            return created

    async def login_user(self, *, email: str, password: str) -> AccessTokenResponse:
        """Authenticate a user and issue an access token."""
        normalized_email = email.lower().strip()
        async with self._uow_factory() as uow:
            try:
                user = await uow.users.get_by_email(normalized_email)
            except NotFoundError as exc:
                raise AuthenticationError("Invalid authentication credentials.") from exc

        if user.status is not UserStatus.ACTIVE:
            raise AuthenticationError("Invalid authentication credentials.")
        if not self._security.verify_password(password, user.password_hash):
            raise AuthenticationError("Invalid authentication credentials.")

        token, claims = self._security.issue_access_token(user)
        return AccessTokenResponse(
            access_token=token,
            expires_in_seconds=claims.exp - claims.iat,
        )

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

        return AuthenticatedPrincipal(user_id=user.id, email=user.email, role=user.role)

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
        normalized_country = country.upper()
        async with self._uow_factory() as uow:
            try:
                user = await uow.users.get_by_email(normalized_email)
            except NotFoundError:
                admin = await uow.users.add(
                    User(
                        id=uuid4(),
                        email=normalized_email,
                        password_hash=self._security.hash_password(password),
                        phone=phone,
                        country=normalized_country,
                        role=UserRole.ADMIN,
                        status=UserStatus.ACTIVE,
                        kyc_status=KycStatus.VERIFIED,
                        risk_level=RiskLevel.LOW,
                        created_at=utc_now(),
                        updated_at=utc_now(),
                    )
                )
                await uow.commit()
                return admin

            promoted = replace(
                user,
                password_hash=self._security.hash_password(password),
                role=UserRole.ADMIN,
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
            updated = replace(
                user,
                role=UserRole.ADMIN,
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
