from __future__ import annotations

import os
from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command
from app.models.account_audit_event import AccountAuditEventModel


def test_alembic_upgrades_empty_database_to_head(tmp_path: Path) -> None:
    database_path = tmp_path / "migration-test.db"
    database_url = f"sqlite+pysqlite:///{database_path}"

    config = Config("alembic.ini")
    config.set_main_option("script_location", "alembic")

    previous = os.environ.get("ALEMBIC_DATABASE_URL")
    os.environ["ALEMBIC_DATABASE_URL"] = database_url
    try:
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("ALEMBIC_DATABASE_URL", None)
        else:
            os.environ["ALEMBIC_DATABASE_URL"] = previous

    engine = create_engine(database_url)
    inspector = inspect(engine)
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    email_verification_token_columns = {
        column["name"] for column in inspector.get_columns("email_verification_tokens")
    }
    password_reset_token_columns = {
        column["name"] for column in inspector.get_columns("password_reset_tokens")
    }
    kyc_verification_columns = {
        column["name"] for column in inspector.get_columns("kyc_verifications")
    }
    exchange_request_columns = {
        column["name"] for column in inspector.get_columns("exchange_requests")
    }
    exchange_offer_columns = {column["name"] for column in inspector.get_columns("exchange_offers")}
    trade_contract_columns = {column["name"] for column in inspector.get_columns("trade_contracts")}
    outbox_event_columns = {column["name"] for column in inspector.get_columns("outbox_events")}
    account_audit_columns = {
        column["name"] for column in inspector.get_columns("account_audit_events")
    }
    idempotency_columns = {
        column["name"] for column in inspector.get_columns("idempotency_records")
    }
    audit_event_column = next(
        column
        for column in inspector.get_columns("account_audit_events")
        if column["name"] == "event_type"
    )

    assert "users" in inspector.get_table_names()
    assert "currencies" in inspector.get_table_names()
    assert "corridors" in inspector.get_table_names()
    assert "corridor_rails" in inspector.get_table_names()
    assert "exchange_requests" in inspector.get_table_names()
    assert "exchange_offers" in inspector.get_table_names()
    assert "trade_contracts" in inspector.get_table_names()
    assert "outbox_events" in inspector.get_table_names()
    assert "account_audit_events" in inspector.get_table_names()
    assert "email_verification_tokens" in inspector.get_table_names()
    assert "password_reset_tokens" in inspector.get_table_names()
    assert "kyc_verifications" in inspector.get_table_names()
    assert "idempotency_records" in inspector.get_table_names()
    assert "password_hash" in user_columns
    assert "email_verified_at" in user_columns
    assert "token_hash" in email_verification_token_columns
    assert "consumed_at" in email_verification_token_columns
    assert "token_hash" in password_reset_token_columns
    assert "consumed_at" in password_reset_token_columns
    assert "identifier_hash" in kyc_verification_columns
    assert "masked_identifier" in kyc_verification_columns
    assert "provider_reference_id" in kyc_verification_columns
    assert "review_events" in kyc_verification_columns
    assert "expires_at" in exchange_request_columns
    assert "relisted_from_request_id" in exchange_request_columns
    assert "offered_rate" in exchange_offer_columns
    assert "accepted_offer_id" in trade_contract_columns
    assert "event_type" in outbox_event_columns
    assert "payload" in outbox_event_columns
    assert {"subject_user_id", "actor_user_id", "event_type", "occurred_at", "metadata"} <= {
        *account_audit_columns
    }
    assert {
        "principal_user_id",
        "operation_scope",
        "key_hash",
        "request_fingerprint",
        "status",
        "response_status_code",
        "response_body",
        "created_at",
        "updated_at",
        "expires_at",
        "completed_at",
    } <= idempotency_columns
    assert getattr(audit_event_column["type"], "length", None) == 64
    assert getattr(AccountAuditEventModel.__table__.c.event_type.type, "length", None) == 64
    assert {
        "ix_account_audit_events_subject_occurred",
        "ix_account_audit_events_actor_occurred",
        "ix_account_audit_events_event_type",
    } <= {index["name"] for index in inspector.get_indexes("account_audit_events")}
    request_unique_constraints = {
        constraint["name"] for constraint in inspector.get_unique_constraints("exchange_requests")
    }
    assert "uq_exchange_requests_relisted_from_request_id" in request_unique_constraints
    request_indexes = {index["name"] for index in inspector.get_indexes("exchange_requests")}
    offer_indexes = {index["name"] for index in inspector.get_indexes("exchange_offers")}
    assert "ix_exchange_requests_creator_created_id" in request_indexes
    assert "ix_exchange_requests_status_created_id" in request_indexes
    assert "ix_exchange_offers_user_created_id" in offer_indexes
    assert "ix_exchange_offers_status_created_id" in offer_indexes
    request_foreign_keys = inspector.get_foreign_keys("exchange_requests")
    assert any(
        foreign_key["referred_table"] == "exchange_requests"
        and foreign_key["constrained_columns"] == ["relisted_from_request_id"]
        for foreign_key in request_foreign_keys
    )
    idempotency_unique_constraints = {
        constraint["name"] for constraint in inspector.get_unique_constraints("idempotency_records")
    }
    assert "uq_idempotency_records_principal_scope_key" in idempotency_unique_constraints
    assert "ix_idempotency_records_expires_at" in {
        index["name"] for index in inspector.get_indexes("idempotency_records")
    }

    migration_text = Path(
        "alembic/versions/20260726_0011_add_request_lineage_and_marketplace_indexes.py"
    ).read_text()
    assert 'recreate="always"' not in migration_text

    previous = os.environ.get("ALEMBIC_DATABASE_URL")
    os.environ["ALEMBIC_DATABASE_URL"] = database_url
    try:
        command.downgrade(config, "20260628_0010")
    finally:
        if previous is None:
            os.environ.pop("ALEMBIC_DATABASE_URL", None)
        else:
            os.environ["ALEMBIC_DATABASE_URL"] = previous

    downgraded_inspector = inspect(engine)
    downgraded_columns = {
        column["name"] for column in downgraded_inspector.get_columns("exchange_requests")
    }
    assert "relisted_from_request_id" not in downgraded_columns
    assert "account_audit_events" not in downgraded_inspector.get_table_names()
    assert "idempotency_records" not in downgraded_inspector.get_table_names()
    assert "ix_exchange_requests_creator_created_id" not in {
        index["name"] for index in downgraded_inspector.get_indexes("exchange_requests")
    }
