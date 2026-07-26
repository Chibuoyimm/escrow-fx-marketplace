"""Add relist lineage and marketplace list indexes."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260726_0011"
down_revision: str | None = "20260628_0010"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Track direct relist successors and support keyset list access paths."""
    with op.batch_alter_table("exchange_requests") as batch_op:
        batch_op.add_column(sa.Column("relisted_from_request_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_exchange_requests_relisted_from_request_id",
            "exchange_requests",
            ["relisted_from_request_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_unique_constraint(
            "uq_exchange_requests_relisted_from_request_id",
            ["relisted_from_request_id"],
        )

    op.create_index(
        "ix_exchange_requests_creator_created_id",
        "exchange_requests",
        ["creator_user_id", "created_at", "id"],
    )
    op.create_index(
        "ix_exchange_requests_status_created_id",
        "exchange_requests",
        ["status", "created_at", "id"],
    )
    op.create_index(
        "ix_exchange_offers_user_created_id",
        "exchange_offers",
        ["offer_user_id", "created_at", "id"],
    )
    op.create_index(
        "ix_exchange_offers_status_created_id",
        "exchange_offers",
        ["status", "created_at", "id"],
    )
    op.create_index(
        "ix_exchange_offers_request_created_id",
        "exchange_offers",
        ["request_id", "created_at", "id"],
    )
    op.create_index(
        "ix_trade_contracts_created_id",
        "trade_contracts",
        ["created_at", "id"],
    )


def downgrade() -> None:
    """Remove marketplace list indexes and relist lineage."""
    op.drop_index("ix_trade_contracts_created_id", table_name="trade_contracts")
    op.drop_index("ix_exchange_offers_request_created_id", table_name="exchange_offers")
    op.drop_index("ix_exchange_offers_status_created_id", table_name="exchange_offers")
    op.drop_index("ix_exchange_offers_user_created_id", table_name="exchange_offers")
    op.drop_index("ix_exchange_requests_status_created_id", table_name="exchange_requests")
    op.drop_index("ix_exchange_requests_creator_created_id", table_name="exchange_requests")
    with op.batch_alter_table("exchange_requests") as batch_op:
        batch_op.drop_constraint(
            "uq_exchange_requests_relisted_from_request_id",
            type_="unique",
        )
        batch_op.drop_constraint(
            "fk_exchange_requests_relisted_from_request_id",
            type_="foreignkey",
        )
        batch_op.drop_column("relisted_from_request_id")
