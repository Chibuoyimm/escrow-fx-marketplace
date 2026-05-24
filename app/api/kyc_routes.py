"""KYC routes."""

from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_current_principal
from app.domain.auth import AuthenticatedPrincipal
from app.schemas.kyc import KycSubmitRequest, KycVerificationResponse
from app.services.kyc import KycService, get_kyc_service

kyc_router = APIRouter(prefix="/kyc", tags=["kyc"])
kyc_service_dependency = Depends(get_kyc_service)
current_principal_dependency = Depends(get_current_principal)


@kyc_router.post(
    "/submit",
    response_model=KycVerificationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_kyc(
    payload: KycSubmitRequest,
    principal: AuthenticatedPrincipal = current_principal_dependency,
    kyc_service: KycService = kyc_service_dependency,
) -> KycVerificationResponse:
    """Submit an identity check for the authenticated user."""
    verification = await kyc_service.submit_identity_check(
        user_id=principal.user_id,
        id_type=payload.id_type,
        id_number=payload.id_number,
        first_name=payload.first_name,
        last_name=payload.last_name,
        date_of_birth=payload.date_of_birth,
        subject_consent=payload.subject_consent,
    )
    return KycVerificationResponse.model_validate(verification)


@kyc_router.get("/status", response_model=KycVerificationResponse)
async def get_kyc_status(
    principal: AuthenticatedPrincipal = current_principal_dependency,
    kyc_service: KycService = kyc_service_dependency,
) -> KycVerificationResponse:
    """Fetch the authenticated user's latest KYC verification."""
    verification = await kyc_service.get_status(principal.user_id)
    return KycVerificationResponse.model_validate(verification)
