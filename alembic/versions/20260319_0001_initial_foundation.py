"""Create the initial persistence foundation tables."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260319_0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Apply the initial foundation schema."""
    op.create_table(
        "currencies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=3), nullable=False),
        sa.Column("minor_unit", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("min_amount", sa.Numeric(24, 8), nullable=False),
        sa.Column("max_amount", sa.Numeric(24, 8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_currencies_code"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("kyc_status", sa.String(length=32), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "corridors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("from_currency_id", sa.Uuid(), nullable=False),
        sa.Column("to_currency_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("funding_sla_minutes", sa.Integer(), nullable=False),
        sa.Column("fee_model_name", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["from_currency_id"], ["currencies.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["to_currency_id"], ["currencies.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "from_currency_id",
            "to_currency_id",
            name="uq_corridors_currency_pair",
        ),
    )
    op.create_table(
        "corridor_rails",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("corridor_id", sa.Uuid(), nullable=False),
        sa.Column("flow_type", sa.String(length=32), nullable=False),
        sa.Column("priority_order", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("method", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["corridor_id"], ["corridors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "corridor_id",
            "flow_type",
            "priority_order",
            name="uq_corridor_rails_priority",
        ),
    )


def downgrade() -> None:
    """Revert the initial foundation schema."""
    op.drop_table("corridor_rails")
    op.drop_table("corridors")
    op.drop_table("users")
    op.drop_table("currencies")
