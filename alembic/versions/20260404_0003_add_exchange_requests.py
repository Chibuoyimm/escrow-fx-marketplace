"""Add exchange requests."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260404_0003"
down_revision: str | None = "20260319_0002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Add exchange request support."""
    op.create_table(
        "exchange_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("creator_user_id", sa.Uuid(), nullable=False),
        sa.Column("from_currency_id", sa.Uuid(), nullable=False),
        sa.Column("to_currency_id", sa.Uuid(), nullable=False),
        sa.Column("from_amount", sa.Numeric(24, 8), nullable=False),
        sa.Column("preferred_rate", sa.Numeric(24, 8), nullable=False),
        sa.Column("min_rate", sa.Numeric(24, 8), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["creator_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["from_currency_id"], ["currencies.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["to_currency_id"], ["currencies.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Remove exchange request support."""
    op.drop_table("exchange_requests")
