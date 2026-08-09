"""User routes."""

from fastapi import APIRouter, Depends

from app.api.dependencies import get_current_principal
from app.api.rate_limiting import authenticated_rate_limit_dependency
from app.domain.auth import AuthenticatedPrincipal
from app.domain.exceptions import InvariantViolationError
from app.infrastructure.rate_limiting import ACCOUNT_DEACTIVATE, ACCOUNT_MUTATION
from app.schemas.auth import CurrentUserResponse
from app.schemas.notification_preferences import (
    NotificationPreferencesPatch,
    NotificationPreferencesResponse,
)
from app.schemas.user import DeactivateAccountRequest, UpdateProfileRequest
from app.services.account import AccountService, get_account_service
from app.services.auth import AuthService, get_auth_service
from app.services.notification_preferences import (
    NotificationPreferenceService,
    get_notification_preference_service,
)

users_router = APIRouter(prefix="/users", tags=["users"])
current_principal_dependency = Depends(get_current_principal)
auth_service_dependency = Depends(get_auth_service)
account_service_dependency = Depends(get_account_service)
notification_preference_service_dependency = Depends(get_notification_preference_service)


@users_router.get("/me", response_model=CurrentUserResponse)
async def get_current_user(
    principal: AuthenticatedPrincipal = current_principal_dependency,
    auth_service: AuthService = auth_service_dependency,
) -> CurrentUserResponse:
    """Return the authenticated user's profile."""
    user = await auth_service.get_user_by_id(principal.user_id)
    return CurrentUserResponse.model_validate(user)


@users_router.patch(
    "/me",
    response_model=CurrentUserResponse,
    dependencies=[Depends(authenticated_rate_limit_dependency(ACCOUNT_MUTATION))],
)
async def update_current_user(
    payload: UpdateProfileRequest,
    principal: AuthenticatedPrincipal = current_principal_dependency,
    account_service: AccountService = account_service_dependency,
) -> CurrentUserResponse:
    """Update the authenticated user's mutable profile fields."""
    if not payload.model_fields_set:
        raise InvariantViolationError("At least one profile field must be provided.")
    user = await account_service.update_profile(user_id=principal.user_id, phone=payload.phone)
    return CurrentUserResponse.model_validate(user)


@users_router.post(
    "/me/deactivate",
    response_model=CurrentUserResponse,
    dependencies=[Depends(authenticated_rate_limit_dependency(ACCOUNT_DEACTIVATE))],
)
async def deactivate_current_user(
    payload: DeactivateAccountRequest,
    principal: AuthenticatedPrincipal = current_principal_dependency,
    account_service: AccountService = account_service_dependency,
) -> CurrentUserResponse:
    """Soft-deactivate the authenticated user's account."""
    user = await account_service.deactivate(
        user_id=principal.user_id,
        current_password=payload.current_password,
    )
    return CurrentUserResponse.model_validate(user)


@users_router.get(
    "/me/notification-preferences",
    response_model=NotificationPreferencesResponse,
)
async def get_notification_preferences(
    principal: AuthenticatedPrincipal = current_principal_dependency,
    preference_service: NotificationPreferenceService = notification_preference_service_dependency,
) -> NotificationPreferencesResponse:
    """Return the current user's effective notification preferences."""
    return await preference_service.get(user_id=principal.user_id)


@users_router.patch(
    "/me/notification-preferences",
    response_model=NotificationPreferencesResponse,
    dependencies=[Depends(authenticated_rate_limit_dependency(ACCOUNT_MUTATION))],
)
async def update_notification_preferences(
    payload: NotificationPreferencesPatch,
    principal: AuthenticatedPrincipal = current_principal_dependency,
    preference_service: NotificationPreferenceService = notification_preference_service_dependency,
) -> NotificationPreferencesResponse:
    """Merge permitted notification preference changes for the current user."""
    return await preference_service.update(user_id=principal.user_id, payload=payload)
