"""SQLAlchemy-backed repository implementations."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities import Corridor, CorridorRail, Currency, User
from app.domain.enums import CorridorStatus, CurrencyStatus, RailStatus
from app.domain.exceptions import ConflictError, NotFoundError
from app.infrastructure.exceptions import InfrastructureError
from app.models.corridor import CorridorModel, CorridorRailModel
from app.models.currency import CurrencyModel
from app.models.user import UserModel
from app.repositories.protocols import (
    CorridorRailRepositoryProtocol,
    CorridorRepositoryProtocol,
    CurrencyRepositoryProtocol,
    UserRepositoryProtocol,
)


class SqlAlchemyRepository:
    """Base repository with SQLAlchemy helpers."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

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
        statement: Select[tuple[CurrencyModel]] = select(CurrencyModel).where(CurrencyModel.code == code)
        result = await self.session.execute(statement)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError(f"Currency '{code}' was not found.")
        return model.to_domain()

    async def list_active(self) -> list[Currency]:
        statement: Select[tuple[CurrencyModel]] = select(CurrencyModel).where(
            CurrencyModel.status == CurrencyStatus.ACTIVE
        )
        result = await self.session.execute(statement)
        return [model.to_domain() for model in result.scalars().all()]


class SqlAlchemyCorridorRepository(SqlAlchemyRepository, CorridorRepositoryProtocol):
    """Corridor repository implementation."""

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

    async def list_active(self) -> list[Corridor]:
        statement: Select[tuple[CorridorModel]] = select(CorridorModel).where(
            CorridorModel.status == CorridorStatus.ACTIVE
        )
        result = await self.session.execute(statement)
        return [model.to_domain() for model in result.scalars().all()]


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
