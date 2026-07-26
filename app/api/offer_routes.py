"""Offer routes."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_current_principal
from app.domain.auth import AuthenticatedPrincipal
from app.domain.enums import ExchangeOfferStatus
from app.schemas.exchange_offer import ExchangeOfferResponse, UpdateExchangeOfferRequest
from app.schemas.pagination import CursorPage
from app.schemas.trade import TradeContractResponse
from app.services.exchange_offer import ExchangeOfferService, get_exchange_offer_service
from app.services.trade import TradeService, get_trade_service

offer_router = APIRouter(prefix="/offers", tags=["offers"])
current_principal_dependency = Depends(get_current_principal)
exchange_offer_service_dependency = Depends(get_exchange_offer_service)
trade_service_dependency = Depends(get_trade_service)
cursor_query = Query(default=None)
page_size_query = Query(default=50, ge=1, le=100)
offer_statuses_query = Query(default=None)
min_offer_rate_query = Query(default=None, gt=0)
max_offer_rate_query = Query(default=None, gt=0)
created_from_query = Query(default=None)
created_to_query = Query(default=None)


@offer_router.post("/{offer_id}/withdraw", response_model=ExchangeOfferResponse)
async def withdraw_exchange_offer(
    offer_id: UUID,
    principal: AuthenticatedPrincipal = current_principal_dependency,
    exchange_offer_service: ExchangeOfferService = exchange_offer_service_dependency,
) -> ExchangeOfferResponse:
    """Withdraw an active offer owned by the authenticated user."""
    exchange_offer = await exchange_offer_service.withdraw_offer(
        offer_id=offer_id,
        offer_user_id=principal.user_id,
    )
    return ExchangeOfferResponse.model_validate(exchange_offer)


@offer_router.patch("/{offer_id}", response_model=ExchangeOfferResponse)
async def update_exchange_offer(
    offer_id: UUID,
    payload: UpdateExchangeOfferRequest,
    principal: AuthenticatedPrincipal = current_principal_dependency,
    exchange_offer_service: ExchangeOfferService = exchange_offer_service_dependency,
) -> ExchangeOfferResponse:
    """Update the offered rate while the parent request remains editable."""
    offer = await exchange_offer_service.update_offer(
        offer_id=offer_id,
        offer_user_id=principal.user_id,
        offered_rate=payload.offered_rate,
    )
    return ExchangeOfferResponse.model_validate(offer)


@offer_router.get("/mine", response_model=CursorPage[ExchangeOfferResponse])
async def list_my_exchange_offers(
    cursor: str | None = cursor_query,
    limit: int = page_size_query,
    statuses: list[ExchangeOfferStatus] | None = offer_statuses_query,
    min_offered_rate: Decimal | None = min_offer_rate_query,
    max_offered_rate: Decimal | None = max_offer_rate_query,
    created_from: datetime | None = created_from_query,
    created_to: datetime | None = created_to_query,
    principal: AuthenticatedPrincipal = current_principal_dependency,
    exchange_offer_service: ExchangeOfferService = exchange_offer_service_dependency,
) -> CursorPage[ExchangeOfferResponse]:
    """List offers owned by the authenticated user."""
    items, next_cursor = await exchange_offer_service.list_my_offers_page(
        offer_user_id=principal.user_id,
        cursor=cursor,
        limit=limit,
        statuses=tuple(statuses) if statuses else None,
        min_offered_rate=min_offered_rate,
        max_offered_rate=max_offered_rate,
        created_from=created_from,
        created_to=created_to,
    )
    return CursorPage(
        items=[ExchangeOfferResponse.model_validate(item) for item in items],
        next_cursor=next_cursor,
    )


@offer_router.get("/{offer_id}", response_model=ExchangeOfferResponse)
async def get_exchange_offer(
    offer_id: UUID,
    principal: AuthenticatedPrincipal = current_principal_dependency,
    exchange_offer_service: ExchangeOfferService = exchange_offer_service_dependency,
) -> ExchangeOfferResponse:
    """Fetch an offer for its owner or the request creator."""
    offer = await exchange_offer_service.get_visible_offer(
        offer_id=offer_id,
        viewer_user_id=principal.user_id,
    )
    return ExchangeOfferResponse.model_validate(offer)


@offer_router.post("/{offer_id}/reject", response_model=ExchangeOfferResponse)
async def reject_exchange_offer(
    offer_id: UUID,
    principal: AuthenticatedPrincipal = current_principal_dependency,
    exchange_offer_service: ExchangeOfferService = exchange_offer_service_dependency,
) -> ExchangeOfferResponse:
    """Reject an active offer as the request creator."""
    exchange_offer = await exchange_offer_service.reject_offer(
        offer_id=offer_id,
        requester_user_id=principal.user_id,
    )
    return ExchangeOfferResponse.model_validate(exchange_offer)


@offer_router.post("/{offer_id}/accept", response_model=TradeContractResponse)
async def accept_exchange_offer(
    offer_id: UUID,
    principal: AuthenticatedPrincipal = current_principal_dependency,
    trade_service: TradeService = trade_service_dependency,
) -> TradeContractResponse:
    """Accept an exchange offer and lock the initial trade."""
    trade = await trade_service.accept_offer(
        offer_id=offer_id,
        requester_user_id=principal.user_id,
    )
    return TradeContractResponse.model_validate(trade)
