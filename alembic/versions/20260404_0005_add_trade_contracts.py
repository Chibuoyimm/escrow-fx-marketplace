"""Add trade contracts."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260404_0005"
down_revision: str | None = "20260404_0004"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Add trade contract support."""
    op.create_table(
        "trade_contracts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("accepted_offer_id", sa.Uuid(), nullable=False),
        sa.Column("agreed_rate", sa.Numeric(24, 8), nullable=False),
        sa.Column("reference_rate_snapshot", sa.Numeric(24, 8), nullable=True),
        sa.Column("from_amount", sa.Numeric(24, 8), nullable=False),
        sa.Column("to_amount", sa.Numeric(24, 8), nullable=False),
        sa.Column("funding_deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["accepted_offer_id"], ["exchange_offers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["request_id"], ["exchange_requests.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
        sa.UniqueConstraint("accepted_offer_id"),
    )


def downgrade() -> None:
    """Remove trade contract support."""
    op.drop_table("trade_contracts")
