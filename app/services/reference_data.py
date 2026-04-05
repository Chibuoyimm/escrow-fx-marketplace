"""Reference-data read service layer."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from app.domain.entities import CorridorDetails, Currency
from app.domain.enums import CurrencyStatus
from app.domain.exceptions import NotFoundError
from app.services._shared import UnitOfWorkFactory, build_uow


@dataclass(frozen=True, slots=True)
class CurrencyView:
    """Public currency response view."""

    code: str
    minor_unit: int
    status: CurrencyStatus
    min_amount: Decimal
    max_amount: Decimal


class ReferenceDataService:
    """Application service for public and authenticated reference-data reads."""

    def __init__(self, uow_factory: UnitOfWorkFactory | None = None) -> None:
        self._uow_factory = uow_factory or build_uow

    async def list_currencies(self) -> list[CurrencyView]:
        """List active currencies."""
        async with self._uow_factory() as uow:
            currencies = await uow.currencies.list_active()
        return [self._currency_view(currency) for currency in currencies]

    async def get_currency_by_code(self, code: str) -> CurrencyView:
        """Fetch an active currency by code."""
        async with self._uow_factory() as uow:
            currency = await uow.currencies.get_by_code(self._normalize_currency_code(code))
        if currency.status is not CurrencyStatus.ACTIVE:
            raise NotFoundError(f"Currency '{code.upper()}' was not found.")
        return self._currency_view(currency)

    async def list_corridors(self) -> list[CorridorDetails]:
        """List active corridors."""
        async with self._uow_factory() as uow:
            return await uow.corridors.list_active_details()

    async def get_corridor_by_id(self, corridor_id: UUID) -> CorridorDetails:
        """Fetch an active corridor by identifier."""
        async with self._uow_factory() as uow:
            return await uow.corridors.get_active_details(corridor_id)

    async def get_corridor_by_currency_pair(
        self,
        from_currency_code: str,
        to_currency_code: str,
    ) -> CorridorDetails:
        """Fetch an active corridor by ordered currency pair."""
        normalized_from = self._normalize_currency_code(from_currency_code)
        normalized_to = self._normalize_currency_code(to_currency_code)

        async with self._uow_factory() as uow:
            return await uow.corridors.get_active_details_by_currency_pair(
                normalized_from,
                normalized_to,
            )

    def _normalize_currency_code(self, code: str) -> str:
        """Normalize a currency code for lookups."""
        return code.strip().upper()

    @staticmethod
    def _currency_view(currency: Currency) -> CurrencyView:
        return CurrencyView(
            code=currency.code,
            minor_unit=currency.minor_unit,
            status=currency.status,
            min_amount=currency.min_amount,
            max_amount=currency.max_amount,
        )


def get_reference_data_service() -> ReferenceDataService:
    """Build the default reference-data service."""
    return ReferenceDataService()
