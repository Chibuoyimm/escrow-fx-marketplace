"""Webhook routes."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, Request

from app.domain.exceptions import AuthorizationError, InvariantViolationError
from app.infrastructure.config import settings
from app.integrations.youverify import YouverifyKycProvider
from app.schemas.auth import MessageResponse
from app.services.kyc import KycService, get_kyc_service

webhook_router = APIRouter(prefix="/webhooks", tags=["webhooks"])
kyc_service_dependency = Depends(get_kyc_service)


@webhook_router.post("/kyc/youverify", response_model=MessageResponse)
async def handle_youverify_kyc_webhook(
    request: Request,
    x_youverify_signature: str = Header(alias="x-youverify-signature"),
    kyc_service: KycService = kyc_service_dependency,
) -> MessageResponse:
    """Process a signed Youverify KYC webhook."""
    raw_body = await request.body()
    secret = settings.youverify_webhook_secret or settings.youverify_api_key
    if not secret:
        raise AuthorizationError("Youverify webhook verification is not configured.")
    if not YouverifyKycProvider.verify_webhook_signature(
        raw_body=raw_body,
        signature=x_youverify_signature,
        secret=secret,
    ):
        raise AuthorizationError("Invalid Youverify webhook signature.")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise InvariantViolationError("Invalid Youverify webhook payload.") from exc
    if not isinstance(payload, dict):
        raise InvariantViolationError("Invalid Youverify webhook payload.")

    await kyc_service.process_youverify_webhook(payload)
    return MessageResponse(message="Youverify KYC webhook processed.")
