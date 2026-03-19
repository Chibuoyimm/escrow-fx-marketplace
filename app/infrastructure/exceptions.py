"""Infrastructure-layer exceptions."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.exceptions import ErrorCode


@dataclass(slots=True)
class InfrastructureError(Exception):
    """Base class for infrastructure failures."""

    title: str
    detail: str
    error_code: str = ErrorCode.INFRASTRUCTURE_FAILURE
    status_code: int = 503

