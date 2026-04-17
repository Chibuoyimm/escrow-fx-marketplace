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
    Corridor,
    CorridorDetails,
    CorridorRail,
    Currency,
    ExchangeOffer,
    ExchangeOfferDetails,
    ExchangeRequest,
    ExchangeRequestDetails,
    TradeContract,
    TradeContractDetails,
    User,
)
from app.domain.enums import (
    CorridorStatus,
    CurrencyStatus,
    ExchangeOfferStatus,
    ExchangeRequestStatus,
    RailStatus,
    TradeContractStatus,
)
from app.domain.exceptions import ConflictError, NotFoundError
from app.infrastructure.exceptions import InfrastructureError
from app.models.corridor import CorridorModel, CorridorRailModel
from app.models.currency import CurrencyModel
from app.models.exchange_offer import ExchangeOfferModel
from app.models.exchange_request import ExchangeRequestModel
from app.models.trade_contract import TradeContractModel
from app.models.user import UserModel
from app.repositories.protocols import (
    CorridorRailRepositoryProtocol,
    CorridorRepositoryProtocol,
    CurrencyRepositoryProtocol,
    ExchangeOfferRepositoryProtocol,
    ExchangeRequestRepositoryProtocol,
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
        model.updated_at = user.updated_at

        await self._flush_or_raise_conflict("A user with that email already exists.")
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
        model.expires_at = exchange_request.expires_at
        model.updated_at = exchange_request.updated_at

        await self._flush_or_raise_conflict("That exchange request could not be saved.")
        return model.to_domain()

    async def get(self, request_id: UUID) -> ExchangeRequest:
        model = await self.session.get(ExchangeRequestModel, request_id)
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

    async def expire_due(self, now: datetime) -> int:
        result = await self.session.execute(
            update(ExchangeRequestModel)
            .where(
                ExchangeRequestModel.status.in_(self._board_visible_statuses()),
                ExchangeRequestModel.expires_at <= now,
            )
            .values(status=ExchangeRequestStatus.EXPIRED, updated_at=now)
        )
        return self._rowcount(result)

    async def reopen_pending_without_active_offers(self, now: datetime) -> int:
        result = await self.session.execute(
            update(ExchangeRequestModel)
            .where(
                ExchangeRequestModel.status == ExchangeRequestStatus.OFFER_PENDING,
                ~ExchangeRequestModel.offers.any(
                    ExchangeOfferModel.status == ExchangeOfferStatus.ACTIVE
                ),
            )
            .values(status=ExchangeRequestStatus.REQUEST_OPEN, updated_at=now)
        )
        return self._rowcount(result)


class SqlAlchemyExchangeOfferRepository(SqlAlchemyRepository, ExchangeOfferRepositoryProtocol):
    """Exchange offer repository implementation."""

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
        model.expires_at = exchange_offer.expires_at
        model.updated_at = exchange_offer.updated_at

        await self._flush_or_raise_conflict("That exchange offer could not be saved.")
        return model.to_domain()

    async def get(self, offer_id: UUID) -> ExchangeOffer:
        model = await self.session.get(ExchangeOfferModel, offer_id)
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
            .where(ExchangeOfferModel.request_id == request_id)
            .order_by(ExchangeOfferModel.created_at.desc())
        )
        result = await self.session.execute(statement)
        return [model.to_details() for model in result.scalars().all()]

    async def has_active_offer_for_request(self, request_id: UUID, user_id: UUID) -> bool:
        statement: Select[tuple[ExchangeOfferModel]] = select(ExchangeOfferModel).where(
            ExchangeOfferModel.request_id == request_id,
            ExchangeOfferModel.offer_user_id == user_id,
            ExchangeOfferModel.status == ExchangeOfferStatus.ACTIVE,
            ExchangeOfferModel.expires_at > SqlAlchemyExchangeRequestRepository._utc_now(),
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def expire_due(self, now: datetime) -> int:
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
        )
        return self._rowcount(result)


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

    async def cancel_due_unfunded(self, now: datetime) -> int:
        result = await self.session.execute(
            update(TradeContractModel)
            .where(
                TradeContractModel.status == TradeContractStatus.TERMS_LOCKED,
                TradeContractModel.funding_deadline_at <= now,
            )
            .values(status=TradeContractStatus.CANCELLED, updated_at=now)
        )
        return self._rowcount(result)
