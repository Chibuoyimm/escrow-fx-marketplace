"""Offer routes."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.dependencies import get_current_principal
from app.domain.auth import AuthenticatedPrincipal
from app.schemas.exchange_offer import ExchangeOfferResponse
from app.schemas.trade import TradeContractResponse
from app.services.exchange_offer import ExchangeOfferService, get_exchange_offer_service
from app.services.trade import TradeService, get_trade_service

offer_router = APIRouter(prefix="/offers", tags=["offers"])
current_principal_dependency = Depends(get_current_principal)
exchange_offer_service_dependency = Depends(get_exchange_offer_service)
trade_service_dependency = Depends(get_trade_service)


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
