"""Admin inspection and review routes."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_current_principal, require_roles
from app.domain.auth import AuthenticatedPrincipal
from app.domain.enums import (
    ExchangeOfferStatus,
    ExchangeRequestStatus,
    KycVerificationStatus,
    OutboxEventStatus,
    TradeContractStatus,
    UserRole,
    UserStatus,
)
from app.schemas.auth import CurrentUserResponse
from app.schemas.exchange_offer import ExchangeOfferResponse
from app.schemas.exchange_request import ExchangeRequestResponse
from app.schemas.kyc import (
    AdminKycRejectRequest,
    AdminKycReviewNoteRequest,
    KycVerificationResponse,
)
from app.schemas.outbox import OutboxEventResponse
from app.schemas.pagination import CursorPage
from app.schemas.trade import TradeContractResponse
from app.services.admin import AdminService, get_admin_service, resolve_status_filters
from app.services.kyc import KycService, get_kyc_service

admin_router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONS))],
)
admin_service_dependency = Depends(get_admin_service)
kyc_service_dependency = Depends(get_kyc_service)
principal_dependency = Depends(get_current_principal)
user_status_query = Query(default=None)
exchange_request_status_query = Query(default=None)
exchange_offer_status_query = Query(default=None)
trade_contract_status_query = Query(default=None)
outbox_event_status_query = Query(default=None)
outbox_event_type_query = Query(default=None)
kyc_verification_status_query = Query(default=None)
admin_cursor_query = Query(default=None)
admin_page_size_query = Query(default=50, ge=1, le=100)
admin_request_statuses_query = Query(default=None)
admin_offer_statuses_query = Query(default=None)
admin_trade_statuses_query = Query(default=None)
admin_created_from_query = Query(default=None)
admin_created_to_query = Query(default=None)


@admin_router.get("/users", response_model=CursorPage[CurrentUserResponse])
async def list_users(
    status: UserStatus | None = user_status_query,
    cursor: str | None = admin_cursor_query,
    limit: int = admin_page_size_query,
    admin_service: AdminService = admin_service_dependency,
) -> CursorPage[CurrentUserResponse]:
    """List users for admin inspection."""
    users, next_cursor = await admin_service.list_users_page(
        status=status, cursor=cursor, limit=limit
    )
    return CursorPage(
        items=[CurrentUserResponse.model_validate(user) for user in users],
        next_cursor=next_cursor,
    )


@admin_router.get("/exchange-requests", response_model=CursorPage[ExchangeRequestResponse])
async def list_exchange_requests(
    status: ExchangeRequestStatus | None = exchange_request_status_query,
    statuses: list[ExchangeRequestStatus] | None = admin_request_statuses_query,
    cursor: str | None = admin_cursor_query,
    limit: int = admin_page_size_query,
    created_from: datetime | None = admin_created_from_query,
    created_to: datetime | None = admin_created_to_query,
    admin_service: AdminService = admin_service_dependency,
) -> CursorPage[ExchangeRequestResponse]:
    """List exchange requests for admin inspection."""
    filters = resolve_status_filters(status, statuses)
    items, next_cursor = await admin_service.list_exchange_requests_page(
        statuses=filters,
        cursor=cursor,
        limit=limit,
        created_from=created_from,
        created_to=created_to,
    )
    return CursorPage(
        items=[ExchangeRequestResponse.model_validate(item) for item in items],
        next_cursor=next_cursor,
    )


@admin_router.get("/exchange-offers", response_model=CursorPage[ExchangeOfferResponse])
async def list_exchange_offers(
    status: ExchangeOfferStatus | None = exchange_offer_status_query,
    statuses: list[ExchangeOfferStatus] | None = admin_offer_statuses_query,
    cursor: str | None = admin_cursor_query,
    limit: int = admin_page_size_query,
    created_from: datetime | None = admin_created_from_query,
    created_to: datetime | None = admin_created_to_query,
    admin_service: AdminService = admin_service_dependency,
) -> CursorPage[ExchangeOfferResponse]:
    """List exchange offers for admin inspection."""
    filters = resolve_status_filters(status, statuses)
    items, next_cursor = await admin_service.list_exchange_offers_page(
        statuses=filters,
        cursor=cursor,
        limit=limit,
        created_from=created_from,
        created_to=created_to,
    )
    return CursorPage(
        items=[ExchangeOfferResponse.model_validate(item) for item in items],
        next_cursor=next_cursor,
    )


@admin_router.get("/trades", response_model=CursorPage[TradeContractResponse])
async def list_trades(
    status: TradeContractStatus | None = trade_contract_status_query,
    statuses: list[TradeContractStatus] | None = admin_trade_statuses_query,
    cursor: str | None = admin_cursor_query,
    limit: int = admin_page_size_query,
    created_from: datetime | None = admin_created_from_query,
    created_to: datetime | None = admin_created_to_query,
    admin_service: AdminService = admin_service_dependency,
) -> CursorPage[TradeContractResponse]:
    """List trade contracts for admin inspection."""
    filters = resolve_status_filters(status, statuses)
    items, next_cursor = await admin_service.list_trades_page(
        statuses=filters,
        cursor=cursor,
        limit=limit,
        created_from=created_from,
        created_to=created_to,
    )
    return CursorPage(
        items=[TradeContractResponse.model_validate(item) for item in items],
        next_cursor=next_cursor,
    )


@admin_router.get("/events", response_model=CursorPage[OutboxEventResponse])
async def list_events(
    status: OutboxEventStatus | None = outbox_event_status_query,
    event_type: str | None = outbox_event_type_query,
    cursor: str | None = admin_cursor_query,
    limit: int = admin_page_size_query,
    admin_service: AdminService = admin_service_dependency,
) -> CursorPage[OutboxEventResponse]:
    """List outbox events for admin inspection."""
    events, next_cursor = await admin_service.list_events_page(
        status=status, event_type=event_type, cursor=cursor, limit=limit
    )
    return CursorPage(
        items=[OutboxEventResponse.model_validate(event) for event in events],
        next_cursor=next_cursor,
    )


@admin_router.get("/kyc", response_model=CursorPage[KycVerificationResponse])
async def list_kyc_verifications(
    status: KycVerificationStatus | None = kyc_verification_status_query,
    cursor: str | None = admin_cursor_query,
    limit: int = admin_page_size_query,
    admin_service: AdminService = admin_service_dependency,
) -> CursorPage[KycVerificationResponse]:
    """List KYC verification attempts for admin inspection."""
    verifications, next_cursor = await admin_service.list_kyc_verifications_page(
        status=status, cursor=cursor, limit=limit
    )
    return CursorPage(
        items=[
            KycVerificationResponse.model_validate(verification) for verification in verifications
        ],
        next_cursor=next_cursor,
    )


@admin_router.get("/kyc/{verification_id}", response_model=KycVerificationResponse)
async def get_kyc_verification(
    verification_id: UUID,
    admin_service: AdminService = admin_service_dependency,
) -> KycVerificationResponse:
    """Fetch a KYC verification attempt for admin inspection."""
    verification = await admin_service.get_kyc_verification(str(verification_id))
    return KycVerificationResponse.model_validate(verification)


@admin_router.post("/kyc/{verification_id}/approve", response_model=KycVerificationResponse)
async def approve_kyc_review(
    verification_id: UUID,
    principal: AuthenticatedPrincipal = principal_dependency,
    kyc_service: KycService = kyc_service_dependency,
) -> KycVerificationResponse:
    """Approve a KYC verification that requires manual review."""
    verification = await kyc_service.approve_review(
        verification_id=verification_id,
        reviewer_user_id=principal.user_id,
    )
    return KycVerificationResponse.model_validate(verification)


@admin_router.post("/kyc/{verification_id}/notes", response_model=KycVerificationResponse)
async def add_kyc_review_note(
    verification_id: UUID,
    payload: AdminKycReviewNoteRequest,
    principal: AuthenticatedPrincipal = principal_dependency,
    kyc_service: KycService = kyc_service_dependency,
) -> KycVerificationResponse:
    """Add an internal note to a KYC verification under review."""
    verification = await kyc_service.add_review_note(
        verification_id=verification_id,
        reviewer_user_id=principal.user_id,
        note=payload.note,
    )
    return KycVerificationResponse.model_validate(verification)


@admin_router.post("/kyc/{verification_id}/reject", response_model=KycVerificationResponse)
async def reject_kyc_review(
    verification_id: UUID,
    payload: AdminKycRejectRequest,
    principal: AuthenticatedPrincipal = principal_dependency,
    kyc_service: KycService = kyc_service_dependency,
) -> KycVerificationResponse:
    """Reject a KYC verification that requires manual review."""
    verification = await kyc_service.reject_review(
        verification_id=verification_id,
        reviewer_user_id=principal.user_id,
        reason=payload.reason,
    )
    return KycVerificationResponse.model_validate(verification)
