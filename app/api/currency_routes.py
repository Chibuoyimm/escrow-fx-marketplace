"""Currency reference-data routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.schemas.reference_data import CurrencyResponse
from app.services.reference_data import ReferenceDataService, get_reference_data_service

currency_router = APIRouter(prefix="/currencies", tags=["currencies"])
reference_data_service_dependency = Depends(get_reference_data_service)


@currency_router.get("", response_model=list[CurrencyResponse])
async def list_currencies(
    reference_data_service: ReferenceDataService = reference_data_service_dependency,
) -> list[CurrencyResponse]:
    """List active currencies."""
    currencies = await reference_data_service.list_currencies()
    return [CurrencyResponse.model_validate(currency) for currency in currencies]


@currency_router.get("/{code}", response_model=CurrencyResponse)
async def get_currency(
    code: str,
    reference_data_service: ReferenceDataService = reference_data_service_dependency,
) -> CurrencyResponse:
    """Fetch an active currency by code."""
    currency = await reference_data_service.get_currency_by_code(code)
    return CurrencyResponse.model_validate(currency)
