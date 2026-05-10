"""Authentication routes."""

from fastapi import APIRouter, Depends, status

from app.schemas.auth import (
    AccessTokenResponse,
    CurrentUserResponse,
    EmailVerificationResponse,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    RegisterUserRequest,
    RegisterUserResponse,
    ResendEmailVerificationRequest,
    ResetPasswordRequest,
    VerifyEmailRequest,
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


@auth_router.post("/verify-email", response_model=EmailVerificationResponse)
async def verify_email(
    payload: VerifyEmailRequest,
    auth_service: AuthService = auth_service_dependency,
) -> EmailVerificationResponse:
    """Verify a user's email address from a request body token."""
    user = await auth_service.verify_email(payload.token)
    token = auth_service.issue_access_token(user)
    return EmailVerificationResponse(
        access_token=token.access_token,
        token_type=token.token_type,
        expires_in_seconds=token.expires_in_seconds,
        user=CurrentUserResponse.model_validate(user),
    )


@auth_router.post(
    "/resend-verification",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def resend_email_verification(
    payload: ResendEmailVerificationRequest,
    auth_service: AuthService = auth_service_dependency,
) -> MessageResponse:
    """Queue another verification email if the account still needs one."""
    await auth_service.resend_email_verification(email=str(payload.email))
    return MessageResponse(message="If that account needs verification, a new email was queued.")


@auth_router.post(
    "/forgot-password",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def forgot_password(
    payload: ForgotPasswordRequest,
    auth_service: AuthService = auth_service_dependency,
) -> MessageResponse:
    """Queue a password reset email if the account is eligible."""
    await auth_service.forgot_password(email=str(payload.email))
    return MessageResponse(
        message="If that account is eligible, a password reset email was queued."
    )


@auth_router.post("/reset-password", response_model=MessageResponse)
async def reset_password(
    payload: ResetPasswordRequest,
    auth_service: AuthService = auth_service_dependency,
) -> MessageResponse:
    """Reset a user's password using a single-use token."""
    await auth_service.reset_password(token=payload.token, password=payload.password)
    return MessageResponse(message="Your password has been reset.")
