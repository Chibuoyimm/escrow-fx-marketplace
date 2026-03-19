"""Problem details response schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProblemDetails(BaseModel):
    """RFC 7807-style problem details payload."""

    model_config = ConfigDict(extra="allow")

    type: str = Field(default="about:blank")
    title: str
    status: int
    detail: str
    instance: str | None = None
    error_code: str
    request_id: str | None = None
    errors: list[dict[str, Any]] | None = None

