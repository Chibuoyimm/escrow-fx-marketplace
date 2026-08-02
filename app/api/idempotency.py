"""API helpers for idempotent mutation headers and replay responses."""

from __future__ import annotations

from fastapi.responses import JSONResponse

from app.infrastructure.idempotency import IdempotencyReplay


def replay_response(replay: IdempotencyReplay) -> JSONResponse:
    """Return a previously committed response without re-running the mutation."""
    return JSONResponse(
        status_code=replay.status_code,
        content=replay.response_body,
        media_type="application/json",
    )
