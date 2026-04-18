"""Trade routes."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.dependencies import get_current_principal
from app.domain.auth import AuthenticatedPrincipal
from app.schemas.trade import TradeContractResponse
from app.services.trade import TradeService, get_trade_service

trade_router = APIRouter(prefix="/trades", tags=["trades"])
current_principal_dependency = Depends(get_current_principal)
trade_service_dependency = Depends(get_trade_service)


@trade_router.get("", response_model=list[TradeContractResponse])
async def list_trades(
    principal: AuthenticatedPrincipal = current_principal_dependency,
    trade_service: TradeService = trade_service_dependency,
) -> list[TradeContractResponse]:
    """List trade contracts for the authenticated participant."""
    trades = await trade_service.list_trades_for_participant(principal.user_id)
    return [TradeContractResponse.model_validate(trade) for trade in trades]


@trade_router.get("/{trade_id}", response_model=TradeContractResponse)
async def get_trade(
    trade_id: UUID,
    principal: AuthenticatedPrincipal = current_principal_dependency,
    trade_service: TradeService = trade_service_dependency,
) -> TradeContractResponse:
    """Fetch a trade contract for a participant."""
    trade = await trade_service.get_trade_for_participant(
        trade_id,
        principal.user_id,
    )
    return TradeContractResponse.model_validate(trade)
