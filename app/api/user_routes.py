"""User routes."""

from fastapi import APIRouter, Depends

from app.api.dependencies import get_current_principal
from app.domain.auth import AuthenticatedPrincipal
from app.schemas.auth import CurrentUserResponse
from app.services.auth import AuthService, get_auth_service

users_router = APIRouter(prefix="/users", tags=["users"])
current_principal_dependency = Depends(get_current_principal)
auth_service_dependency = Depends(get_auth_service)


@users_router.get("/me", response_model=CurrentUserResponse)
async def get_current_user(
    principal: AuthenticatedPrincipal = current_principal_dependency,
    auth_service: AuthService = auth_service_dependency,
) -> CurrentUserResponse:
    """Return the authenticated user's profile."""
    user = await auth_service.get_user_by_id(principal.user_id)
    return CurrentUserResponse.model_validate(user)
