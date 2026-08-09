"""Authentication routes."""

from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_current_principal
from app.api.rate_limiting import (
    authenticated_rate_limit_dependency,
    public_rate_limit_dependency,
)
from app.domain.auth import AuthenticatedPrincipal
from app.infrastructure.rate_limiting import (
    AUTH_CHANGE_PASSWORD,
    AUTH_FORGOT_PASSWORD,
    AUTH_LOGIN,
    AUTH_REGISTER,
    AUTH_RESEND_VERIFICATION,
    AUTH_RESET_PASSWORD,
    AUTH_VERIFY_EMAIL,
)
from app.schemas.auth import (
    AccessTokenResponse,
    ChangePasswordRequest,
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
current_principal_dependency = Depends(get_current_principal)


@auth_router.post(
    "/register",
    response_model=RegisterUserResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(public_rate_limit_dependency(AUTH_REGISTER, identifier_field="email"))],
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


@auth_router.post(
    "/login",
    response_model=AccessTokenResponse,
    dependencies=[Depends(public_rate_limit_dependency(AUTH_LOGIN, identifier_field="email"))],
)
async def login_user(
    payload: LoginRequest,
    auth_service: AuthService = auth_service_dependency,
) -> AccessTokenResponse:
    """Authenticate a user and issue a bearer token."""
    return await auth_service.login_user(email=str(payload.email), password=payload.password)


@auth_router.post(
    "/verify-email",
    response_model=EmailVerificationResponse,
    dependencies=[
        Depends(public_rate_limit_dependency(AUTH_VERIFY_EMAIL, identifier_field="token"))
    ],
)
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
    dependencies=[
        Depends(public_rate_limit_dependency(AUTH_RESEND_VERIFICATION, identifier_field="email"))
    ],
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
    dependencies=[
        Depends(public_rate_limit_dependency(AUTH_FORGOT_PASSWORD, identifier_field="email"))
    ],
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


@auth_router.post(
    "/reset-password",
    response_model=MessageResponse,
    dependencies=[
        Depends(public_rate_limit_dependency(AUTH_RESET_PASSWORD, identifier_field="token"))
    ],
)
async def reset_password(
    payload: ResetPasswordRequest,
    auth_service: AuthService = auth_service_dependency,
) -> MessageResponse:
    """Reset a user's password using a single-use token."""
    await auth_service.reset_password(token=payload.token, password=payload.password)
    return MessageResponse(message="Your password has been reset.")


@auth_router.post(
    "/change-password",
    response_model=MessageResponse,
    dependencies=[Depends(authenticated_rate_limit_dependency(AUTH_CHANGE_PASSWORD))],
)
async def change_password(
    payload: ChangePasswordRequest,
    principal: AuthenticatedPrincipal = current_principal_dependency,
    auth_service: AuthService = auth_service_dependency,
) -> MessageResponse:
    """Change the authenticated user's password."""
    await auth_service.change_password(
        user_id=principal.user_id,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    return MessageResponse(message="Your password has been changed.")
