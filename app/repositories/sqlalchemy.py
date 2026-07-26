"""SQLAlchemy-backed repository implementations."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Select, and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload, with_loader_criteria

from app.domain.entities import (
    AccountAuditEvent,
    Corridor,
    CorridorDetails,
    CorridorRail,
    Currency,
    EmailVerificationToken,
    ExchangeOffer,
    ExchangeOfferDetails,
    ExchangeRequest,
    ExchangeRequestDetails,
    KycVerification,
    OutboxEvent,
    PasswordResetToken,
    TradeContract,
    TradeContractDetails,
    User,
)
from app.domain.enums import (
    CorridorStatus,
    CurrencyStatus,
    ExchangeOfferStatus,
    ExchangeRequestStatus,
    KycVerificationStatus,
    OutboxEventStatus,
    RailStatus,
    TradeContractStatus,
    UserStatus,
)
from app.domain.exceptions import ConflictError, NotFoundError
from app.infrastructure.exceptions import InfrastructureError
from app.infrastructure.pagination import Cursor
from app.models.account_audit_event import AccountAuditEventModel
from app.models.corridor import CorridorModel, CorridorRailModel
from app.models.currency import CurrencyModel
from app.models.email_verification_token import EmailVerificationTokenModel
from app.models.exchange_offer import ExchangeOfferModel
from app.models.exchange_request import ExchangeRequestModel
from app.models.kyc_verification import KycVerificationModel
from app.models.outbox_event import OutboxEventModel
from app.models.password_reset_token import PasswordResetTokenModel
from app.models.trade_contract import TradeContractModel
from app.models.user import UserModel
from app.repositories.protocols import (
    AccountAuditEventRepositoryProtocol,
    CorridorRailRepositoryProtocol,
    CorridorRepositoryProtocol,
    CurrencyRepositoryProtocol,
    EmailVerificationTokenRepositoryProtocol,
    ExchangeOfferRepositoryProtocol,
    ExchangeRequestRepositoryProtocol,
    KycVerificationRepositoryProtocol,
    OutboxEventRepositoryProtocol,
    PasswordResetTokenRepositoryProtocol,
    TradeContractRepositoryProtocol,
    UserRepositoryProtocol,
)


class SqlAlchemyRepository:
    """Base repository with SQLAlchemy helpers."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _rowcount(result: Any) -> int:
        return int(getattr(result, "rowcount", 0) or 0)

    async def _flush_or_raise_conflict(self, conflict_detail: str) -> None:
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError(conflict_detail) from exc
        except Exception as exc:
            raise InfrastructureError(title="Database Error", detail=str(exc)) from exc

    @staticmethod
    def _before_cursor(created_at: Any, item_id: Any, cursor: Cursor | None) -> Any:
        """Build the stable descending keyset predicate for a cursor."""
        if cursor is None:
            return None
        return or_(
            created_at < cursor.created_at,
            and_(created_at == cursor.created_at, item_id < cursor.item_id),
        )

    @staticmethod
    def _page_cursor(models: Any, limit: int) -> tuple[list[Any], Cursor | None]:
        """Trim one look-ahead row and return the next position."""
        has_next = len(models) > limit
        page = list(models[:limit])
        if not has_next or not page:
            return page, None
        last = page[-1]
        return page, Cursor(last.created_at, last.id)


class SqlAlchemyUserRepository(SqlAlchemyRepository, UserRepositoryProtocol):
    """User repository implementation."""

    async def add(self, user: User) -> User:
        model = UserModel(
            id=user.id,
            email=user.email,
            password_hash=user.password_hash,
            phone=user.phone,
            country=user.country,
            role=user.role,
            status=user.status,
            kyc_status=user.kyc_status,
            risk_level=user.risk_level,
            email_verified_at=user.email_verified_at,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )
        self.session.add(model)
        await self._flush_or_raise_conflict("A user with that email already exists.")
        return model.to_domain()

    async def get(self, user_id: UUID) -> User:
        model = await self.session.get(UserModel, user_id)
        if model is None:
            raise NotFoundError(f"User '{user_id}' was not found.")
        return model.to_domain()

    async def get_for_update(self, user_id: UUID) -> User:
        statement: Select[tuple[UserModel]] = (
            select(UserModel)
            .where(UserModel.id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result = await self.session.execute(statement)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError(f"User '{user_id}' was not found.")
        return model.to_domain()

    async def get_by_email(self, email: str) -> User:
        statement: Select[tuple[UserModel]] = select(UserModel).where(UserModel.email == email)
        result = await self.session.execute(statement)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError(f"User with email '{email}' was not found.")
        return model.to_domain()

    async def update(self, user: User) -> User:
        model = await self.session.get(UserModel, user.id)
        if model is None:
            raise NotFoundError(f"User '{user.id}' was not found.")

        model.email = user.email
        model.password_hash = user.password_hash
        model.phone = user.phone
        model.country = user.country
        model.role = user.role
        model.status = user.status
        model.kyc_status = user.kyc_status
        model.risk_level = user.risk_level
        model.email_verified_at = user.email_verified_at
        model.updated_at = user.updated_at

        await self._flush_or_raise_conflict("A user with that email already exists.")
        return model.to_domain()

    async def list_all(self, status: UserStatus | None = None) -> list[User]:
        statement: Select[tuple[UserModel]] = select(UserModel).order_by(
            UserModel.created_at.desc()
        )
        if status is not None:
            statement = statement.where(UserModel.status == status)
        result = await self.session.execute(statement)
        return [model.to_domain() for model in result.scalars().all()]

    async def list_page(
        self,
        *,
        status: UserStatus | None = None,
        cursor: Cursor | None = None,
        limit: int = 50,
    ) -> tuple[list[User], Cursor | None]:
        statement: Select[tuple[UserModel]] = (
            select(UserModel)
            .order_by(UserModel.created_at.desc(), UserModel.id.desc())
            .limit(limit + 1)
        )
        if status is not None:
            statement = statement.where(UserModel.status == status)
        cursor_clause = self._before_cursor(UserModel.created_at, UserModel.id, cursor)
        if cursor_clause is not None:
            statement = statement.where(cursor_clause)
        result = await self.session.execute(statement)
        models, next_cursor = self._page_cursor(result.scalars().all(), limit)
        return [model.to_domain() for model in models], next_cursor


class SqlAlchemyEmailVerificationTokenRepository(
    SqlAlchemyRepository,
    EmailVerificationTokenRepositoryProtocol,
):
    """Email verification token repository implementation."""

    async def add(self, token: EmailVerificationToken) -> EmailVerificationToken:
        model = EmailVerificationTokenModel(
            id=token.id,
            user_id=token.user_id,
            token_hash=token.token_hash,
            expires_at=token.expires_at,
            consumed_at=token.consumed_at,
            created_at=token.created_at,
            updated_at=token.updated_at,
        )
        self.session.add(model)
        await self._flush_or_raise_conflict("That email verification token could not be saved.")
        return model.to_domain()

    async def get_by_token_hash(self, token_hash: str) -> EmailVerificationToken:
        statement: Select[tuple[EmailVerificationTokenModel]] = select(
            EmailVerificationTokenModel
        ).where(EmailVerificationTokenModel.token_hash == token_hash)
        result = await self.session.execute(statement)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError("Email verification token was not found.")
        return model.to_domain()

    async def consume(
        self,
        token_hash: str,
        now: datetime,
    ) -> EmailVerificationToken | None:
        """Atomically consume an unexpired, unused email verification token."""
        result = await self.session.execute(
            update(EmailVerificationTokenModel)
            .where(
                EmailVerificationTokenModel.token_hash == token_hash,
                EmailVerificationTokenModel.consumed_at.is_(None),
                EmailVerificationTokenModel.expires_at > now,
            )
            .values(consumed_at=now, updated_at=now)
            .returning(EmailVerificationTokenModel)
        )
        model = result.scalar_one_or_none()
        return model.to_domain() if model is not None else None


class SqlAlchemyPasswordResetTokenRepository(
    SqlAlchemyRepository,
    PasswordResetTokenRepositoryProtocol,
):
    """Password reset token repository implementation."""

    async def add(self, token: PasswordResetToken) -> PasswordResetToken:
        model = PasswordResetTokenModel(
            id=token.id,
            user_id=token.user_id,
            token_hash=token.token_hash,
            expires_at=token.expires_at,
            consumed_at=token.consumed_at,
            created_at=token.created_at,
            updated_at=token.updated_at,
        )
        self.session.add(model)
        await self._flush_or_raise_conflict("That password reset token could not be saved.")
        return model.to_domain()

    async def get_by_token_hash(self, token_hash: str) -> PasswordResetToken:
        statement: Select[tuple[PasswordResetTokenModel]] = select(PasswordResetTokenModel).where(
            PasswordResetTokenModel.token_hash == token_hash
        )
        result = await self.session.execute(statement)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError("Password reset token was not found.")
        return model.to_domain()

    async def consume(
        self,
        token_hash: str,
        now: datetime,
    ) -> PasswordResetToken | None:
        """Atomically consume an unexpired, unused password reset token."""
        result = await self.session.execute(
            update(PasswordResetTokenModel)
            .where(
                PasswordResetTokenModel.token_hash == token_hash,
                PasswordResetTokenModel.consumed_at.is_(None),
                PasswordResetTokenModel.expires_at > now,
            )
            .values(consumed_at=now, updated_at=now)
            .returning(PasswordResetTokenModel)
        )
        model = result.scalar_one_or_none()
        return model.to_domain() if model is not None else None


class SqlAlchemyKycVerificationRepository(
    SqlAlchemyRepository,
    KycVerificationRepositoryProtocol,
):
    """KYC verification repository implementation."""

    async def add(self, verification: KycVerification) -> KycVerification:
        model = KycVerificationModel(
            id=verification.id,
            user_id=verification.user_id,
            provider=verification.provider,
            provider_reference_id=verification.provider_reference_id,
            id_type=verification.id_type,
            masked_identifier=verification.masked_identifier,
            identifier_hash=verification.identifier_hash,
            status=verification.status,
            provider_status=verification.provider_status,
            field_match_summary=verification.field_match_summary,
            review_events=verification.review_events,
            rejection_reason=verification.rejection_reason,
            consented_at=verification.consented_at,
            submitted_at=verification.submitted_at,
            completed_at=verification.completed_at,
            created_at=verification.created_at,
            updated_at=verification.updated_at,
        )
        self.session.add(model)
        await self._flush_or_raise_conflict("That KYC verification could not be saved.")
        return model.to_domain()

    async def get(self, verification_id: UUID) -> KycVerification:
        model = await self.session.get(KycVerificationModel, verification_id)
        if model is None:
            raise NotFoundError(f"KYC verification '{verification_id}' was not found.")
        return model.to_domain()

    async def get_for_update(self, verification_id: UUID) -> KycVerification:
        """Fetch and lock a KYC verification row for a transactional decision."""
        statement: Select[tuple[KycVerificationModel]] = (
            select(KycVerificationModel)
            .where(KycVerificationModel.id == verification_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result = await self.session.execute(statement)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError(f"KYC verification '{verification_id}' was not found.")
        return model.to_domain()

    async def get_latest_for_user(self, user_id: UUID) -> KycVerification:
        statement: Select[tuple[KycVerificationModel]] = (
            select(KycVerificationModel)
            .where(KycVerificationModel.user_id == user_id)
            .order_by(KycVerificationModel.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(statement)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError(f"KYC verification for user '{user_id}' was not found.")
        return model.to_domain()

    async def get_by_provider_reference(
        self,
        provider_reference_id: str,
    ) -> KycVerification:
        statement: Select[tuple[KycVerificationModel]] = select(KycVerificationModel).where(
            KycVerificationModel.provider_reference_id == provider_reference_id
        )
        result = await self.session.execute(statement)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError(
                f"KYC verification with provider reference '{provider_reference_id}' was not found."
            )
        return model.to_domain()

    async def list_by_status(
        self,
        status: KycVerificationStatus,
        *,
        limit: int,
    ) -> list[KycVerification]:
        statement: Select[tuple[KycVerificationModel]] = (
            select(KycVerificationModel)
            .where(KycVerificationModel.status == status)
            .order_by(KycVerificationModel.submitted_at.asc())
            .limit(limit)
        )
        result = await self.session.execute(statement)
        return [model.to_domain() for model in result.scalars().all()]

    async def list_submitted_since(
        self,
        *,
        user_id: UUID,
        since: datetime,
        limit: int,
    ) -> list[KycVerification]:
        statement: Select[tuple[KycVerificationModel]] = (
            select(KycVerificationModel)
            .where(
                KycVerificationModel.user_id == user_id,
                KycVerificationModel.submitted_at >= since,
            )
            .order_by(KycVerificationModel.submitted_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(statement)
        return [model.to_domain() for model in result.scalars().all()]

    async def list_admin(
        self,
        status: KycVerificationStatus | None = None,
    ) -> list[KycVerification]:
        statement: Select[tuple[KycVerificationModel]] = select(KycVerificationModel).order_by(
            KycVerificationModel.created_at.desc()
        )
        if status is not None:
            statement = statement.where(KycVerificationModel.status == status)
        result = await self.session.execute(statement)
        return [model.to_domain() for model in result.scalars().all()]

    async def list_admin_page(
        self,
        *,
        status: KycVerificationStatus | None = None,
        cursor: Cursor | None = None,
        limit: int = 50,
    ) -> tuple[list[KycVerification], Cursor | None]:
        statement: Select[tuple[KycVerificationModel]] = (
            select(KycVerificationModel)
            .order_by(KycVerificationModel.created_at.desc(), KycVerificationModel.id.desc())
            .limit(limit + 1)
        )
        if status is not None:
            statement = statement.where(KycVerificationModel.status == status)
        cursor_clause = self._before_cursor(
            KycVerificationModel.created_at, KycVerificationModel.id, cursor
        )
        if cursor_clause is not None:
            statement = statement.where(cursor_clause)
        result = await self.session.execute(statement)
        models, next_cursor = self._page_cursor(result.scalars().all(), limit)
        return [model.to_domain() for model in models], next_cursor

    async def update(self, verification: KycVerification) -> KycVerification:
        model = await self.session.get(KycVerificationModel, verification.id)
        if model is None:
            raise NotFoundError(f"KYC verification '{verification.id}' was not found.")

        model.provider = verification.provider
        model.provider_reference_id = verification.provider_reference_id
        model.id_type = verification.id_type
        model.masked_identifier = verification.masked_identifier
        model.identifier_hash = verification.identifier_hash
        model.status = verification.status
        model.provider_status = verification.provider_status
        model.field_match_summary = verification.field_match_summary
        model.review_events = verification.review_events
        model.rejection_reason = verification.rejection_reason
        model.consented_at = verification.consented_at
        model.submitted_at = verification.submitted_at
        model.completed_at = verification.completed_at
        model.updated_at = verification.updated_at

        await self._flush_or_raise_conflict("That KYC verification could not be updated.")
        return model.to_domain()


class SqlAlchemyCurrencyRepository(SqlAlchemyRepository, CurrencyRepositoryProtocol):
    """Currency repository implementation."""

    async def add(self, currency: Currency) -> Currency:
        model = CurrencyModel(
            id=currency.id,
            code=currency.code,
            minor_unit=currency.minor_unit,
            status=currency.status,
            min_amount=Decimal(currency.min_amount),
            max_amount=Decimal(currency.max_amount),
            created_at=currency.created_at,
            updated_at=currency.updated_at,
        )
        self.session.add(model)
        await self._flush_or_raise_conflict("A currency with that code already exists.")
        return model.to_domain()

    async def get_by_code(self, code: str) -> Currency:
        statement: Select[tuple[CurrencyModel]] = select(CurrencyModel).where(
            CurrencyModel.code == code
        )
        result = await self.session.execute(statement)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError(f"Currency '{code}' was not found.")
        return model.to_domain()

    async def get(self, currency_id: UUID) -> Currency:
        model = await self.session.get(CurrencyModel, currency_id)
        if model is None:
            raise NotFoundError(f"Currency '{currency_id}' was not found.")
        return model.to_domain()

    async def list_active(self) -> list[Currency]:
        statement: Select[tuple[CurrencyModel]] = (
            select(CurrencyModel)
            .where(CurrencyModel.status == CurrencyStatus.ACTIVE)
            .order_by(CurrencyModel.code.asc())
        )
        result = await self.session.execute(statement)
        return [model.to_domain() for model in result.scalars().all()]


class SqlAlchemyCorridorRepository(SqlAlchemyRepository, CorridorRepositoryProtocol):
    """Corridor repository implementation."""

    @staticmethod
    def _details_load_options() -> tuple[Any, ...]:
        return (
            joinedload(CorridorModel.from_currency),
            joinedload(CorridorModel.to_currency),
            selectinload(CorridorModel.rails),
            with_loader_criteria(
                CorridorRailModel,
                CorridorRailModel.status == RailStatus.ACTIVE,
                include_aliases=True,
            ),
        )

    async def add(self, corridor: Corridor) -> Corridor:
        model = CorridorModel(
            id=corridor.id,
            from_currency_id=corridor.from_currency_id,
            to_currency_id=corridor.to_currency_id,
            status=corridor.status,
            funding_sla_minutes=corridor.funding_sla_minutes,
            fee_model_name=corridor.fee_model_name,
            created_at=corridor.created_at,
            updated_at=corridor.updated_at,
        )
        self.session.add(model)
        await self._flush_or_raise_conflict("That corridor already exists.")
        return model.to_domain()

    async def get(self, corridor_id: UUID) -> Corridor:
        model = await self.session.get(CorridorModel, corridor_id)
        if model is None:
            raise NotFoundError(f"Corridor '{corridor_id}' was not found.")
        return model.to_domain()

    async def get_by_currency_pair(self, from_currency_id: UUID, to_currency_id: UUID) -> Corridor:
        statement: Select[tuple[CorridorModel]] = select(CorridorModel).where(
            CorridorModel.from_currency_id == from_currency_id,
            CorridorModel.to_currency_id == to_currency_id,
        )
        result = await self.session.execute(statement)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError("The requested corridor was not found.")
        return model.to_domain()

    async def list_active_details(self) -> list[CorridorDetails]:
        statement: Select[tuple[CorridorModel]] = (
            select(CorridorModel)
            .options(*self._details_load_options())
            .where(
                CorridorModel.status == CorridorStatus.ACTIVE,
                CorridorModel.from_currency.has(CurrencyModel.status == CurrencyStatus.ACTIVE),
                CorridorModel.to_currency.has(CurrencyModel.status == CurrencyStatus.ACTIVE),
            )
        )
        result = await self.session.execute(statement)
        return [model.to_details() for model in result.unique().scalars().all()]

    async def get_active_details(self, corridor_id: UUID) -> CorridorDetails:
        statement: Select[tuple[CorridorModel]] = (
            select(CorridorModel)
            .options(*self._details_load_options())
            .where(
                CorridorModel.id == corridor_id,
                CorridorModel.status == CorridorStatus.ACTIVE,
                CorridorModel.from_currency.has(CurrencyModel.status == CurrencyStatus.ACTIVE),
                CorridorModel.to_currency.has(CurrencyModel.status == CurrencyStatus.ACTIVE),
            )
        )
        result = await self.session.execute(statement)
        model = result.unique().scalar_one_or_none()
        if model is None:
            raise NotFoundError(f"Corridor '{corridor_id}' was not found.")
        return model.to_details()

    async def get_active_details_by_currency_pair(
        self,
        from_currency_code: str,
        to_currency_code: str,
    ) -> CorridorDetails:
        statement: Select[tuple[CorridorModel]] = (
            select(CorridorModel)
            .options(*self._details_load_options())
            .where(
                CorridorModel.status == CorridorStatus.ACTIVE,
                CorridorModel.from_currency.has(
                    and_(
                        CurrencyModel.code == from_currency_code,
                        CurrencyModel.status == CurrencyStatus.ACTIVE,
                    )
                ),
                CorridorModel.to_currency.has(
                    and_(
                        CurrencyModel.code == to_currency_code,
                        CurrencyModel.status == CurrencyStatus.ACTIVE,
                    )
                ),
            )
        )
        result = await self.session.execute(statement)
        model = result.unique().scalar_one_or_none()
        if model is None:
            raise NotFoundError(
                f"Corridor '{from_currency_code}/{to_currency_code}' was not found."
            )
        return model.to_details()


class SqlAlchemyCorridorRailRepository(SqlAlchemyRepository, CorridorRailRepositoryProtocol):
    """Corridor rail repository implementation."""

    async def add(self, rail: CorridorRail) -> CorridorRail:
        model = CorridorRailModel(
            id=rail.id,
            corridor_id=rail.corridor_id,
            flow_type=rail.flow_type,
            priority_order=rail.priority_order,
            provider=rail.provider,
            method=rail.method,
            status=rail.status,
            created_at=rail.created_at,
            updated_at=rail.updated_at,
        )
        self.session.add(model)
        await self._flush_or_raise_conflict("That corridor rail priority is already in use.")
        return model.to_domain()

    async def list_for_corridor(self, corridor_id: UUID) -> list[CorridorRail]:
        statement: Select[tuple[CorridorRailModel]] = (
            select(CorridorRailModel)
            .where(CorridorRailModel.corridor_id == corridor_id)
            .where(CorridorRailModel.status == RailStatus.ACTIVE)
            .order_by(CorridorRailModel.priority_order.asc())
        )
        result = await self.session.execute(statement)
        return [model.to_domain() for model in result.scalars().all()]


class SqlAlchemyExchangeRequestRepository(SqlAlchemyRepository, ExchangeRequestRepositoryProtocol):
    """Exchange request repository implementation."""

    @staticmethod
    def _details_load_options() -> tuple[Any, ...]:
        return (
            joinedload(ExchangeRequestModel.from_currency),
            joinedload(ExchangeRequestModel.to_currency),
        )

    @staticmethod
    def _board_visible_statuses() -> tuple[ExchangeRequestStatus, ...]:
        return (
            ExchangeRequestStatus.REQUEST_OPEN,
            ExchangeRequestStatus.OFFER_PENDING,
        )

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(UTC)

    async def add(self, exchange_request: ExchangeRequest) -> ExchangeRequest:
        model = ExchangeRequestModel(
            id=exchange_request.id,
            relisted_from_request_id=exchange_request.relisted_from_request_id,
            creator_user_id=exchange_request.creator_user_id,
            from_currency_id=exchange_request.from_currency_id,
            to_currency_id=exchange_request.to_currency_id,
            from_amount=exchange_request.from_amount,
            preferred_rate=exchange_request.preferred_rate,
            min_rate=exchange_request.min_rate,
            status=exchange_request.status,
            expires_at=exchange_request.expires_at,
            created_at=exchange_request.created_at,
            updated_at=exchange_request.updated_at,
        )
        self.session.add(model)
        await self._flush_or_raise_conflict("That exchange request could not be saved.")
        return model.to_domain()

    async def update(self, exchange_request: ExchangeRequest) -> ExchangeRequest:
        model = await self.session.get(ExchangeRequestModel, exchange_request.id)
        if model is None:
            raise NotFoundError(f"Exchange request '{exchange_request.id}' was not found.")

        model.status = exchange_request.status
        model.relisted_from_request_id = exchange_request.relisted_from_request_id
        model.from_amount = exchange_request.from_amount
        model.preferred_rate = exchange_request.preferred_rate
        model.min_rate = exchange_request.min_rate
        model.expires_at = exchange_request.expires_at
        model.updated_at = exchange_request.updated_at

        await self._flush_or_raise_conflict("That exchange request could not be saved.")
        return model.to_domain()

    async def get(self, request_id: UUID) -> ExchangeRequest:
        model = await self.session.get(ExchangeRequestModel, request_id)
        if model is None:
            raise NotFoundError(f"Exchange request '{request_id}' was not found.")
        return model.to_domain()

    async def get_for_update(self, request_id: UUID) -> ExchangeRequest:
        statement: Select[tuple[ExchangeRequestModel]] = (
            select(ExchangeRequestModel)
            .where(ExchangeRequestModel.id == request_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        result = await self.session.execute(statement)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError(f"Exchange request '{request_id}' was not found.")
        return model.to_domain()

    async def get_details_for_user(self, request_id: UUID, user_id: UUID) -> ExchangeRequestDetails:
        statement: Select[tuple[ExchangeRequestModel]] = (
            select(ExchangeRequestModel)
            .options(*self._details_load_options())
            .where(
                ExchangeRequestModel.id == request_id,
                ExchangeRequestModel.creator_user_id == user_id,
            )
        )
        result = await self.session.execute(statement)
        model = result.unique().scalar_one_or_none()
        if model is None:
            raise NotFoundError(f"Exchange request '{request_id}' was not found.")
        return model.to_details()

    async def list_details_for_user(self, user_id: UUID) -> list[ExchangeRequestDetails]:
        statement: Select[tuple[ExchangeRequestModel]] = (
            select(ExchangeRequestModel)
            .options(*self._details_load_options())
            .where(ExchangeRequestModel.creator_user_id == user_id)
            .order_by(ExchangeRequestModel.created_at.desc())
        )
        result = await self.session.execute(statement)
        return [model.to_details() for model in result.unique().scalars().all()]

    async def list_board_details(self, viewer_user_id: UUID) -> list[ExchangeRequestDetails]:
        statement: Select[tuple[ExchangeRequestModel]] = (
            select(ExchangeRequestModel)
            .options(*self._details_load_options())
            .where(
                ExchangeRequestModel.creator_user_id != viewer_user_id,
                ExchangeRequestModel.creator.has(UserModel.status == UserStatus.ACTIVE),
                ExchangeRequestModel.status.in_(self._board_visible_statuses()),
                ExchangeRequestModel.expires_at > self._utc_now(),
                ExchangeRequestModel.from_currency.has(
                    CurrencyModel.status == CurrencyStatus.ACTIVE
                ),
                ExchangeRequestModel.to_currency.has(CurrencyModel.status == CurrencyStatus.ACTIVE),
            )
            .order_by(ExchangeRequestModel.created_at.desc())
        )
        result = await self.session.execute(statement)
        return [model.to_details() for model in result.unique().scalars().all()]

    async def get_visible_details(
        self,
        request_id: UUID,
        viewer_user_id: UUID,
    ) -> ExchangeRequestDetails:
        statement: Select[tuple[ExchangeRequestModel]] = (
            select(ExchangeRequestModel)
            .options(*self._details_load_options())
            .where(
                ExchangeRequestModel.id == request_id,
                or_(
                    ExchangeRequestModel.creator_user_id == viewer_user_id,
                    and_(
                        ExchangeRequestModel.creator_user_id != viewer_user_id,
                        ExchangeRequestModel.creator.has(UserModel.status == UserStatus.ACTIVE),
                        ExchangeRequestModel.status.in_(self._board_visible_statuses()),
                        ExchangeRequestModel.expires_at > self._utc_now(),
                        ExchangeRequestModel.from_currency.has(
                            CurrencyModel.status == CurrencyStatus.ACTIVE
                        ),
                        ExchangeRequestModel.to_currency.has(
                            CurrencyModel.status == CurrencyStatus.ACTIVE
                        ),
                    ),
                ),
            )
        )
        result = await self.session.execute(statement)
        model = result.unique().scalar_one_or_none()
        if model is None:
            raise NotFoundError(f"Exchange request '{request_id}' was not found.")
        return model.to_details()

    async def list_admin_details_page(
        self,
        *,
        statuses: tuple[ExchangeRequestStatus, ...] | None = None,
        cursor: Cursor | None = None,
        limit: int = 50,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[list[ExchangeRequestDetails], Cursor | None]:
        statement: Select[tuple[ExchangeRequestModel]] = (
            select(ExchangeRequestModel)
            .options(*self._details_load_options())
            .order_by(ExchangeRequestModel.created_at.desc(), ExchangeRequestModel.id.desc())
            .limit(limit + 1)
        )
        if statuses:
            statement = statement.where(ExchangeRequestModel.status.in_(statuses))
        if created_from is not None:
            statement = statement.where(ExchangeRequestModel.created_at >= created_from)
        if created_to is not None:
            statement = statement.where(ExchangeRequestModel.created_at <= created_to)
        cursor_clause = self._before_cursor(
            ExchangeRequestModel.created_at, ExchangeRequestModel.id, cursor
        )
        if cursor_clause is not None:
            statement = statement.where(cursor_clause)
        result = await self.session.execute(statement)
        models, next_cursor = self._page_cursor(result.unique().scalars().all(), limit)
        return [model.to_details() for model in models], next_cursor

    async def expire_due(self, now: datetime) -> list[ExchangeRequest]:
        result = await self.session.execute(
            update(ExchangeRequestModel)
            .where(
                ExchangeRequestModel.status.in_(self._board_visible_statuses()),
                ExchangeRequestModel.expires_at <= now,
            )
            .values(status=ExchangeRequestStatus.EXPIRED, updated_at=now)
            .returning(ExchangeRequestModel)
        )
        return [model.to_domain() for model in result.scalars().all()]

    async def reopen_pending_without_active_offers(self, now: datetime) -> list[ExchangeRequest]:
        result = await self.session.execute(
            update(ExchangeRequestModel)
            .where(
                ExchangeRequestModel.status == ExchangeRequestStatus.OFFER_PENDING,
                ~ExchangeRequestModel.offers.any(
                    ExchangeOfferModel.status == ExchangeOfferStatus.ACTIVE
                ),
            )
            .values(status=ExchangeRequestStatus.REQUEST_OPEN, updated_at=now)
            .returning(ExchangeRequestModel)
        )
        return [model.to_domain() for model in result.scalars().all()]

    async def has_any_offers(self, request_id: UUID) -> bool:
        statement: Select[tuple[UUID]] = (
            select(ExchangeOfferModel.id)
            .where(ExchangeOfferModel.request_id == request_id)
            .limit(1)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def has_relisted_successor(self, request_id: UUID) -> bool:
        statement: Select[tuple[UUID]] = (
            select(ExchangeRequestModel.id)
            .where(ExchangeRequestModel.relisted_from_request_id == request_id)
            .limit(1)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def has_actionable_for_creator(self, user_id: UUID, now: datetime) -> bool:
        statement: Select[tuple[UUID]] = (
            select(ExchangeRequestModel.id)
            .where(
                ExchangeRequestModel.creator_user_id == user_id,
                ExchangeRequestModel.status.in_(self._board_visible_statuses()),
                ExchangeRequestModel.expires_at > now,
            )
            .limit(1)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def list_board_details_page(
        self,
        viewer_user_id: UUID,
        *,
        cursor: Cursor | None = None,
        limit: int = 50,
        statuses: tuple[ExchangeRequestStatus, ...] | None = None,
        from_currency_code: str | None = None,
        to_currency_code: str | None = None,
        min_amount: Decimal | None = None,
        max_amount: Decimal | None = None,
        min_preferred_rate: Decimal | None = None,
        max_preferred_rate: Decimal | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[list[ExchangeRequestDetails], Cursor | None]:
        visible_statuses = tuple(
            status
            for status in (statuses or tuple(self._board_visible_statuses()))
            if status in self._board_visible_statuses()
        )
        statement: Select[tuple[ExchangeRequestModel]] = (
            select(ExchangeRequestModel)
            .options(*self._details_load_options())
            .where(
                ExchangeRequestModel.creator_user_id != viewer_user_id,
                ExchangeRequestModel.creator.has(UserModel.status == UserStatus.ACTIVE),
                ExchangeRequestModel.status.in_(visible_statuses),
                ExchangeRequestModel.expires_at > self._utc_now(),
                ExchangeRequestModel.from_currency.has(
                    CurrencyModel.status == CurrencyStatus.ACTIVE
                ),
                ExchangeRequestModel.to_currency.has(CurrencyModel.status == CurrencyStatus.ACTIVE),
            )
            .order_by(ExchangeRequestModel.created_at.desc(), ExchangeRequestModel.id.desc())
            .limit(limit + 1)
        )
        if from_currency_code is not None:
            statement = statement.where(
                ExchangeRequestModel.from_currency.has(CurrencyModel.code == from_currency_code)
            )
        if to_currency_code is not None:
            statement = statement.where(
                ExchangeRequestModel.to_currency.has(CurrencyModel.code == to_currency_code)
            )
        if min_amount is not None:
            statement = statement.where(ExchangeRequestModel.from_amount >= min_amount)
        if max_amount is not None:
            statement = statement.where(ExchangeRequestModel.from_amount <= max_amount)
        if min_preferred_rate is not None:
            statement = statement.where(ExchangeRequestModel.preferred_rate >= min_preferred_rate)
        if max_preferred_rate is not None:
            statement = statement.where(ExchangeRequestModel.preferred_rate <= max_preferred_rate)
        if created_from is not None:
            statement = statement.where(ExchangeRequestModel.created_at >= created_from)
        if created_to is not None:
            statement = statement.where(ExchangeRequestModel.created_at <= created_to)
        cursor_clause = self._before_cursor(
            ExchangeRequestModel.created_at, ExchangeRequestModel.id, cursor
        )
        if cursor_clause is not None:
            statement = statement.where(cursor_clause)
        result = await self.session.execute(statement)
        models, next_cursor = self._page_cursor(result.unique().scalars().all(), limit)
        return [model.to_details() for model in models], next_cursor

    async def list_details_for_user_page(
        self,
        user_id: UUID,
        *,
        cursor: Cursor | None = None,
        limit: int = 50,
        statuses: tuple[ExchangeRequestStatus, ...] | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[list[ExchangeRequestDetails], Cursor | None]:
        statement: Select[tuple[ExchangeRequestModel]] = (
            select(ExchangeRequestModel)
            .options(*self._details_load_options())
            .where(ExchangeRequestModel.creator_user_id == user_id)
            .order_by(ExchangeRequestModel.created_at.desc(), ExchangeRequestModel.id.desc())
            .limit(limit + 1)
        )
        if statuses:
            statement = statement.where(ExchangeRequestModel.status.in_(statuses))
        if created_from is not None:
            statement = statement.where(ExchangeRequestModel.created_at >= created_from)
        if created_to is not None:
            statement = statement.where(ExchangeRequestModel.created_at <= created_to)
        cursor_clause = self._before_cursor(
            ExchangeRequestModel.created_at, ExchangeRequestModel.id, cursor
        )
        if cursor_clause is not None:
            statement = statement.where(cursor_clause)
        result = await self.session.execute(statement)
        models, next_cursor = self._page_cursor(result.unique().scalars().all(), limit)
        return [model.to_details() for model in models], next_cursor


class SqlAlchemyExchangeOfferRepository(SqlAlchemyRepository, ExchangeOfferRepositoryProtocol):
    """Exchange offer repository implementation."""

    @staticmethod
    def _details_load_options() -> tuple[Any, ...]:
        return (
            joinedload(ExchangeOfferModel.request).joinedload(ExchangeRequestModel.from_currency),
            joinedload(ExchangeOfferModel.request).joinedload(ExchangeRequestModel.to_currency),
        )

    async def add(self, exchange_offer: ExchangeOffer) -> ExchangeOffer:
        model = ExchangeOfferModel(
            id=exchange_offer.id,
            request_id=exchange_offer.request_id,
            offer_user_id=exchange_offer.offer_user_id,
            offered_rate=exchange_offer.offered_rate,
            status=exchange_offer.status,
            expires_at=exchange_offer.expires_at,
            created_at=exchange_offer.created_at,
            updated_at=exchange_offer.updated_at,
        )
        self.session.add(model)
        await self._flush_or_raise_conflict("That exchange offer could not be saved.")
        return model.to_domain()

    async def update(self, exchange_offer: ExchangeOffer) -> ExchangeOffer:
        model = await self.session.get(ExchangeOfferModel, exchange_offer.id)
        if model is None:
            raise NotFoundError(f"Exchange offer '{exchange_offer.id}' was not found.")

        model.status = exchange_offer.status
        model.offered_rate = exchange_offer.offered_rate
        model.expires_at = exchange_offer.expires_at
        model.updated_at = exchange_offer.updated_at

        await self._flush_or_raise_conflict("That exchange offer could not be saved.")
        return model.to_domain()

    async def get(self, offer_id: UUID) -> ExchangeOffer:
        model = await self.session.get(ExchangeOfferModel, offer_id)
        if model is None:
            raise NotFoundError(f"Exchange offer '{offer_id}' was not found.")
        return model.to_domain()

    async def get_for_update(self, offer_id: UUID) -> ExchangeOffer:
        statement: Select[tuple[ExchangeOfferModel]] = (
            select(ExchangeOfferModel)
            .where(ExchangeOfferModel.id == offer_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        result = await self.session.execute(statement)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError(f"Exchange offer '{offer_id}' was not found.")
        return model.to_domain()

    async def list_for_request(self, request_id: UUID) -> list[ExchangeOffer]:
        statement: Select[tuple[ExchangeOfferModel]] = (
            select(ExchangeOfferModel)
            .where(ExchangeOfferModel.request_id == request_id)
            .order_by(ExchangeOfferModel.created_at.desc())
        )
        result = await self.session.execute(statement)
        return [model.to_domain() for model in result.scalars().all()]

    async def list_details_for_request(self, request_id: UUID) -> list[ExchangeOfferDetails]:
        statement: Select[tuple[ExchangeOfferModel]] = (
            select(ExchangeOfferModel)
            .options(*self._details_load_options())
            .where(ExchangeOfferModel.request_id == request_id)
            .order_by(ExchangeOfferModel.created_at.desc())
        )
        result = await self.session.execute(statement)
        return [model.to_details() for model in result.scalars().all()]

    async def get_visible_details(
        self, offer_id: UUID, viewer_user_id: UUID
    ) -> ExchangeOfferDetails:
        statement: Select[tuple[ExchangeOfferModel]] = (
            select(ExchangeOfferModel)
            .options(*self._details_load_options())
            .where(
                ExchangeOfferModel.id == offer_id,
                or_(
                    ExchangeOfferModel.offer_user_id == viewer_user_id,
                    ExchangeOfferModel.request.has(
                        ExchangeRequestModel.creator_user_id == viewer_user_id
                    ),
                ),
            )
        )
        result = await self.session.execute(statement)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError(f"Exchange offer '{offer_id}' was not found.")
        return model.to_details()

    async def list_details_for_request_page(
        self,
        request_id: UUID,
        *,
        cursor: Cursor | None = None,
        limit: int = 50,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[list[ExchangeOfferDetails], Cursor | None]:
        statement: Select[tuple[ExchangeOfferModel]] = (
            select(ExchangeOfferModel)
            .options(*self._details_load_options())
            .where(ExchangeOfferModel.request_id == request_id)
            .order_by(ExchangeOfferModel.created_at.desc(), ExchangeOfferModel.id.desc())
            .limit(limit + 1)
        )
        if created_from is not None:
            statement = statement.where(ExchangeOfferModel.created_at >= created_from)
        if created_to is not None:
            statement = statement.where(ExchangeOfferModel.created_at <= created_to)
        cursor_clause = self._before_cursor(
            ExchangeOfferModel.created_at, ExchangeOfferModel.id, cursor
        )
        if cursor_clause is not None:
            statement = statement.where(cursor_clause)
        result = await self.session.execute(statement)
        models, next_cursor = self._page_cursor(result.scalars().all(), limit)
        return [model.to_details() for model in models], next_cursor

    async def list_details_for_user_page(
        self,
        user_id: UUID,
        *,
        cursor: Cursor | None = None,
        limit: int = 50,
        statuses: tuple[ExchangeOfferStatus, ...] | None = None,
        min_offered_rate: Decimal | None = None,
        max_offered_rate: Decimal | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[list[ExchangeOfferDetails], Cursor | None]:
        statement: Select[tuple[ExchangeOfferModel]] = (
            select(ExchangeOfferModel)
            .options(*self._details_load_options())
            .where(ExchangeOfferModel.offer_user_id == user_id)
            .order_by(ExchangeOfferModel.created_at.desc(), ExchangeOfferModel.id.desc())
            .limit(limit + 1)
        )
        if statuses:
            statement = statement.where(ExchangeOfferModel.status.in_(statuses))
        if min_offered_rate is not None:
            statement = statement.where(ExchangeOfferModel.offered_rate >= min_offered_rate)
        if max_offered_rate is not None:
            statement = statement.where(ExchangeOfferModel.offered_rate <= max_offered_rate)
        if created_from is not None:
            statement = statement.where(ExchangeOfferModel.created_at >= created_from)
        if created_to is not None:
            statement = statement.where(ExchangeOfferModel.created_at <= created_to)
        cursor_clause = self._before_cursor(
            ExchangeOfferModel.created_at, ExchangeOfferModel.id, cursor
        )
        if cursor_clause is not None:
            statement = statement.where(cursor_clause)
        result = await self.session.execute(statement)
        models, next_cursor = self._page_cursor(result.scalars().all(), limit)
        return [model.to_details() for model in models], next_cursor

    async def has_active_offer_for_request(self, request_id: UUID, user_id: UUID) -> bool:
        statement: Select[tuple[ExchangeOfferModel]] = select(ExchangeOfferModel).where(
            ExchangeOfferModel.request_id == request_id,
            ExchangeOfferModel.offer_user_id == user_id,
            ExchangeOfferModel.status == ExchangeOfferStatus.ACTIVE,
            ExchangeOfferModel.expires_at > SqlAlchemyExchangeRequestRepository._utc_now(),
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def has_active_for_user(self, user_id: UUID, now: datetime) -> bool:
        statement: Select[tuple[UUID]] = (
            select(ExchangeOfferModel.id)
            .where(
                ExchangeOfferModel.offer_user_id == user_id,
                ExchangeOfferModel.status == ExchangeOfferStatus.ACTIVE,
                ExchangeOfferModel.expires_at > now,
            )
            .limit(1)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def list_admin_details_page(
        self,
        *,
        statuses: tuple[ExchangeOfferStatus, ...] | None = None,
        cursor: Cursor | None = None,
        limit: int = 50,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[list[ExchangeOfferDetails], Cursor | None]:
        statement: Select[tuple[ExchangeOfferModel]] = (
            select(ExchangeOfferModel)
            .options(*self._details_load_options())
            .order_by(ExchangeOfferModel.created_at.desc(), ExchangeOfferModel.id.desc())
            .limit(limit + 1)
        )
        if statuses:
            statement = statement.where(ExchangeOfferModel.status.in_(statuses))
        if created_from is not None:
            statement = statement.where(ExchangeOfferModel.created_at >= created_from)
        if created_to is not None:
            statement = statement.where(ExchangeOfferModel.created_at <= created_to)
        cursor_clause = self._before_cursor(
            ExchangeOfferModel.created_at, ExchangeOfferModel.id, cursor
        )
        if cursor_clause is not None:
            statement = statement.where(cursor_clause)
        result = await self.session.execute(statement)
        models, next_cursor = self._page_cursor(result.scalars().all(), limit)
        return [model.to_details() for model in models], next_cursor

    async def expire_due(self, now: datetime) -> list[ExchangeOffer]:
        result = await self.session.execute(
            update(ExchangeOfferModel)
            .where(
                ExchangeOfferModel.status == ExchangeOfferStatus.ACTIVE,
                or_(
                    ExchangeOfferModel.expires_at <= now,
                    ExchangeOfferModel.request.has(
                        ExchangeRequestModel.status == ExchangeRequestStatus.EXPIRED
                    ),
                ),
            )
            .values(status=ExchangeOfferStatus.EXPIRED, updated_at=now)
            .returning(ExchangeOfferModel)
        )
        return [model.to_domain() for model in result.scalars().all()]


class SqlAlchemyTradeContractRepository(SqlAlchemyRepository, TradeContractRepositoryProtocol):
    """Trade contract repository implementation."""

    @staticmethod
    def _details_load_options() -> tuple[Any, ...]:
        return (
            joinedload(TradeContractModel.request).joinedload(ExchangeRequestModel.from_currency),
            joinedload(TradeContractModel.request).joinedload(ExchangeRequestModel.to_currency),
            joinedload(TradeContractModel.accepted_offer),
        )

    async def add(self, trade_contract: TradeContract) -> TradeContract:
        model = TradeContractModel(
            id=trade_contract.id,
            request_id=trade_contract.request_id,
            accepted_offer_id=trade_contract.accepted_offer_id,
            agreed_rate=trade_contract.agreed_rate,
            reference_rate_snapshot=trade_contract.reference_rate_snapshot,
            from_amount=trade_contract.from_amount,
            to_amount=trade_contract.to_amount,
            funding_deadline_at=trade_contract.funding_deadline_at,
            status=trade_contract.status,
            created_at=trade_contract.created_at,
            updated_at=trade_contract.updated_at,
        )
        self.session.add(model)
        await self._flush_or_raise_conflict("That trade contract could not be saved.")
        return model.to_domain()

    async def get(self, trade_id: UUID) -> TradeContract:
        model = await self.session.get(TradeContractModel, trade_id)
        if model is None:
            raise NotFoundError(f"Trade '{trade_id}' was not found.")
        return model.to_domain()

    async def get_for_participant(self, trade_id: UUID, user_id: UUID) -> TradeContractDetails:
        statement: Select[tuple[TradeContractModel]] = (
            select(TradeContractModel)
            .options(*self._details_load_options())
            .where(
                TradeContractModel.id == trade_id,
                or_(
                    TradeContractModel.request.has(ExchangeRequestModel.creator_user_id == user_id),
                    TradeContractModel.accepted_offer.has(
                        ExchangeOfferModel.offer_user_id == user_id
                    ),
                ),
            )
        )
        result = await self.session.execute(statement)
        model = result.unique().scalar_one_or_none()
        if model is None:
            raise NotFoundError(f"Trade '{trade_id}' was not found.")
        return model.to_details()

    async def list_for_participant_page(
        self,
        user_id: UUID,
        *,
        cursor: Cursor | None = None,
        limit: int = 50,
        statuses: tuple[TradeContractStatus, ...] | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[list[TradeContractDetails], Cursor | None]:
        statement: Select[tuple[TradeContractModel]] = (
            select(TradeContractModel)
            .options(*self._details_load_options())
            .where(
                or_(
                    TradeContractModel.request.has(ExchangeRequestModel.creator_user_id == user_id),
                    TradeContractModel.accepted_offer.has(
                        ExchangeOfferModel.offer_user_id == user_id
                    ),
                )
            )
            .order_by(TradeContractModel.created_at.desc(), TradeContractModel.id.desc())
            .limit(limit + 1)
        )
        if statuses:
            statement = statement.where(TradeContractModel.status.in_(statuses))
        if created_from is not None:
            statement = statement.where(TradeContractModel.created_at >= created_from)
        if created_to is not None:
            statement = statement.where(TradeContractModel.created_at <= created_to)
        cursor_clause = self._before_cursor(
            TradeContractModel.created_at, TradeContractModel.id, cursor
        )
        if cursor_clause is not None:
            statement = statement.where(cursor_clause)
        result = await self.session.execute(statement)
        models, next_cursor = self._page_cursor(result.unique().scalars().all(), limit)
        return [model.to_details() for model in models], next_cursor

    async def list_admin_details_page(
        self,
        *,
        statuses: tuple[TradeContractStatus, ...] | None = None,
        cursor: Cursor | None = None,
        limit: int = 50,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[list[TradeContractDetails], Cursor | None]:
        statement: Select[tuple[TradeContractModel]] = (
            select(TradeContractModel)
            .options(*self._details_load_options())
            .order_by(TradeContractModel.created_at.desc(), TradeContractModel.id.desc())
            .limit(limit + 1)
        )
        if statuses:
            statement = statement.where(TradeContractModel.status.in_(statuses))
        if created_from is not None:
            statement = statement.where(TradeContractModel.created_at >= created_from)
        if created_to is not None:
            statement = statement.where(TradeContractModel.created_at <= created_to)
        cursor_clause = self._before_cursor(
            TradeContractModel.created_at, TradeContractModel.id, cursor
        )
        if cursor_clause is not None:
            statement = statement.where(cursor_clause)
        result = await self.session.execute(statement)
        models, next_cursor = self._page_cursor(result.unique().scalars().all(), limit)
        return [model.to_details() for model in models], next_cursor

    async def cancel_due_unfunded(self, now: datetime) -> list[TradeContractDetails]:
        result = await self.session.execute(
            update(TradeContractModel)
            .where(
                TradeContractModel.status == TradeContractStatus.TERMS_LOCKED,
                TradeContractModel.funding_deadline_at <= now,
            )
            .values(status=TradeContractStatus.CANCELLED, updated_at=now)
            .returning(TradeContractModel.id)
        )
        trade_ids = result.scalars().all()
        if not trade_ids:
            return []
        details_result = await self.session.execute(
            select(TradeContractModel)
            .options(*self._details_load_options())
            .where(TradeContractModel.id.in_(trade_ids))
        )
        return [model.to_details() for model in details_result.unique().scalars().all()]

    async def has_non_terminal_for_participant(self, user_id: UUID) -> bool:
        terminal_statuses = (TradeContractStatus.SETTLED, TradeContractStatus.CANCELLED)
        statement: Select[tuple[UUID]] = (
            select(TradeContractModel.id)
            .join(
                ExchangeRequestModel,
                TradeContractModel.request_id == ExchangeRequestModel.id,
            )
            .join(
                ExchangeOfferModel,
                TradeContractModel.accepted_offer_id == ExchangeOfferModel.id,
            )
            .where(
                TradeContractModel.status.not_in(terminal_statuses),
                or_(
                    ExchangeRequestModel.creator_user_id == user_id,
                    ExchangeOfferModel.offer_user_id == user_id,
                ),
            )
            .limit(1)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none() is not None


class SqlAlchemyOutboxEventRepository(SqlAlchemyRepository, OutboxEventRepositoryProtocol):
    """Outbox event repository implementation."""

    async def add(self, event: OutboxEvent) -> OutboxEvent:
        model = OutboxEventModel(
            id=event.id,
            event_type=event.event_type,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            recipient_user_id=event.recipient_user_id,
            payload=event.payload,
            status=event.status,
            attempt_count=event.attempt_count,
            next_attempt_at=event.next_attempt_at,
            last_error=event.last_error,
            created_at=event.created_at,
            updated_at=event.updated_at,
        )
        self.session.add(model)
        await self._flush_or_raise_conflict("That outbox event could not be saved.")
        return model.to_domain()

    async def list_admin(
        self,
        status: OutboxEventStatus | None = None,
        event_type: str | None = None,
    ) -> list[OutboxEvent]:
        statement: Select[tuple[OutboxEventModel]] = select(OutboxEventModel).order_by(
            OutboxEventModel.created_at.desc()
        )
        if status is not None:
            statement = statement.where(OutboxEventModel.status == status)
        if event_type is not None:
            statement = statement.where(OutboxEventModel.event_type == event_type)
        result = await self.session.execute(statement)
        return [model.to_domain() for model in result.scalars().all()]

    async def list_admin_page(
        self,
        *,
        status: OutboxEventStatus | None = None,
        event_type: str | None = None,
        cursor: Cursor | None = None,
        limit: int = 50,
    ) -> tuple[list[OutboxEvent], Cursor | None]:
        statement: Select[tuple[OutboxEventModel]] = (
            select(OutboxEventModel)
            .order_by(OutboxEventModel.created_at.desc(), OutboxEventModel.id.desc())
            .limit(limit + 1)
        )
        if status is not None:
            statement = statement.where(OutboxEventModel.status == status)
        if event_type is not None:
            statement = statement.where(OutboxEventModel.event_type == event_type)
        cursor_clause = self._before_cursor(
            OutboxEventModel.created_at, OutboxEventModel.id, cursor
        )
        if cursor_clause is not None:
            statement = statement.where(cursor_clause)
        result = await self.session.execute(statement)
        models, next_cursor = self._page_cursor(result.scalars().all(), limit)
        return [model.to_domain() for model in models], next_cursor

    async def claim_due_for_dispatch(
        self,
        *,
        now: datetime,
        processing_deadline: datetime,
        limit: int,
    ) -> list[OutboxEvent]:
        statement: Select[tuple[OutboxEventModel]] = (
            select(OutboxEventModel)
            .where(
                or_(
                    and_(
                        OutboxEventModel.status.in_(
                            (OutboxEventStatus.PENDING, OutboxEventStatus.FAILED)
                        ),
                        or_(
                            OutboxEventModel.next_attempt_at.is_(None),
                            OutboxEventModel.next_attempt_at <= now,
                        ),
                    ),
                    and_(
                        OutboxEventModel.status == OutboxEventStatus.PROCESSING,
                        OutboxEventModel.next_attempt_at <= now,
                    ),
                ),
            )
            .order_by(OutboxEventModel.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self.session.execute(statement)
        models = result.scalars().all()
        for model in models:
            model.status = OutboxEventStatus.PROCESSING
            model.next_attempt_at = processing_deadline
            model.updated_at = now
        await self._flush_or_raise_conflict("Due outbox events could not be claimed.")
        return [model.to_domain() for model in models]

    async def mark_delivered(
        self,
        *,
        event_id: UUID,
        expected_processing_deadline: datetime,
        now: datetime,
    ) -> OutboxEvent | None:
        statement = (
            update(OutboxEventModel)
            .where(
                OutboxEventModel.id == event_id,
                OutboxEventModel.status == OutboxEventStatus.PROCESSING,
                OutboxEventModel.next_attempt_at == expected_processing_deadline,
            )
            .values(
                status=OutboxEventStatus.DELIVERED,
                next_attempt_at=None,
                last_error=None,
                updated_at=now,
            )
            .returning(OutboxEventModel)
        )
        result = await self.session.execute(statement)
        model = result.scalar_one_or_none()
        return model.to_domain() if model is not None else None

    async def mark_failed(
        self,
        *,
        event_id: UUID,
        status: OutboxEventStatus,
        attempt_count: int,
        last_error: str,
        next_attempt_at: datetime | None,
        now: datetime,
        expected_processing_deadline: datetime,
    ) -> OutboxEvent | None:
        statement = (
            update(OutboxEventModel)
            .where(
                OutboxEventModel.id == event_id,
                OutboxEventModel.status == OutboxEventStatus.PROCESSING,
                OutboxEventModel.next_attempt_at == expected_processing_deadline,
            )
            .values(
                status=status,
                attempt_count=attempt_count,
                last_error=last_error[:1000],
                next_attempt_at=next_attempt_at,
                updated_at=now,
            )
            .returning(OutboxEventModel)
        )
        result = await self.session.execute(statement)
        model = result.scalar_one_or_none()
        return model.to_domain() if model is not None else None


class SqlAlchemyAccountAuditEventRepository(
    SqlAlchemyRepository,
    AccountAuditEventRepositoryProtocol,
):
    """Append-only account audit event repository."""

    async def add(self, event: AccountAuditEvent) -> AccountAuditEvent:
        model = AccountAuditEventModel(
            id=event.id,
            subject_user_id=event.subject_user_id,
            actor_user_id=event.actor_user_id,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            metadata_json=event.metadata,
        )
        self.session.add(model)
        await self._flush_or_raise_conflict("That account audit event could not be recorded.")
        return model.to_domain()

    async def list_for_subject(self, subject_user_id: UUID) -> list[AccountAuditEvent]:
        statement: Select[tuple[AccountAuditEventModel]] = (
            select(AccountAuditEventModel)
            .where(AccountAuditEventModel.subject_user_id == subject_user_id)
            .order_by(
                AccountAuditEventModel.occurred_at.desc(),
                AccountAuditEventModel.id.desc(),
            )
        )
        result = await self.session.execute(statement)
        return [model.to_domain() for model in result.scalars().all()]
