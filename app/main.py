"""FastAPI application entrypoint."""

from fastapi import FastAPI
from fastapi.responses import Response

from app.api.exception_handlers import register_exception_handlers
from app.api.router import api_router
from app.infrastructure.application_logging import configure_logging
from app.infrastructure.config import settings
from app.infrastructure.metrics import CONTENT_TYPE_LATEST, metrics_enabled, render_metrics
from app.infrastructure.request_context import register_request_context


def create_application() -> FastAPI:
    """Build and configure the FastAPI application."""
    configure_logging()
    application = FastAPI(
        title="Escrow FX Marketplace API",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )
    register_request_context(application)
    register_exception_handlers(application)
    application.include_router(api_router)
    if metrics_enabled():

        @application.get(settings.metrics_path, include_in_schema=False)
        async def metrics() -> Response:
            return Response(content=render_metrics(), media_type=CONTENT_TYPE_LATEST)

    return application


app = create_application()
