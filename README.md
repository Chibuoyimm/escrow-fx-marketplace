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
Agent-facing codebase guidance lives in [`AGENTS.md`](AGENTS.md).

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

Nigeria KYC has a provider-ready backend foundation:

```bash
POST /api/v1/kyc/submit
GET /api/v1/kyc/status
```

The default `APP_KYC_PROVIDER="local"` mode is deterministic and is intended for
local/dev testing while Youverify account access is pending. Set
`APP_KYC_PROVIDER="youverify"` plus `APP_YOUVERIFY_API_KEY` when real provider
access is available. The integration is intentionally configured through
provider-neutral service code so the BVN endpoint can be swapped if Youverify
offers a cheaper non-premium BVN product.

Reconcile pending provider KYC checks with:

```bash
make reconcile-kyc
```

Marketplace lifecycle endpoints include:

```bash
PATCH /api/v1/exchange-requests/{request_id}
POST /api/v1/exchange-requests/{request_id}/relist
PATCH /api/v1/offers/{offer_id}
GET /api/v1/offers/mine
GET /api/v1/offers/{offer_id}
```

Requests can be edited only while `request_open` and before any historical offer
exists. Offers can change their rate only while active and before the parent
request is locked. Relisting creates a new open request and never resurrects or
mutates the original terminal request. The new request exposes
`relisted_from_request_id`; a terminal request can have only one direct
successor, while a later terminal successor may itself be relisted.

Marketplace and admin list endpoints return cursor-paginated responses with
`items` and `next_cursor`. Use `limit`, `cursor`, and the endpoint-specific
status/currency/amount/rate filters. Marketplace, participant, and admin
request/offer/trade pages also accept inclusive `created_from` and
`created_to` filters. Naive or timezone-aware date values are normalized to
UTC before comparison. Ordering is newest first by `created_at`, then by ID for
stable pagination; malformed cursors and reversed ranges return the standard
domain validation error. This is an intentional v1 breaking contract change:
because no frontend/client exists yet, list arrays were changed to
`{items, next_cursor}` rather than adding duplicate paginated endpoints.

Offer history reads include the parent request's status, currency pair, source
amount, preferred rate, and expiry. They remain available to either the offer
owner or request creator after the marketplace request becomes terminal.

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
`exchange_request.relisted` triggers `exchange-request-relisted`,
`exchange_offer.updated` triggers `exchange-offer-updated`, and
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
