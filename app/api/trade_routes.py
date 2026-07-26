"""Trade routes."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_current_principal
from app.domain.auth import AuthenticatedPrincipal
from app.domain.enums import TradeContractStatus
from app.schemas.pagination import CursorPage
from app.schemas.trade import TradeContractResponse
from app.services.trade import TradeService, get_trade_service

trade_router = APIRouter(prefix="/trades", tags=["trades"])
current_principal_dependency = Depends(get_current_principal)
trade_service_dependency = Depends(get_trade_service)
cursor_query = Query(default=None)
page_size_query = Query(default=50, ge=1, le=100)
trade_statuses_query = Query(default=None)
created_from_query = Query(default=None)
created_to_query = Query(default=None)


@trade_router.get("", response_model=CursorPage[TradeContractResponse])
async def list_trades(
    cursor: str | None = cursor_query,
    limit: int = page_size_query,
    statuses: list[TradeContractStatus] | None = trade_statuses_query,
    created_from: datetime | None = created_from_query,
    created_to: datetime | None = created_to_query,
    principal: AuthenticatedPrincipal = current_principal_dependency,
    trade_service: TradeService = trade_service_dependency,
) -> CursorPage[TradeContractResponse]:
    """List trade contracts for the authenticated participant."""
    trades, next_cursor = await trade_service.list_trades_for_participant_page(
        principal.user_id,
        cursor=cursor,
        limit=limit,
        statuses=tuple(statuses) if statuses else None,
        created_from=created_from,
        created_to=created_to,
    )
    return CursorPage(
        items=[TradeContractResponse.model_validate(trade) for trade in trades],
        next_cursor=next_cursor,
    )


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
