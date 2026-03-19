"""FastAPI application entrypoint."""

from fastapi import FastAPI

from app.api.router import api_router


def create_application() -> FastAPI:
    """Build and configure the FastAPI application."""
    application = FastAPI(
        title="Escrow FX Marketplace API",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )
    application.include_router(api_router)
    return application


app = create_application()

