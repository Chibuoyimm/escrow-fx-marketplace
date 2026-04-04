"""Exchange request service layer."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from app.domain.entities import ExchangeRequest, ExchangeRequestDetails
from app.domain.enums import (
    CorridorStatus,
    CurrencyStatus,
    ExchangeRequestStatus,
    KycStatus,
    UserStatus,
)
from app.domain.exceptions import (
    InvariantViolationError,
    NotFoundError,
    PreconditionFailedError,
)
from app.domain.value_objects import Money, Rate
from app.infrastructure.config import settings
from app.infrastructure.database.session import AsyncSessionFactory
from app.infrastructure.database.unit_of_work import (
    AbstractUnitOfWork,
    SqlAlchemyUnitOfWork,
)

UnitOfWorkFactory = Callable[[], AbstractUnitOfWork]


def utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)


def build_uow() -> AbstractUnitOfWork:
    """Build the default unit of work."""
    return SqlAlchemyUnitOfWork(AsyncSessionFactory)


class ExchangeRequestService:
    """Application service for exchange request creation and reads."""

    def __init__(self, uow_factory: UnitOfWorkFactory | None = None) -> None:
        self._uow_factory = uow_factory or build_uow

    async def create_request(
        self,
        *,
        creator_user_id: UUID,
        from_currency_code: str,
        to_currency_code: str,
        from_amount: Decimal,
        preferred_rate: Decimal,
        min_rate: Decimal | None,
    ) -> ExchangeRequestDetails:
        """Create a new exchange request for the authenticated user."""
        normalized_from = self._normalize_currency_code(from_currency_code)
        normalized_to = self._normalize_currency_code(to_currency_code)

        if normalized_from == normalized_to:
            raise InvariantViolationError("Source and destination currencies must differ.")

        money = Money(amount=from_amount, currency_code=normalized_from)
        preferred = Rate(value=preferred_rate)
        minimum = Rate(value=min_rate) if min_rate is not None else None
        if minimum is not None and minimum.value > preferred.value:
            raise InvariantViolationError("Minimum rate cannot be greater than preferred rate.")

        async with self._uow_factory() as uow:
            user = await uow.users.get(creator_user_id)
            if user.status is not UserStatus.ACTIVE:
                raise PreconditionFailedError("Only active users can create exchange requests.")
            if user.kyc_status is not KycStatus.VERIFIED:
                raise PreconditionFailedError(
                    "Verified KYC is required to create exchange requests."
                )

            from_currency = await uow.currencies.get_by_code(normalized_from)
            to_currency = await uow.currencies.get_by_code(normalized_to)
            if from_currency.status is not CurrencyStatus.ACTIVE:
                raise NotFoundError(f"Currency '{normalized_from}' was not found.")
            if to_currency.status is not CurrencyStatus.ACTIVE:
                raise NotFoundError(f"Currency '{normalized_to}' was not found.")

            if money.amount < from_currency.min_amount:
                raise InvariantViolationError(
                    "Amount is below the configured minimum for that currency."
                )
            if money.amount > from_currency.max_amount:
                raise InvariantViolationError(
                    "Amount exceeds the configured maximum for that currency."
                )

            try:
                corridor = await uow.corridors.get_by_currency_pair(
                    from_currency.id, to_currency.id
                )
            except NotFoundError as exc:
                raise NotFoundError(
                    f"An active corridor for '{normalized_from}/{normalized_to}' was not found."
                ) from exc

            if corridor.status is not CorridorStatus.ACTIVE:
                raise NotFoundError(
                    f"An active corridor for '{normalized_from}/{normalized_to}' was not found."
                )

            current_time = utc_now()
            created = await uow.exchange_requests.add(
                ExchangeRequest(
                    id=uuid4(),
                    creator_user_id=user.id,
                    from_currency_id=from_currency.id,
                    to_currency_id=to_currency.id,
                    from_amount=money.amount,
                    preferred_rate=preferred.value,
                    min_rate=minimum.value if minimum is not None else None,
                    status=ExchangeRequestStatus.REQUEST_OPEN,
                    expires_at=current_time
                    + timedelta(minutes=settings.exchange_request_expiry_minutes),
                    created_at=current_time,
                    updated_at=current_time,
                )
            )
            await uow.commit()
            return await uow.exchange_requests.get_details_for_user(created.id, user.id)

    async def list_board_requests(self, viewer_user_id: UUID) -> list[ExchangeRequestDetails]:
        """List board-visible exchange requests for an authenticated viewer."""
        async with self._uow_factory() as uow:
            return await uow.exchange_requests.list_board_details(viewer_user_id)

    async def list_requests_for_user(self, user_id: UUID) -> list[ExchangeRequestDetails]:
        """List exchange requests created by the authenticated user."""
        async with self._uow_factory() as uow:
            return await uow.exchange_requests.list_details_for_user(user_id)

    async def get_visible_request(
        self,
        request_id: UUID,
        viewer_user_id: UUID,
    ) -> ExchangeRequestDetails:
        """Fetch a request visible to the authenticated viewer."""
        async with self._uow_factory() as uow:
            return await uow.exchange_requests.get_visible_details(request_id, viewer_user_id)

    @staticmethod
    def _normalize_currency_code(code: str) -> str:
        """Normalize a currency code for lookups."""
        return code.strip().upper()


def get_exchange_request_service() -> ExchangeRequestService:
    """Build the default exchange request service."""
    return ExchangeRequestService()
