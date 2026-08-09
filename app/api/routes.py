"""HTTP route definitions."""

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from app.infrastructure.health import database_readiness_check

health_router = APIRouter(tags=["health"])


@health_router.get("/health", summary="Health check")
async def health_check() -> dict[str, str]:
    """Return the legacy liveness-compatible health response."""
    return {"status": "ok"}


@health_router.get("/health/live", summary="Liveness check")
async def liveness_check() -> dict[str, str]:
    """Return success without checking external dependencies."""
    return {"status": "alive"}


@health_router.get("/health/ready", summary="Readiness check", response_model=None)
async def readiness_check(
    ready: bool = Depends(database_readiness_check),
) -> dict[str, str] | JSONResponse:
    """Return ready only when the database responds to a read-only probe."""
    if ready:
        return {"status": "ready"}
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"status": "not_ready"}
    )
