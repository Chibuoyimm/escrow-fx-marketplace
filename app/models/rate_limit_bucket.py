"""Persistent API rate-limit bucket model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.entities import RateLimitBucket
from app.models.base import Base, UUIDPrimaryKeyMixin


class RateLimitBucketModel(UUIDPrimaryKeyMixin, Base):
    """One hashed identity counter for one named policy."""

    __tablename__ = "rate_limit_buckets"
    __table_args__ = (
        UniqueConstraint(
            "policy_name",
            "key_hash",
            name="uq_rate_limit_buckets_policy_key",
        ),
        Index("ix_rate_limit_buckets_expires_at", "expires_at"),
    )

    policy_name: Mapped[str] = mapped_column(String(100))
    key_hash: Mapped[str] = mapped_column(String(64))
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    request_count: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def to_domain(self) -> RateLimitBucket:
        """Convert the ORM row to a domain counter."""
        return RateLimitBucket(
            policy_name=self.policy_name,
            key_hash=self.key_hash,
            window_started_at=self.window_started_at,
            request_count=self.request_count,
            expires_at=self.expires_at,
        )
