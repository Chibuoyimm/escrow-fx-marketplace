"""Admin inspection routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import require_roles
from app.domain.enums import (
    ExchangeOfferStatus,
    ExchangeRequestStatus,
    OutboxEventStatus,
    TradeContractStatus,
    UserRole,
    UserStatus,
)
from app.schemas.auth import CurrentUserResponse
from app.schemas.exchange_offer import ExchangeOfferResponse
from app.schemas.exchange_request import ExchangeRequestResponse
from app.schemas.outbox import OutboxEventResponse
from app.schemas.trade import TradeContractResponse
from app.services.admin import AdminService, get_admin_service

admin_router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONS))],
)
admin_service_dependency = Depends(get_admin_service)
user_status_query = Query(default=None)
exchange_request_status_query = Query(default=None)
exchange_offer_status_query = Query(default=None)
trade_contract_status_query = Query(default=None)
outbox_event_status_query = Query(default=None)
outbox_event_type_query = Query(default=None)


@admin_router.get("/users", response_model=list[CurrentUserResponse])
async def list_users(
    status: UserStatus | None = user_status_query,
    admin_service: AdminService = admin_service_dependency,
) -> list[CurrentUserResponse]:
    """List users for admin inspection."""
    users = await admin_service.list_users(status)
    return [CurrentUserResponse.model_validate(user) for user in users]


@admin_router.get("/exchange-requests", response_model=list[ExchangeRequestResponse])
async def list_exchange_requests(
    status: ExchangeRequestStatus | None = exchange_request_status_query,
    admin_service: AdminService = admin_service_dependency,
) -> list[ExchangeRequestResponse]:
    """List exchange requests for admin inspection."""
    exchange_requests = await admin_service.list_exchange_requests(status)
    return [
        ExchangeRequestResponse.model_validate(exchange_request)
        for exchange_request in exchange_requests
    ]


@admin_router.get("/exchange-offers", response_model=list[ExchangeOfferResponse])
async def list_exchange_offers(
    status: ExchangeOfferStatus | None = exchange_offer_status_query,
    admin_service: AdminService = admin_service_dependency,
) -> list[ExchangeOfferResponse]:
    """List exchange offers for admin inspection."""
    exchange_offers = await admin_service.list_exchange_offers(status)
    return [
        ExchangeOfferResponse.model_validate(exchange_offer) for exchange_offer in exchange_offers
    ]


@admin_router.get("/trades", response_model=list[TradeContractResponse])
async def list_trades(
    status: TradeContractStatus | None = trade_contract_status_query,
    admin_service: AdminService = admin_service_dependency,
) -> list[TradeContractResponse]:
    """List trade contracts for admin inspection."""
    trades = await admin_service.list_trades(status)
    return [TradeContractResponse.model_validate(trade) for trade in trades]


@admin_router.get("/events", response_model=list[OutboxEventResponse])
async def list_events(
    status: OutboxEventStatus | None = outbox_event_status_query,
    event_type: str | None = outbox_event_type_query,
    admin_service: AdminService = admin_service_dependency,
) -> list[OutboxEventResponse]:
    """List outbox events for admin inspection."""
    events = await admin_service.list_events(status=status, event_type=event_type)
    return [OutboxEventResponse.model_validate(event) for event in events]
