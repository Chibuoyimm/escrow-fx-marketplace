"""Password reset token ORM model."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.entities import PasswordResetToken
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PasswordResetTokenModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Single-use password reset token row."""

    __tablename__ = "password_reset_tokens"

    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def to_domain(self) -> PasswordResetToken:
        """Convert the ORM row to a domain entity."""
        return PasswordResetToken(
            id=self.id,
            user_id=self.user_id,
            token_hash=self.token_hash,
            expires_at=self.expires_at,
            consumed_at=self.consumed_at,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
