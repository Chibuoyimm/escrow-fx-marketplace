"""HTTP route definitions."""

from fastapi import APIRouter

health_router = APIRouter(tags=["health"])


@health_router.get("/health", summary="Health check")
async def health_check() -> dict[str, str]:
    """Return the service health status."""
    return {"status": "ok"}
