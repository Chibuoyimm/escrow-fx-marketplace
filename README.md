# Escrow FX Marketplace

Typed FastAPI backend for a layered escrow foreign-exchange marketplace.

## Architecture

The project starts with an application-first structure that keeps business logic and transport concerns separate:

```text
app/
├── api/
├── domain/
├── infrastructure/
├── integrations/
├── models/
├── orchestrators/
├── repositories/
├── schemas/
├── services/
└── workers/
```

The detailed product and system plan lives in [`docs/escrow-plan.md`](docs/escrow-plan.md).

## Local Setup

1. Create or refresh the virtual environment:
   ```bash
   python3 -m venv .venv
   ```
2. Activate it:
   ```bash
   source .venv/bin/activate
   ```
3. Install project dependencies:
   ```bash
   pip install -e ".[dev]"
   ```
4. Create your local environment file:
   ```bash
   cp .env.example .env
   ```

## Local Database

This repo includes a local PostgreSQL service via Docker Compose. The default
`APP_DATABASE_URL` in `.env.example` is already aligned with it and uses port
`5433` so it does not collide with a machine-level PostgreSQL instance that may
already be using `5432`.

Start Postgres with:

```bash
make db-up
```

Wait for the container to become healthy, then run the latest migration:

```bash
make migrate
```

If you want to inspect the database logs:

```bash
make db-logs
```

## Quality Gates

- `ruff` for linting and formatting
- `mypy` in strict mode for static typing
- `pytest` for tests
- `pre-commit` for local automation

## Persistence

- Async SQLAlchemy 2.0 for runtime persistence
- Alembic for schema migrations
- Problem Details style API errors with centralized exception handling
- JWT bearer auth with role-based authorization

Run the latest migration with:

```bash
make migrate
```

Bootstrap the first admin user with:

```bash
.venv/bin/python -m app.bootstrap_admin create-admin --email admin@example.com --password "ChangeMe123!" --country NG
```

Seed reference currencies and corridors with:

```bash
make seed-reference-data
```

## Run

```bash
make run
```

The full local startup flow is:

```bash
source .venv/bin/activate
cp .env.example .env
make db-up
make migrate
make run
```
