# AGENTS.md

Guidance for coding agents working in this repository.

## Repository Identity

This is a backend-only FastAPI project for an escrow foreign-exchange marketplace. Do not introduce frontend application work into this repo.

Correct local repo path:

```text
/Users/chibuoyim/Code/escrow-fx-marketplace
```

Do not assume the old Desktop path is current.

## Current Architecture

The codebase is layered intentionally:

- `app/api`: FastAPI route handlers and dependencies
- `app/domain`: entities, enums, value objects, domain exceptions
- `app/infrastructure`: config, database/session plumbing, security, request context
- `app/integrations`: external provider integrations such as Knock
- `app/models`: SQLAlchemy ORM models
- `app/repositories`: repository protocols and SQLAlchemy implementations
- `app/schemas`: Pydantic request/response schemas
- `app/services`: application services and business workflows

Keep transport concerns in `api`, persistence details in repositories/models, and business rules in services/domain code.

## Current Next Work

Near-term backend priorities:

- continue account/auth hardening where useful
- continue marketplace lifecycle work before funding is introduced
- keep notification workflows aligned with new business events
- keep KYC provider integration flexible until Youverify account access confirms exact products and pricing
- update this file when a decision becomes project guidance rather than a one-off implementation detail

Funding, escrow legs, ledgers, payout rails, and payment webhooks remain deferred until explicitly picked up.

## Do Not Do

- Do not add frontend pages or frontend app code to this repo.
- Do not commit secrets or API keys.
- Do not call Knock, Resend, or other providers directly from feature services.
- Do not add new inline outbox event construction in feature services.
- Do not expose raw currency foreign-key UUIDs in customer-facing corridor payloads.
- Do not assume `make db-down` wipes local data; use `make db-reset` for that.
- Do not use the old Desktop repo path.

## Local Commands

Use the Makefile targets:

```bash
make format
make lint
make typecheck
make test
make db-up
make db-reset
make migrate
make seed-reference-data
make expire-marketplace
make reconcile-kyc
make dispatch-notifications
make run
```

The standard verification set after meaningful backend changes is:

```bash
make format
make lint
make typecheck
make test
```

## Database Notes

Local Postgres runs through Docker Compose on port `5433`.

- `make db-up` starts Postgres.
- `make db-down` stops the container but keeps the volume.
- `make db-reset` deletes the volume, reapplies migrations, and seeds reference data.
- Always run `make migrate` after adding or pulling migrations.

## Auth Decisions

Implemented auth flows include:

- registration
- login
- email verification
- resend verification
- forgot password
- reset password
- authenticated change password

Important decisions:

- Unverified users cannot log in.
- Email verification uses `POST /api/v1/auth/verify-email`.
- The backend no longer exposes a `GET /verify-email` verification endpoint.
- Verification succeeds by returning an access token plus the verified user.
- Password reset tokens and email verification tokens are stored hashed, not raw.
- Password reset does not currently auto-login the user.
- Change password requires bearer auth plus the current password.

Admin/bootstrap-created users are marked email verified automatically.

## KYC Decisions

Nigeria KYC has a provider-ready backend foundation.

- Current endpoints are `POST /api/v1/kyc/submit` and `GET /api/v1/kyc/status`.
- Supported first-pass ID types are `BVN`, `NIN`, and `VNIN`.
- Raw BVN/NIN/vNIN values must not be stored long-term.
- Store masked identifiers, identifier hashes, provider references, status, and audit timestamps.
- `APP_KYC_PROVIDER=local` is the default until Youverify account access is available.
- Do not hard-code premium BVN assumptions into service or domain code.
- Keep Youverify endpoint/version details isolated in `app/integrations/youverify.py`.
- If Youverify offers a cheaper basic BVN endpoint, swap it in through configuration or the integration layer.
- Pending KYC attempts should complete through provider webhooks eventually, with `make reconcile-kyc` as the polling fallback.

## Marketplace Decisions

- Currency read endpoints are public.
- Corridor read endpoints require authentication.
- Corridor responses expose currency codes, not internal currency UUIDs.
- Exchange request creation requires an authenticated, active, KYC-verified user.
- Exchange request board reads are distinct from "my requests" reads.
- Request edits are limited to `request_open` records with no historical offers.
- Offer edits are limited to the owner's active offer before request lock.
- Relisting creates a new request from an expired/cancelled request; terminal records are immutable.
- Each request can have at most one direct successor, recorded by
  `relisted_from_request_id`; a successor can itself be relisted once after it
  reaches a terminal state.
- Marketplace and admin list endpoints use `{items, next_cursor}` cursor pagination, ordered by `created_at` and ID. This is an intentional v1 breaking contract change because no frontend/client exists yet; do not add duplicate legacy list endpoints.
- Creation-date filters are inclusive (`created_from` and `created_to`), and
  values are normalized to UTC before comparison. Invalid lower/upper ranges
  are domain validation errors.
- Offer history responses include request status, currency codes, request amount,
  preferred rate, and request expiry, without exposing request-owner private data.
- Request and offer mutations use explicit `SELECT FOR UPDATE` repository
  methods. Request-row locks are acquired before offer-row locks for offer
  edits, withdrawal, rejection, and acceptance. Expiry updates retain terminal
  status predicates so they cannot overwrite accepted or locked transitions.
- An offer PATCH with the existing rate is a no-op: it does not change
  `updated_at` or publish `exchange_offer.updated`.
- Trade acceptance also relies on unique request/offer constraints.
- Expiry jobs use conditional atomic transitions and publish notifications only
  for rows returned as changed by those transitions. SQLite tests are
  deterministic; PostgreSQL multi-worker race testing remains a deployment
  verification task.
- New lifecycle notifications are published through `OutboxEventPublisher` as `exchange_offer.updated` and `exchange_request.relisted`.
- Funding and escrow-leg behavior are intentionally deferred for now.

## Notification And Outbox Decisions

The app uses a database outbox pattern.

- Feature services decide when a business event happened.
- `app/services/outbox.py` owns event names, payload shape, aggregate metadata, and recipient targeting.
- `app/services/notification_dispatcher.py` claims and dispatches pending outbox events.
- `app/integrations/knock.py` owns Knock-specific SDK calls and payload transformation.

Do not scatter new `build_outbox_event(...)` calls through feature services. Add a named method to `OutboxEventPublisher` instead, then call that method from the feature service.

The dispatcher:

- marks successful events delivered
- retries failures with exponential backoff
- marks exhausted failures dead

Knock workflow keys are derived from event type by replacing dots and underscores with hyphens.

Examples:

- `user.email_verification_requested` -> `user-email-verification-requested`
- `exchange_request.created` -> `exchange-request-created`
- `exchange_request.relisted` -> `exchange-request-relisted`
- `exchange_offer.updated` -> `exchange-offer-updated`
- `trade_contract.locked` -> `trade-contract-locked`

Knock rendering data is sent as uppercase top-level variables, such as:

- `USER_NAME`
- `USER_EMAIL`
- `REQUEST_ID`
- `OFFER_ID`
- `TRADE_ID`
- `EXPIRES_AT_DISPLAY`

## Live-Tested Notification Flows

These flows have been tested against the running backend, local DB, Knock, Resend, and Gmail:

- email verification requested
- password reset requested
- password reset completed
- password changed

After the outbox publisher refactor, a live smoke test confirmed:

- `POST /api/v1/auth/forgot-password` queues `user.password_reset_requested`
- `make dispatch-notifications` delivers the event
- the real reset email lands in Gmail

## Integration Boundaries

Keep provider-specific code out of business services.

- Knock SDK usage belongs in `app/integrations/knock.py`.
- Business services should emit outbox events, not call providers directly.
- Notification dispatching should go through the dispatcher/provider abstraction.

## Persistence Rules

- Repositories return domain entities or read models, not live ORM rows.
- Prefer explicit repository methods for business queries.
- Use ORM relationships where already configured, but do not leak ORM models into services.
- Keep migrations and models aligned.
- Add migration tests when schema changes.

## Testing Guidance

Use test depth according to risk:

- API tests for endpoint behavior and Problem Details error shapes.
- Repository tests for persistence contracts and query behavior.
- Migration tests for schema changes.
- Dispatcher/provider tests for outbox and notification integration logic.
- Light live smoke tests are useful after changes touching provider wiring or event payloads.

Do not rely on live Knock/Resend/Gmail tests as the only coverage. They are smoke tests, not repeatable CI coverage.

The default test database is SQLite, which does not provide PostgreSQL row-lock
semantics. Lock query generation is tested deterministically; a real
multi-transaction PostgreSQL race test remains a deployment-environment test
when a safe PostgreSQL test fixture is available.

## Current Product Gaps

Known deferred work:

- live Youverify integration after account access
- funding instructions
- escrow legs
- payment webhooks
- payout/release flows
- ledger accounting
- in-app notifications
- compliance/risk automation
- frontend verification/reset pages

## Working Lessons

- Check the repo path first. This repo moved from Desktop to `~/Code`.
- Keep secrets in `.env`; do not commit API keys.
- For local live email tests, Gmail plus Knock/Resend has been used manually.
- The Resend test domain can only send to the account/domain allowed by Resend until a real sending domain is verified.
- Prefer adding durable docs when chat context starts carrying important project memory.

## Before Finishing A Change

For code changes, normally run:

```bash
make format
make lint
make typecheck
make test
```

If the change affects outbox dispatch or Knock payloads, also consider one narrow live smoke test against the running server.
