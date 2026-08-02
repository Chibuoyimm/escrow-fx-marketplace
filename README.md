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

Account management currently supports:

```bash
PATCH /api/v1/users/me
POST /api/v1/users/me/deactivate
PATCH /api/v1/admin/users/{user_id}/status
```

Profile updates are limited to `phone`; formatting whitespace is removed and
the remaining value must contain `+` followed by 7-15 digits.
This is provider-neutral validation, not country-specific number validation.
Email and country remain immutable. An empty update and unknown fields are
rejected. Deactivation requires the current password and changes the user to
`inactive` without deleting marketplace, KYC, or trade records. It is rejected
while the user owns a non-expired open/pending request, owns a non-expired active
offer, or participates
in a trade that is not `settled` or `cancelled`. A successful deactivation
immediately invalidates the current bearer token, so a repeat call with that
token returns `401`; an administrator can reactivate an inactive account.
Administrators can suspend active users even when obligations exist. Suspension
does not silently cancel marketplace records, but hides the owner's requests
from the board and prevents new offers or acceptance involving the suspended
participant. Expiry processing continues for their existing records.

To avoid deadlocks and keep deactivation checks serialized with marketplace
mutations, row locks are always acquired in this order: participant users sorted
by UUID, then the exchange request, then exchange offers sorted by UUID.
Services may use initial non-locking reads to discover immutable participant
relationships, but must revalidate those relationships after acquiring locks.
SQLite tests assert repository call order; PostgreSQL concurrency testing should
also exercise these paths before production rollout.

Account profile and status changes are recorded in the append-only
`account_audit_events` table and publish security outbox events in the same
transaction. Audit metadata contains field names or lifecycle states only; it
does not contain passwords or profile before/after secrets. The ORM rejects
instance updates and deletes, and production database credentials used by the
application should have no `UPDATE` or `DELETE` privileges on this table.

Notification preferences are owned by Knock and are not duplicated in the
database:

```bash
GET /api/v1/users/me/notification-preferences
PATCH /api/v1/users/me/notification-preferences
```

The response exposes `security`, `kyc`, `trade`, and `marketplace` categories,
each with `email_enabled` and `mutable`. Only
`marketplace.email_enabled` can currently be changed:

```json
{
  "categories": {
    "marketplace": {
      "email_enabled": false
    }
  }
}
```

Preference operations are immediate Knock calls and do not create outbox
events. The adapter always identifies the current Knock recipient first and
uses merge semantics so unrelated preferences are preserved. If a user-level
`default` set does not exist, the API returns all categories enabled, matching
Knock's defaults. If the configured notification provider is not Knock, these
endpoints return `503` because the logging provider cannot be a preference
source of truth.

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

## Mutation Idempotency

High-value marketplace POST mutations accept an optional `Idempotency-Key`
header. It is supported by request creation, cancellation, relisting, offer
creation, offer withdrawal/rejection, and offer acceptance/trade locking.

```http
Idempotency-Key: create-request-2026-08-02-001
```

Keys must be 1-128 characters from ASCII letters, digits, `.`, `_`, `~`, and
`-`. The key is scoped to the authenticated user and operation/resource route.
Repeating the same key with the same semantic payload returns the original
status and response without creating another marketplace row or notification
outbox event. Reusing it with a different payload returns a `409` Problem
Details response. Authentication, schema validation, and business-rule
failures do not reserve a key because the claim is rolled back with the
mutation transaction. Unexpected exceptions also roll back the claim; a client
can safely retry the same key. An explicitly committed but incomplete claim
returns `409` with `Retry-After: 1` until its processing timeout; after that,
a retry can reclaim it and the cleanup command removes any abandoned row.
Relist fingerprints preserve optional-field presence: omitting a field inherits
the original request value, while an explicit `null` clears a nullable field,
so those requests use different keys or receive a conflict.

The database stores only hashed key/fingerprint values and the safe customer
response needed for replay. Completed records are retained for 24 hours by
default. Run the bounded cleanup command from cron or another scheduler:

```bash
make cleanup-idempotency
```

Configure retention, abandoned-claim timeout, and cleanup batch size with
`APP_IDEMPOTENCY_RETENTION_HOURS`,
`APP_IDEMPOTENCY_PROCESSING_TIMEOUT_SECONDS`, and
`APP_IDEMPOTENCY_CLEANUP_BATCH_SIZE`. Do not send passwords, authorization
headers, raw KYC data, or secrets in idempotency payloads.

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

Account lifecycle paths require these committed Knock workflows before they are
enabled in an environment:

- `user.profile_updated` -> `user-profile-updated`
- `user.account_deactivated` -> `user-account-deactivated`
- `user.account_suspended` -> `user-account-suspended`
- `user.account_reactivated` -> `user-account-reactivated`

The dispatcher marks events as delivered on success and schedules failed events
for retry with exponential backoff. Events that exhaust
`APP_NOTIFICATION_MAX_ATTEMPTS` are marked `dead` for admin inspection instead of
retrying forever. The Knock provider sends top-level uppercase rendering data
such as `REQUEST_ID`, `OFFER_ID`, and `USER_NAME`.

Knock workflow categories are tracked in
`app/services/notification_categories.py`. Configure these manually in the
Knock dashboard:

- `security`: email verification, password, profile, and account-status events
- `kyc`: all `user.kyc.*` events
- `marketplace`: exchange request/offer events except accepted offers
- `trade`: accepted offers and `trade_contract.*` events
- `none`: `marketplace_expiry.completed`

Enable Knock's **Override recipient preferences** option for mandatory
security, KYC, and trade workflows. The hosted preference center should expose
only marketplace preferences. A successful outbox dispatch means Knock
accepted the workflow trigger; it does not guarantee that a channel was sent
or delivered. Actual delivery status belongs to Knock/Resend logs or provider
webhooks.

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
