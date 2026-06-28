"""Add KYC review event audit history."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260628_0010"
down_revision: str | None = "20260516_0009"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Add structured KYC review history."""
    op.add_column(
        "kyc_verifications",
        sa.Column(
            "review_events",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    """Remove structured KYC review history."""
    op.drop_column("kyc_verifications", "review_events")
