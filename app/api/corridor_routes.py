"""Corridor reference-data routes."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.dependencies import get_current_principal
from app.schemas.reference_data import CorridorResponse
from app.services.reference_data import ReferenceDataService, get_reference_data_service

corridor_router = APIRouter(
    prefix="/corridors",
    tags=["corridors"],
    dependencies=[Depends(get_current_principal)],
)
reference_data_service_dependency = Depends(get_reference_data_service)


@corridor_router.get("", response_model=list[CorridorResponse])
async def list_corridors(
    reference_data_service: ReferenceDataService = reference_data_service_dependency,
) -> list[CorridorResponse]:
    """List active corridors for authenticated callers."""
    corridors = await reference_data_service.list_corridors()
    return [CorridorResponse.model_validate(corridor) for corridor in corridors]


@corridor_router.get("/{corridor_id}", response_model=CorridorResponse)
async def get_corridor(
    corridor_id: UUID,
    reference_data_service: ReferenceDataService = reference_data_service_dependency,
) -> CorridorResponse:
    """Fetch an active corridor by identifier."""
    corridor = await reference_data_service.get_corridor_by_id(corridor_id)
    return CorridorResponse.model_validate(corridor)


@corridor_router.get("/{from_currency_code}/{to_currency_code}", response_model=CorridorResponse)
async def get_corridor_by_currency_pair(
    from_currency_code: str,
    to_currency_code: str,
    reference_data_service: ReferenceDataService = reference_data_service_dependency,
) -> CorridorResponse:
    """Fetch an active corridor by ordered currency pair."""
    corridor = await reference_data_service.get_corridor_by_currency_pair(
        from_currency_code=from_currency_code,
        to_currency_code=to_currency_code,
    )
    return CorridorResponse.model_validate(corridor)
