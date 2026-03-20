"""API dependencies for authentication and authorization."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.domain.auth import AuthenticatedPrincipal
from app.domain.enums import UserRole
from app.domain.exceptions import AuthenticationError, AuthorizationError
from app.services.auth import AuthService, get_auth_service

bearer_scheme = HTTPBearer(auto_error=False)
bearer_credentials_dependency = Depends(bearer_scheme)
auth_service_dependency = Depends(get_auth_service)


async def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = bearer_credentials_dependency,
    auth_service: AuthService = auth_service_dependency,
) -> AuthenticatedPrincipal:
    """Resolve the current principal from the bearer token."""
    if credentials is None:
        raise AuthenticationError()
    return await auth_service.authenticate_token(credentials.credentials)


def require_roles(
    *allowed_roles: UserRole,
) -> Callable[[AuthenticatedPrincipal], Awaitable[AuthenticatedPrincipal]]:
    """Build a dependency that restricts access to specific roles."""

    current_principal_dependency = Depends(get_current_principal)

    async def dependency(
        principal: AuthenticatedPrincipal = current_principal_dependency,
    ) -> AuthenticatedPrincipal:
        if principal.role not in allowed_roles:
            raise AuthorizationError()
        return principal

    return dependency
