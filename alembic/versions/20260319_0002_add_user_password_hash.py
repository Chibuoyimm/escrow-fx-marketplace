"""Add password hash to users."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260319_0002"
down_revision: str | None = "20260319_0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Add password hash support to users."""
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column(
                "password_hash",
                sa.String(length=255),
                nullable=False,
                server_default="bootstrap-placeholder",
            )
        )
        batch_op.alter_column("password_hash", server_default=None)


def downgrade() -> None:
    """Remove password hash support from users."""
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("password_hash")
