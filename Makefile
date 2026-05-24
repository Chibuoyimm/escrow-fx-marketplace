.PHONY: format lint typecheck test db-up db-down db-reset db-logs migrate run seed-reference-data expire-marketplace reconcile-kyc dispatch-notifications

format:
	.venv/bin/ruff format .

lint:
	.venv/bin/ruff check .

typecheck:
	.venv/bin/mypy .

test:
	.venv/bin/pytest

db-up:
	docker compose up -d postgres

db-down:
	docker compose down

db-reset:
	docker compose down -v
	docker compose up -d postgres
	.venv/bin/alembic upgrade head
	.venv/bin/python -m app.seed_reference_data

db-logs:
	docker compose logs -f postgres

migrate:
	.venv/bin/alembic upgrade head

run:
	.venv/bin/uvicorn app.main:app --reload

seed-reference-data:
	.venv/bin/python -m app.seed_reference_data

expire-marketplace:
	.venv/bin/python -m app.expire_marketplace

reconcile-kyc:
	.venv/bin/python -m app.reconcile_kyc

dispatch-notifications:
	.venv/bin/python -m app.dispatch_notifications
