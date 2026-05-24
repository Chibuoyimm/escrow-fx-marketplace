"""Add KYC verification attempts."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260516_0009"
down_revision: str | None = "20260510_0008"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Add KYC verification attempts."""
    op.create_table(
        "kyc_verifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_reference_id", sa.String(length=128), nullable=False),
        sa.Column("id_type", sa.String(length=32), nullable=False),
        sa.Column("masked_identifier", sa.String(length=32), nullable=False),
        sa.Column("identifier_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("provider_status", sa.String(length=64), nullable=False),
        sa.Column("field_match_summary", sa.JSON(), nullable=False),
        sa.Column("rejection_reason", sa.String(length=500), nullable=True),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_kyc_verifications_identifier_hash",
        "kyc_verifications",
        ["identifier_hash"],
    )
    op.create_index("ix_kyc_verifications_status", "kyc_verifications", ["status"])
    op.create_index("ix_kyc_verifications_user_id", "kyc_verifications", ["user_id"])
    op.create_index(
        "ix_kyc_verifications_user_created",
        "kyc_verifications",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_kyc_verifications_provider_reference",
        "kyc_verifications",
        ["provider", "provider_reference_id"],
    )


def downgrade() -> None:
    """Remove KYC verification attempts."""
    op.drop_index("ix_kyc_verifications_provider_reference", table_name="kyc_verifications")
    op.drop_index("ix_kyc_verifications_user_created", table_name="kyc_verifications")
    op.drop_index("ix_kyc_verifications_user_id", table_name="kyc_verifications")
    op.drop_index("ix_kyc_verifications_status", table_name="kyc_verifications")
    op.drop_index("ix_kyc_verifications_identifier_hash", table_name="kyc_verifications")
    op.drop_table("kyc_verifications")
