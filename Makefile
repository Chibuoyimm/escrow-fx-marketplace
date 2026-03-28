.PHONY: format lint typecheck test db-up db-down db-logs migrate run seed-reference-data

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

db-logs:
	docker compose logs -f postgres

migrate:
	.venv/bin/alembic upgrade head

run:
	.venv/bin/uvicorn app.main:app --reload

seed-reference-data:
	.venv/bin/python -m app.seed_reference_data
