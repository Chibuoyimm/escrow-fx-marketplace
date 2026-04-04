"""Offer routes."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.dependencies import get_current_principal
from app.domain.auth import AuthenticatedPrincipal
from app.schemas.trade import TradeContractResponse
from app.services.trade import TradeService, get_trade_service

offer_router = APIRouter(prefix="/offers", tags=["offers"])
current_principal_dependency = Depends(get_current_principal)
trade_service_dependency = Depends(get_trade_service)


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
