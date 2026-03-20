"""Authentication routes."""

from fastapi import APIRouter, Depends, status

from app.schemas.auth import (
    AccessTokenResponse,
    LoginRequest,
    RegisterUserRequest,
    RegisterUserResponse,
)
from app.services.auth import AuthService, get_auth_service

auth_router = APIRouter(prefix="/auth", tags=["auth"])
auth_service_dependency = Depends(get_auth_service)


@auth_router.post(
    "/register",
    response_model=RegisterUserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_user(
    payload: RegisterUserRequest,
    auth_service: AuthService = auth_service_dependency,
) -> RegisterUserResponse:
    """Register a user account."""
    user = await auth_service.register_user(
        email=str(payload.email),
        password=payload.password,
        country=payload.country,
        phone=payload.phone,
    )
    return RegisterUserResponse.model_validate(user)


@auth_router.post("/login", response_model=AccessTokenResponse)
async def login_user(
    payload: LoginRequest,
    auth_service: AuthService = auth_service_dependency,
) -> AccessTokenResponse:
    """Authenticate a user and issue a bearer token."""
    return await auth_service.login_user(email=str(payload.email), password=payload.password)
