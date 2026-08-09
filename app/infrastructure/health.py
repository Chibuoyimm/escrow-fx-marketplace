"""Dependency checks used by readiness endpoints."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text

from app.infrastructure.config import settings
from app.infrastructure.database.session import AsyncSessionFactory

logger = logging.getLogger(__name__)


async def database_readiness_check() -> bool:
    """Perform a bounded, read-only database connectivity check."""
    try:
        async with asyncio.timeout(settings.readiness_timeout_seconds):
            async with AsyncSessionFactory() as session:
                await session.execute(text("SELECT 1"))
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - readiness must never expose dependency details.
        logger.warning("readiness_check_failed", extra={"event": "readiness_check_failed"})
        return False
    return True
