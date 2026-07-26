"""Add append-only account audit history."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260726_0012"
down_revision: str | None = "20260726_0011"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Create account audit events for security-sensitive changes."""
    op.create_table(
        "account_audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_user_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["subject_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_account_audit_events_subject_occurred",
        "account_audit_events",
        ["subject_user_id", "occurred_at"],
    )
    op.create_index(
        "ix_account_audit_events_actor_occurred",
        "account_audit_events",
        ["actor_user_id", "occurred_at"],
    )
    op.create_index(
        "ix_account_audit_events_event_type",
        "account_audit_events",
        ["event_type"],
    )


def downgrade() -> None:
    """Remove account audit history."""
    op.drop_index("ix_account_audit_events_event_type", table_name="account_audit_events")
    op.drop_index("ix_account_audit_events_actor_occurred", table_name="account_audit_events")
    op.drop_index("ix_account_audit_events_subject_occurred", table_name="account_audit_events")
    op.drop_table("account_audit_events")
