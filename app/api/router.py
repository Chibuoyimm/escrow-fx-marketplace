"""Top-level API router assembly."""

from fastapi import APIRouter

from app.api.auth_routes import auth_router
from app.api.routes import health_router
from app.api.user_routes import users_router
from app.infrastructure.config import settings

api_router = APIRouter(prefix=settings.api_v1_prefix)
api_router.include_router(auth_router)
api_router.include_router(health_router)
api_router.include_router(users_router)
