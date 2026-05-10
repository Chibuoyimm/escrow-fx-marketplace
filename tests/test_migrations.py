from __future__ import annotations

import os
from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command


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
    exchange_request_columns = {
        column["name"] for column in inspector.get_columns("exchange_requests")
    }
    exchange_offer_columns = {column["name"] for column in inspector.get_columns("exchange_offers")}
    trade_contract_columns = {column["name"] for column in inspector.get_columns("trade_contracts")}
    outbox_event_columns = {column["name"] for column in inspector.get_columns("outbox_events")}

    assert "users" in inspector.get_table_names()
    assert "currencies" in inspector.get_table_names()
    assert "corridors" in inspector.get_table_names()
    assert "corridor_rails" in inspector.get_table_names()
    assert "exchange_requests" in inspector.get_table_names()
    assert "exchange_offers" in inspector.get_table_names()
    assert "trade_contracts" in inspector.get_table_names()
    assert "outbox_events" in inspector.get_table_names()
    assert "email_verification_tokens" in inspector.get_table_names()
    assert "password_hash" in user_columns
    assert "email_verified_at" in user_columns
    assert "token_hash" in email_verification_token_columns
    assert "consumed_at" in email_verification_token_columns
    assert "expires_at" in exchange_request_columns
    assert "offered_rate" in exchange_offer_columns
    assert "accepted_offer_id" in trade_contract_columns
    assert "event_type" in outbox_event_columns
    assert "payload" in outbox_event_columns
