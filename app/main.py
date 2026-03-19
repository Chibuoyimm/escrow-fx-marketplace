"""FastAPI application entrypoint."""

from fastapi import FastAPI

from app.api.exception_handlers import register_exception_handlers
from app.api.router import api_router
from app.infrastructure.request_context import register_request_context


def create_application() -> FastAPI:
    """Build and configure the FastAPI application."""
    application = FastAPI(
        title="Escrow FX Marketplace API",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )
    register_request_context(application)
    register_exception_handlers(application)
    application.include_router(api_router)
    return application


app = create_application()
