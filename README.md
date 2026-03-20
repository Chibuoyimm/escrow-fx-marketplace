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
.venv/bin/alembic upgrade head
```

Bootstrap the first admin user with:

```bash
.venv/bin/python -m app.bootstrap_admin create-admin --email admin@example.com --password "ChangeMe123!" --country NG
```

## Run

```bash
.venv/bin/uvicorn app.main:app --reload
```
