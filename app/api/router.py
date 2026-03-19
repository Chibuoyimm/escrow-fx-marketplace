"""Top-level API router assembly."""

from fastapi import APIRouter

from app.api.routes import health_router
from app.infrastructure.config import settings

api_router = APIRouter(prefix=settings.api_v1_prefix)
api_router.include_router(health_router)
