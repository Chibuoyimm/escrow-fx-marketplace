"""Exchange request routes."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_current_principal
from app.domain.auth import AuthenticatedPrincipal
from app.schemas.exchange_request import CreateExchangeRequestRequest, ExchangeRequestResponse
from app.services.exchange_request import ExchangeRequestService, get_exchange_request_service

exchange_request_router = APIRouter(prefix="/exchange-requests", tags=["exchange-requests"])
current_principal_dependency = Depends(get_current_principal)
exchange_request_service_dependency = Depends(get_exchange_request_service)


@exchange_request_router.post(
    "",
    response_model=ExchangeRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_exchange_request(
    payload: CreateExchangeRequestRequest,
    principal: AuthenticatedPrincipal = current_principal_dependency,
    exchange_request_service: ExchangeRequestService = exchange_request_service_dependency,
) -> ExchangeRequestResponse:
    """Create an exchange request for the authenticated user."""
    exchange_request = await exchange_request_service.create_request(
        creator_user_id=principal.user_id,
        from_currency_code=payload.from_currency_code,
        to_currency_code=payload.to_currency_code,
        from_amount=payload.from_amount,
        preferred_rate=payload.preferred_rate,
        min_rate=payload.min_rate,
    )
    return ExchangeRequestResponse.model_validate(exchange_request)


@exchange_request_router.get("", response_model=list[ExchangeRequestResponse])
async def list_exchange_requests(
    principal: AuthenticatedPrincipal = current_principal_dependency,
    exchange_request_service: ExchangeRequestService = exchange_request_service_dependency,
) -> list[ExchangeRequestResponse]:
    """List exchange requests for the authenticated user."""
    exchange_requests = await exchange_request_service.list_requests_for_user(principal.user_id)
    return [
        ExchangeRequestResponse.model_validate(exchange_request)
        for exchange_request in exchange_requests
    ]


@exchange_request_router.get("/{request_id}", response_model=ExchangeRequestResponse)
async def get_exchange_request(
    request_id: UUID,
    principal: AuthenticatedPrincipal = current_principal_dependency,
    exchange_request_service: ExchangeRequestService = exchange_request_service_dependency,
) -> ExchangeRequestResponse:
    """Fetch an exchange request for the authenticated user."""
    exchange_request = await exchange_request_service.get_request_for_user(
        request_id, principal.user_id
    )
    return ExchangeRequestResponse.model_validate(exchange_request)
