"""Reusable cursor-paginated API response schemas."""

from __future__ import annotations

from pydantic import BaseModel


class CursorPage[ItemT: object](BaseModel):
    """A page ordered newest first with a cursor for the next page."""

    items: list[ItemT]
    next_cursor: str | None = None
