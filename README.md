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

To wipe the local Docker database and rebuild it from migrations plus seed data:

```bash
make db-reset
```

`make db-down` only stops the container. It does not delete the database volume.

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

Customer registration now queues an email verification notification. Customers
must verify their email before login; bootstrap/admin-created users are marked
verified automatically.

```bash
POST /api/v1/auth/verify-email
POST /api/v1/auth/resend-verification
POST /api/v1/auth/forgot-password
POST /api/v1/auth/reset-password
```

Set `APP_EMAIL_VERIFICATION_FRONTEND_URL` to the frontend page that reads the
token from the URL and calls `POST /api/v1/auth/verify-email`.

Set `APP_PASSWORD_RESET_FRONTEND_URL` to the frontend page that reads the reset
token from the URL and calls `POST /api/v1/auth/reset-password`.

Seed reference currencies and corridors with:

```bash
make seed-reference-data
```

Expire due marketplace records with:

```bash
make expire-marketplace
```

The expiry command updates stale marketplace state and records outbox events for
affected users. It also records a summary `marketplace_expiry.completed` event
for operational inspection.

Admin and operations users can inspect pending notification/outbox events with:

```bash
GET /api/v1/admin/events
GET /api/v1/admin/events?status=pending
GET /api/v1/admin/events?event_type=trade_contract.cancelled
```

Dispatch pending notification/outbox events with:

```bash
make dispatch-notifications
```

The local dispatcher uses a development logging provider by default. To dispatch
through Knock instead, set:

```bash
APP_NOTIFICATION_PROVIDER="knock"
APP_KNOCK_API_KEY="sk_..."
APP_KNOCK_BRANCH=""
```

Outbox event types are mapped to hyphenated Knock workflow keys. For example,
`exchange_request.created` triggers `exchange-request-created`, and
`trade_contract.locked` triggers `trade-contract-locked`.

The dispatcher marks events as delivered on success and schedules failed events
for retry with exponential backoff. Events that exhaust
`APP_NOTIFICATION_MAX_ATTEMPTS` are marked `dead` for admin inspection instead of
retrying forever. The Knock provider sends top-level uppercase rendering data
such as `REQUEST_ID`, `OFFER_ID`, and `USER_NAME`.

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
make seed-reference-data
make run
```
