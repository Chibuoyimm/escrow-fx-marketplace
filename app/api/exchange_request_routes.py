"""Exchange request routes."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from starlette.responses import Response

from app.api.dependencies import get_current_principal
from app.api.idempotency import replay_response
from app.domain.auth import AuthenticatedPrincipal
from app.domain.enums import ExchangeRequestStatus
from app.infrastructure.idempotency import IdempotencyReplay, build_idempotency_request
from app.schemas.exchange_offer import CreateExchangeOfferRequest, ExchangeOfferResponse
from app.schemas.exchange_request import (
    CreateExchangeRequestRequest,
    ExchangeRequestResponse,
    RelistExchangeRequestRequest,
    UpdateExchangeRequestRequest,
)
from app.schemas.pagination import CursorPage
from app.services.exchange_offer import ExchangeOfferService, get_exchange_offer_service
from app.services.exchange_request import ExchangeRequestService, get_exchange_request_service

exchange_request_router = APIRouter(prefix="/exchange-requests", tags=["exchange-requests"])
current_principal_dependency = Depends(get_current_principal)
exchange_request_service_dependency = Depends(get_exchange_request_service)
exchange_offer_service_dependency = Depends(get_exchange_offer_service)
cursor_query = Query(default=None)
page_size_query = Query(default=50, ge=1, le=100)
request_statuses_query = Query(default=None)
from_currency_query = Query(default=None, min_length=3, max_length=3)
to_currency_query = Query(default=None, min_length=3, max_length=3)
min_amount_query = Query(default=None, gt=0)
max_amount_query = Query(default=None, gt=0)
min_rate_query = Query(default=None, gt=0)
max_rate_query = Query(default=None, gt=0)
created_from_query = Query(default=None)
created_to_query = Query(default=None)


@exchange_request_router.post(
    "",
    response_model=ExchangeRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_exchange_request(
    payload: CreateExchangeRequestRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: AuthenticatedPrincipal = current_principal_dependency,
    exchange_request_service: ExchangeRequestService = exchange_request_service_dependency,
) -> ExchangeRequestResponse | Response:
    """Create an exchange request for the authenticated user."""
    idempotency = build_idempotency_request(
        principal_user_id=principal.user_id,
        key=idempotency_key,
        operation_scope="exchange-request.create",
        payload=payload.model_dump(mode="python", exclude_unset=False),
    )
    exchange_request = await exchange_request_service.create_request(
        creator_user_id=principal.user_id,
        from_currency_code=payload.from_currency_code,
        to_currency_code=payload.to_currency_code,
        from_amount=payload.from_amount,
        preferred_rate=payload.preferred_rate,
        min_rate=payload.min_rate,
        idempotency=idempotency,
    )
    if isinstance(exchange_request, IdempotencyReplay):
        return replay_response(exchange_request)
    return ExchangeRequestResponse.model_validate(exchange_request)


@exchange_request_router.get("", response_model=CursorPage[ExchangeRequestResponse])
async def list_exchange_requests(
    cursor: str | None = cursor_query,
    limit: int = page_size_query,
    statuses: list[ExchangeRequestStatus] | None = request_statuses_query,
    from_currency_code: str | None = from_currency_query,
    to_currency_code: str | None = to_currency_query,
    min_amount: Decimal | None = min_amount_query,
    max_amount: Decimal | None = max_amount_query,
    min_preferred_rate: Decimal | None = min_rate_query,
    max_preferred_rate: Decimal | None = max_rate_query,
    created_from: datetime | None = created_from_query,
    created_to: datetime | None = created_to_query,
    principal: AuthenticatedPrincipal = current_principal_dependency,
    exchange_request_service: ExchangeRequestService = exchange_request_service_dependency,
) -> CursorPage[ExchangeRequestResponse]:
    """List board-visible exchange requests for the authenticated user."""
    items, next_cursor = await exchange_request_service.list_board_requests_page(
        principal.user_id,
        cursor=cursor,
        limit=limit,
        statuses=tuple(statuses) if statuses else None,
        from_currency_code=from_currency_code.upper() if from_currency_code else None,
        to_currency_code=to_currency_code.upper() if to_currency_code else None,
        min_amount=min_amount,
        max_amount=max_amount,
        min_preferred_rate=min_preferred_rate,
        max_preferred_rate=max_preferred_rate,
        created_from=created_from,
        created_to=created_to,
    )
    return CursorPage(
        items=[ExchangeRequestResponse.model_validate(item) for item in items],
        next_cursor=next_cursor,
    )


@exchange_request_router.get("/mine", response_model=CursorPage[ExchangeRequestResponse])
async def list_my_exchange_requests(
    cursor: str | None = cursor_query,
    limit: int = page_size_query,
    statuses: list[ExchangeRequestStatus] | None = request_statuses_query,
    created_from: datetime | None = created_from_query,
    created_to: datetime | None = created_to_query,
    principal: AuthenticatedPrincipal = current_principal_dependency,
    exchange_request_service: ExchangeRequestService = exchange_request_service_dependency,
) -> CursorPage[ExchangeRequestResponse]:
    """List exchange requests created by the authenticated user."""
    items, next_cursor = await exchange_request_service.list_requests_for_user_page(
        principal.user_id,
        cursor=cursor,
        limit=limit,
        statuses=tuple(statuses) if statuses else None,
        created_from=created_from,
        created_to=created_to,
    )
    return CursorPage(
        items=[ExchangeRequestResponse.model_validate(item) for item in items],
        next_cursor=next_cursor,
    )


@exchange_request_router.patch("/{request_id}", response_model=ExchangeRequestResponse)
async def update_exchange_request(
    request_id: UUID,
    payload: UpdateExchangeRequestRequest,
    principal: AuthenticatedPrincipal = current_principal_dependency,
    exchange_request_service: ExchangeRequestService = exchange_request_service_dependency,
) -> ExchangeRequestResponse:
    """Update request terms before any offer has been submitted."""
    exchange_request = await exchange_request_service.update_request(
        request_id=request_id,
        requester_user_id=principal.user_id,
        fields=payload.model_fields_set,
        from_amount=payload.from_amount,
        preferred_rate=payload.preferred_rate,
        min_rate=payload.min_rate,
    )
    return ExchangeRequestResponse.model_validate(exchange_request)


@exchange_request_router.post("/{request_id}/cancel", response_model=ExchangeRequestResponse)
async def cancel_exchange_request(
    request_id: UUID,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: AuthenticatedPrincipal = current_principal_dependency,
    exchange_request_service: ExchangeRequestService = exchange_request_service_dependency,
) -> ExchangeRequestResponse | Response:
    """Cancel an open or pending request owned by the authenticated user."""
    idempotency = build_idempotency_request(
        principal_user_id=principal.user_id,
        key=idempotency_key,
        operation_scope=f"exchange-request.cancel:{request_id}",
        payload={},
    )
    exchange_request = await exchange_request_service.cancel_request(
        request_id=request_id,
        requester_user_id=principal.user_id,
        idempotency=idempotency,
    )
    if isinstance(exchange_request, IdempotencyReplay):
        return replay_response(exchange_request)
    return ExchangeRequestResponse.model_validate(exchange_request)


@exchange_request_router.post(
    "/{request_id}/relist",
    response_model=ExchangeRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def relist_exchange_request(
    request_id: UUID,
    payload: RelistExchangeRequestRequest | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: AuthenticatedPrincipal = current_principal_dependency,
    exchange_request_service: ExchangeRequestService = exchange_request_service_dependency,
) -> ExchangeRequestResponse | Response:
    """Create a fresh open request from an expired or cancelled request."""
    payload = payload or RelistExchangeRequestRequest()
    idempotency = build_idempotency_request(
        principal_user_id=principal.user_id,
        key=idempotency_key,
        operation_scope=f"exchange-request.relist:{request_id}",
        payload=payload.model_dump(mode="python", exclude_unset=True),
    )
    exchange_request = await exchange_request_service.relist_request(
        request_id=request_id,
        requester_user_id=principal.user_id,
        fields=payload.model_fields_set,
        from_amount=payload.from_amount,
        preferred_rate=payload.preferred_rate,
        min_rate=payload.min_rate,
        idempotency=idempotency,
    )
    if isinstance(exchange_request, IdempotencyReplay):
        return replay_response(exchange_request)
    return ExchangeRequestResponse.model_validate(exchange_request)


@exchange_request_router.post(
    "/{request_id}/offers",
    response_model=ExchangeOfferResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_exchange_offer(
    request_id: UUID,
    payload: CreateExchangeOfferRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: AuthenticatedPrincipal = current_principal_dependency,
    exchange_offer_service: ExchangeOfferService = exchange_offer_service_dependency,
) -> ExchangeOfferResponse | Response:
    """Create a counterparty offer on a board-visible exchange request."""
    idempotency = build_idempotency_request(
        principal_user_id=principal.user_id,
        key=idempotency_key,
        operation_scope=f"exchange-offer.create:{request_id}",
        payload=payload.model_dump(mode="python", exclude_unset=False),
    )
    exchange_offer = await exchange_offer_service.create_offer(
        request_id=request_id,
        offer_user_id=principal.user_id,
        offered_rate=payload.offered_rate,
        idempotency=idempotency,
    )
    if isinstance(exchange_offer, IdempotencyReplay):
        return replay_response(exchange_offer)
    return ExchangeOfferResponse.model_validate(exchange_offer)


@exchange_request_router.get(
    "/{request_id}/offers", response_model=CursorPage[ExchangeOfferResponse]
)
async def list_exchange_request_offers(
    request_id: UUID,
    cursor: str | None = cursor_query,
    limit: int = page_size_query,
    created_from: datetime | None = created_from_query,
    created_to: datetime | None = created_to_query,
    principal: AuthenticatedPrincipal = current_principal_dependency,
    exchange_offer_service: ExchangeOfferService = exchange_offer_service_dependency,
) -> CursorPage[ExchangeOfferResponse]:
    """List offers attached to a request for the request creator."""
    exchange_offers, next_cursor = await exchange_offer_service.list_offers_for_request_page(
        request_id=request_id,
        requester_user_id=principal.user_id,
        cursor=cursor,
        limit=limit,
        created_from=created_from,
        created_to=created_to,
    )
    return CursorPage(
        items=[ExchangeOfferResponse.model_validate(item) for item in exchange_offers],
        next_cursor=next_cursor,
    )


@exchange_request_router.get("/{request_id}", response_model=ExchangeRequestResponse)
async def get_exchange_request(
    request_id: UUID,
    principal: AuthenticatedPrincipal = current_principal_dependency,
    exchange_request_service: ExchangeRequestService = exchange_request_service_dependency,
) -> ExchangeRequestResponse:
    """Fetch an exchange request visible to the authenticated user."""
    exchange_request = await exchange_request_service.get_visible_request(
        request_id,
        principal.user_id,
    )
    return ExchangeRequestResponse.model_validate(exchange_request)
