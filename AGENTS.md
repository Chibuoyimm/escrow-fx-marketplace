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

The provider-independent observability and operational-hardening milestone is
implemented. Near-term backend priorities are now the deferred integration and
deployment work:

- integrate Youverify after account access confirms exact products and pricing
- design payment/escrow infrastructure, ledgers, funding, and payout rails
- prepare deployment configuration, secret management, and scheduler topology
- keep this file updated when a decision becomes project guidance rather than a one-off implementation detail

Funding, escrow legs, ledgers, payout rails, and payment webhooks remain deferred until explicitly picked up.

## Observability Decisions

- `app/infrastructure/application_logging.py` owns logging configuration, safe field allowlisting, and the request correlation context. Do not add route-specific logging wrappers or log arbitrary exception messages.
- The application request middleware is the canonical access log; `uvicorn.access` is intentionally disabled because its default records expose client IPs and raw request targets.
- `app/infrastructure/request_context.py` is the one HTTP middleware. It validates or generates `X-Request-ID`, emits one completion event, and records HTTP metrics. Do not add a second correlation or metrics middleware.
- `app/infrastructure/metrics.py` owns one process-wide Prometheus registry and all metric collectors. Add instrumentation at shared boundaries, not in every route.
- `app/infrastructure/health.py` owns the read-only readiness probe. `/api/v1/health` remains backward compatible; `/api/v1/health/live` is dependency-free and `/api/v1/health/ready` checks PostgreSQL with a bounded timeout.
- `app/infrastructure/jobs.py` owns scheduled-command lifecycle logging and failure exit handling. One-shot job metrics are intentionally not registered because they are not visible from the API process; feature jobs should provide only their operation and human-readable success output.
- Fixed-cardinality labels are required. Never label metrics or logs with request IDs, user IDs, email addresses, tokens, raw paths, query strings, KYC identifiers, or provider/database error text.
- `/metrics` is process-local and currently covers HTTP and rate-limit metrics. Multi-worker aggregation requires Prometheus multiprocess configuration; do not add Pushgateway or vendor telemetry code without a separate decision. Unexpected exception logs include only a safe exception type and correlation ID, never tracebacks or exception details.
- No Sentry, OpenTelemetry, Redis, Kafka, or vendor observability SDK is part of this milestone. JSON logs and Prometheus exposition are the integration boundaries.

## Account Management Decisions

- `PATCH /api/v1/users/me` currently permits only `phone`. Formatting whitespace is removed, then provider-neutral validation requires `+` followed by 7-15 digits; email and country are immutable.
- `POST /api/v1/users/me/deactivate` soft-deactivates the account after current-password verification. It is blocked by an owned non-expired open/pending request, an owned non-expired active offer, or participation in any trade other than `settled`/`cancelled`. It preserves all related records and invalidates the bearer token immediately. Repeating the call with that token returns `401`; self-reactivation is not supported.
- `PATCH /api/v1/admin/users/{user_id}/status` is admin-only for suspension/reactivation. Service-layer authorization re-reads and locks both actor and subject in UUID order. Operations users can inspect and review but cannot change account status. Administrators cannot change their own status.
- Emergency suspension remains available despite marketplace obligations and does not cancel them. Board queries hide non-active owners, offer creation requires an active request owner, offer acceptance requires an active offer owner, and expiry processing still transitions due records.
- Account and marketplace mutations use one canonical row-lock order: all participant users sorted by UUID, then the exchange request, then exchange offers sorted by UUID. When participant IDs require discovery, perform non-locking reads first, acquire locks in canonical order, and validate immutable relationships after locking. Never acquire a request or offer lock before a participant user lock.
- Current database status is checked during login and bearer-token authentication, so suspension/deactivation invalidates existing tokens without JWT revocation state.
- Security-sensitive account changes are recorded in the append-only `account_audit_events` table and published through `OutboxEventPublisher` atomically with the user mutation. Audit metadata excludes passwords and profile values. The repository exposes only append/read operations, ORM instance updates/deletes are rejected, and production application credentials should lack `UPDATE`/`DELETE` privileges on this table.
- Notification preferences are backed by Knock's `default` preference set; do not
  add a local preferences table or outbox events for preference reads/updates.
- `GET` and `PATCH /api/v1/users/me/notification-preferences` expose only the
  provider-neutral categories `security`, `kyc`, `trade`, and `marketplace`.
- Only `marketplace.email_enabled` is mutable. Security, KYC, and trade
  workflows are mandatory and must use Knock's Override recipient preferences
  setting. The hosted preference center must expose only marketplace settings.
- Knock preference updates must use `_persistence_strategy="merge"`; replacing
  a complete preference set could erase unrelated provider configuration.
- A successful dispatcher result means Knock accepted the workflow trigger. It
  does not prove that an email was delivered or reached Gmail.

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
make cleanup-idempotency
make cleanup-rate-limits
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

## Marketplace Idempotency Decisions

- Authenticated non-repeatable marketplace POST mutations accept an optional
  `Idempotency-Key` header: request creation, request cancellation/relisting,
  offer creation/withdrawal/rejection, and offer acceptance/trade locking.
- Keys are validated as 1-128 ASCII letters, digits, `.`, `_`, `~`, or `-`.
  The database stores only a SHA-256 key hash and canonical request fingerprint;
  it never stores the raw header, authorization header, passwords, or KYC data.
- A key is scoped by authenticated principal, operation/resource scope, and key
  hash. Reusing it with a different canonical payload returns a `409` Problem
  Details conflict. A completed duplicate returns the original status and JSON
  response without rerunning domain mutations or outbox publication.
- Relist fingerprints preserve optional-field presence because omitted fields
  inherit the original request value while explicit `null` clears a nullable
  field; those requests are not semantically interchangeable.
- Claims, business mutations, outbox events, and completed response bodies are
  committed in the same UoW transaction. The repository uses a savepoint around
  the unique insert, so PostgreSQL concurrent duplicates wait on the winner and
  replay after its commit; a rolled-back winner leaves no poisoned key.
- Durable `processing` claims return `409` with `Retry-After: 1` until their
  timeout; after that, a retry can reclaim them and expiry cleanup removes any
  abandoned row. Completed records are retained for
  `APP_IDEMPOTENCY_RETENTION_HOURS` (24 hours by default).
- Expected validation, authorization, business-rule, and unexpected exceptions
  roll back the claim with the surrounding UoW, so a client may retry the same
  key. A claim that was explicitly committed without completion is treated as
  in progress until its timeout and then removed by cleanup.
- Run `make cleanup-idempotency` from a scheduler to remove expired records.
  The command deletes at most `APP_IDEMPOTENCY_CLEANUP_BATCH_SIZE` rows per run.
- PATCH mutations and auth/KYC operations are intentionally outside this first
  milestone. They either already have idempotent assignment semantics or need
  separate token/security replay contracts.

## API Rate-Limiting Decisions

- High-risk auth/account endpoints, KYC submission, authenticated marketplace
  mutations, and admin state/review mutations use named policies from
  `app/infrastructure/rate_limiting.py`. Health checks and ordinary reads are
  intentionally not rate limited.
- Counters are durable PostgreSQL rows in `rate_limit_buckets`. The repository
  uses dialect-native `INSERT ... ON CONFLICT` upserts and caps overflow at
  `limit + 1`, so concurrent API instances cannot allow more than the policy
  limit and counters do not grow without bound.
- Policy keys are HMAC-SHA256 values of transient policy/dimension/identity
  data, keyed by `APP_RATE_LIMIT_KEY_SECRET` or the development fallback
  `APP_JWT_SECRET_KEY`. Production should set a distinct strong rate-limit
  secret. Never persist raw emails, tokens, authorization headers, passwords, KYC
  identifiers, or other secrets in limiter keys. Public auth uses normalized
  account identifiers plus client IP where available; authenticated actions
  use the current user ID.
- The direct peer address is trusted by default. Only explicitly configured
  IPs/CIDRs in `APP_TRUSTED_PROXY_NETWORKS` may supply a meaningful
  `X-Forwarded-For` chain. Do not add blind forwarding-header trust.
- Auth/account/KYC/admin limiter storage failures fail closed with a sanitized
  `503`; marketplace limiter storage failures fail open by default to favor
  availability. Change this only deliberately through the category settings.
- `429` responses use Problem Details with error code `rate_limited`, a
  correct `Retry-After`, and `RateLimit-*` headers. Failed authentication and
  malformed auth requests consume their applicable public limits.
- Completed idempotency replays are checked before consuming marketplace
  capacity only when the authenticated user, operation/resource scope, key hash,
  and exact canonical request fingerprint all match. A changed payload remains
  rate limited and then follows normal idempotency conflict handling; a new key
  remains rate limited.
- KYC's limiter is additional abuse protection and must not replace the
  existing one-minute cooldown and rolling attempt-window business rules.
- Run `make cleanup-rate-limits` from a scheduler to delete expired counters in
  bounded batches. Configure limits/windows with
  `APP_RATE_LIMIT_POLICY_OVERRIDES`; disable the feature only for controlled
  local/test environments with `APP_RATE_LIMIT_ENABLED`.

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

The centralized workflow-category checklist is in
`app/services/notification_categories.py`. It maps security events (including
profile and account-status changes) to `security`, all `user.kyc.*` events to
`kyc`, exchange request/offer activity except accepted offers to `marketplace`,
accepted offers and trade-contract events to `trade`, and
`marketplace_expiry.completed` to `none`. Knock workflow categories and
mandatory preference overrides are configured manually in the Knock dashboard;
the application does not pretend to configure committed dashboard workflows.

Knock workflow keys are derived from event type by replacing dots and underscores with hyphens.

Examples:

- `user.email_verification_requested` -> `user-email-verification-requested`
- `exchange_request.created` -> `exchange-request-created`
- `exchange_request.relisted` -> `exchange-request-relisted`
- `exchange_offer.updated` -> `exchange-offer-updated`
- `trade_contract.locked` -> `trade-contract-locked`
- `user.profile_updated` -> `user-profile-updated`
- `user.account_deactivated` -> `user-account-deactivated`
- `user.account_suspended` -> `user-account-suspended`
- `user.account_reactivated` -> `user-account-reactivated`

Create and commit all required Knock workflows before enabling their producing
business paths in an environment.

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
semantics. Lock query generation is tested deterministically. Idempotency
replay behavior is covered with SQLite API/repository tests, and the opt-in
PostgreSQL suite includes a concurrent duplicate request race that asserts one
marketplace row and one outbox event.

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
