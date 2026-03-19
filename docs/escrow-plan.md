# Escrow FX Marketplace MVP Plan (Currency-Agnostic, CAD/USD/GBP/NGN First, Partner-Custody, FastAPI)

## Summary
Build a two-sided escrow exchange marketplace where:
1. User A posts an exchange request (e.g., USD→NGN, CAD→GBP, etc.) with amount and preferred rate.
2. User B accepts or counter-offers.
3. Once terms are locked, both users fund escrow legs into partner-controlled custody accounts.
4. Funds release only after both legs are confirmed funded.
5. If second leg misses SLA, auto-cancel and refund funded leg.
6. Users receive in-app + email notifications for all key state transitions.

This keeps the original dual-leg P2P escrow model, but generalizes it via config-driven currencies and corridor rules.

## Internet-Backed Design Anchors
1. Reference-rate transparency:
Use a benchmark source and clearly separate benchmark from executable/agreed rate (example source for NGN visibility: [CBN Rates](https://www.cbn.gov.ng/rates/exrate.asp)).
2. Funding confirmation pattern:
Webhook ingestion + direct provider verification call (e.g., [Paystack Transaction API](https://paystack.com/docs/api/transaction/), [Flutterwave verify transaction](https://developer.flutterwave.com/reference/verify-transaction-with-id)).
3. Idempotency:
All external create/update calls must use idempotency keys ([Stripe idempotent requests](https://docs.stripe.com/api/idempotent_requests)).
4. Quote lock/expiry:
If external FX quotes are used for settlement, enforce expiry semantics ([Wise quote docs](https://docs.wise.com/api-reference/quote)).
5. US remittance baseline:
Include required disclosure concepts in UX and receipts ([CFPB Reg E remittance](https://www.consumerfinance.gov/rules-policy/regulations/1005/)).
6. US AML baseline:
Design around MSB/AML expectations ([FinCEN MSB registration](https://www.fincen.gov/money-services-business-msb-registration)).
7. Sanctions screening:
Support sanctions checks and list updates ([OFAC search](https://sanctionssearch.ofac.treas.gov/)).

## Target Architecture
1. `api-gateway` (FastAPI):
Auth, onboarding, request/offer APIs, escrow APIs, admin APIs, idempotency middleware.
2. `matching-service`:
Request board, accept/counter-offer, lock terms, expiration handling.
3. `escrow-service`:
Dual-leg orchestration, state machine, release/refund logic.
4. `ledger-service`:
Double-entry postings, immutable journals, balance snapshots.
5. `payments-adapter`:
Inbound funding rails by corridor/currency; webhook signature + verification.
6. `payout-adapter`:
Outbound disbursements with priority fallback routing.
7. `rate-service`:
Provider-aggregated reference rates, caching, snapshots for audit.
8. `currency-registry-service`:
Config-driven active currencies and precision/min-max rules.
9. `corridor-config-service`:
Allowed pairs, fee model, SLA timers, rail priority lists.
10. `routing-engine`:
Deterministic rule-based rail selection with fallback order.
11. `notification-service`:
In-app + email fanout with templates and delivery tracking.
12. `risk-compliance-service`:
KYC checks, sanctions hooks, velocity/risk flags, manual-review triggers.
13. `admin-console`:
Disputes, exceptions, reconciliation, webhook replay.
14. `event-bus`:
Kafka/RabbitMQ topics: `trade.created`, `offer.accepted`, `leg.funded`, `escrow.released`, `escrow.refunded`, `notification.requested`.
15. `datastores`:
PostgreSQL (OLTP + ledger), Redis (locks/idempotency/cache), object storage (KYC/audit docs).

## Core Domain Model
1. `users`:
`id`, `role`, `kyc_status`, `country`, `email`, `phone`, `risk_level`, timestamps.
2. `currencies`:
`code`, `minor_unit`, `status`, `min_amount`, `max_amount`.
3. `corridors`:
`id`, `from_currency`, `to_currency`, `status`, `funding_sla_minutes`, `fee_model_id`.
4. `corridor_rails`:
`corridor_id`, `flow_type`, `priority_order`, `provider`, `method`, `status`.
5. `exchange_requests`:
`id`, `creator_user_id`, `from_currency`, `to_currency`, `from_amount`, `preferred_rate`, `min_rate`, `status`, `expires_at`.
6. `exchange_offers`:
`id`, `request_id`, `offer_user_id`, `offered_rate`, `status`, `expires_at`.
7. `trade_contracts`:
`id`, `request_id`, `accepted_offer_id`, `agreed_rate`, `reference_rate_snapshot`, `from_amount`, `to_amount`, `funding_deadline_at`, `status`.
8. `escrow_legs`:
`id`, `trade_contract_id`, `user_id`, `currency`, `amount`, `rail`, `funding_status`, `external_tx_ref`, `funded_at`.
9. `ledger_accounts`:
User wallets, escrow custody, fees, payable/receivable accounts.
10. `ledger_entries`:
`id`, `journal_id`, `debit_account_id`, `credit_account_id`, `amount`, `currency`, `reference_type`, `reference_id`, `posted_at`.
11. `payouts`:
`id`, `trade_contract_id`, `beneficiary_user_id`, `currency`, `amount`, `rail`, `status`, `external_ref`.
12. `notifications`:
`id`, `user_id`, `channel`, `event_type`, `payload`, `delivery_status`, `sent_at`.
13. `audit_events`:
Immutable state/action history.
14. `reference_rates`:
`base_currency`, `quote_currency`, `rate`, `source`, `as_of_date`.

## Trade State Machine (Decision Complete)
1. `REQUEST_OPEN`:
Exchange request is published on the marketplace board and available for eligible counterparties to view and act on.
2. `OFFER_PENDING`:
One or more offers/counter-rates are active on the request, and the requester is evaluating options before locking terms.
3. `TERMS_LOCKED`:
Requester accepts one offer; a binding `trade_contract` is created with agreed rate, amounts, deadlines, and policy snapshots.
4. `AWAITING_DUAL_FUNDING`:
Both parties receive escrow funding instructions and must fund their respective legs within the configured SLA window.
5. `ONE_LEG_FUNDED`:
One escrow leg is confirmed funded while the other is still pending; reminders and countdown logic remain active.
6. `DUAL_FUNDED`:
Both escrow legs are verified funded and the trade is ready for settlement; release job is enqueued automatically.
7. `RELEASING`:
Payouts are being executed to each beneficiary through corridor-defined payout rails with fallback handling.
8. `SETTLED`:
Both payouts completed successfully, ledger entries finalized, and trade is closed as successful.
9. `EXPIRED_REFUNDING`:
Funding deadline was missed before dual funding; any funded leg is moved into refund flow per policy.
10. `CANCELLED`:
Trade/request was cancelled before settlement (user/admin/system policy path), with no successful release.
11. `DISPUTED`:
Trade moved to exception handling due to mismatch, complaint, or risk trigger; admin/manual workflow required.

## End-to-End Flow
1. User posts request for any enabled corridor.
2. Marketplace notifies eligible counterparties.
3. Counterparty accepts/counters rate.
4. Creator accepts one offer; terms lock.
5. System snapshots reference rate + source for audit.
6. Funding instructions issued for both legs.
7. Webhooks + verification confirm inbound funding.
8. Dual-funded trade auto-triggers release.
9. Payout adapter sends each side to beneficiary via routed rail.
10. Ledger finalizes settlement + fees.
11. Notifications sent at each transition.
12. Timeout before dual funding triggers auto-refund and closure.

## Public API Surface (MVP)
1. `POST /api/v1/auth/register`
2. `POST /api/v1/auth/login`
3. `POST /api/v1/auth/refresh`
4. `POST /api/v1/auth/mfa/verify`
5. `GET /api/v1/users/me`
6. `POST /api/v1/kyc/start`
7. `POST /api/v1/kyc/documents`
8. `GET /api/v1/kyc/status`
9. `GET /api/v1/currencies`
10. `GET /api/v1/corridors`
11. `POST /api/v1/exchange-requests`
12. `GET /api/v1/exchange-requests?pair=&status=`
13. `POST /api/v1/exchange-requests/{id}/offers`
14. `POST /api/v1/offers/{id}/accept`
15. `GET /api/v1/trades/{tradeId}`
16. `POST /api/v1/trades/{tradeId}/funding-instructions`
17. `GET /api/v1/trades/{tradeId}/timeline`
18. `POST /api/v1/trades/{tradeId}/cancel`
19. `GET /api/v1/rates/reference?base=&quote=&date=YYYY-MM-DD`
20. `POST /api/v1/webhooks/payments/{provider}`
21. `POST /api/v1/webhooks/payouts/{provider}`
22. `GET /api/v1/notifications`
23. `POST /api/v1/admin/trades/{tradeId}/override`
24. `GET /api/v1/admin/reconciliation?from=&to=`

## Interfaces/Types That Must Be Explicit
1. `TradeContract`:
Agreed rate, benchmark snapshot, fee model, deadline, release policy.
2. `FundingConfirmation`:
Provider, tx ref, amount, currency, signature status, risk flags.
3. `ReleaseDecision`:
`AUTO_RELEASE`, `AUTO_REFUND_TIMEOUT`, `MANUAL_REVIEW`.
4. `NotificationEvent`:
Event type, target, channels, template vars, dedupe key.
5. `LedgerJournal`:
Atomic grouped entries where `sum(debits)==sum(credits)`.
6. `RoutingDecision`:
Chosen rail, fallback sequence, reason code.
7. `CorridorPolicySnapshot`:
Frozen SLA/fees/routing config at lock time.

## Compliance and Risk Controls
1. Block trade creation for non-verified KYC users.
2. Sanctions checks at onboarding and pre-release.
3. Velocity/amount thresholds route high-risk trades to review.
4. Immutable audit trail for offer/rate/funding/release/refund actions.
5. Lock-time disclosures: agreed vs reference rate, fees, receive amount, cancellation window.
6. Suspicious behavior flags: repeated failed funding, identity mismatch, unusual account changes.

## Notification Plan (In-App + Email)
1. `REQUEST_POSTED`:
Sent to eligible counterparties when a new exchange request is published in their supported corridor.
2. `NEW_OFFER_RECEIVED`:
Sent to requester when another user accepts/counters with a proposed rate.
3. `OFFER_ACCEPTED_TERMS_LOCKED`:
Sent to both parties when one offer is accepted and the trade contract is formally locked.
4. `FUNDING_INSTRUCTIONS_READY`:
Sent to both parties with funding destination, reference code, amount, currency, and deadline.
5. `ONE_LEG_FUNDED_WAITING_COUNTERPARTY`:
Sent when one side has funded and the system is waiting for the second leg.
6. `DUAL_FUNDING_CONFIRMED_RELEASING`:
Sent when both legs are confirmed and payout release has started.
7. `PAYOUT_COMPLETED`:
Sent to each beneficiary when their outbound transfer is confirmed successful.
8. `TRADE_EXPIRED_REFUND_INITIATED`:
Sent when deadline is missed and refund flow begins for funded side.
9. `DISPUTE_OPENED` / `DISPUTE_RESOLVED`:
Sent on exception creation and final resolution outcome.

## Implementation Phases
1. Phase 0: Foundations
FastAPI skeleton, auth, RBAC, schemas, Redis, event bus, observability.
2. Phase 1: Currency/Corridor Config
`currencies`, `corridors`, precision and limits, routing policies.
3. Phase 2: Marketplace + Contracting
Request board, offers/counters, acceptance, benchmark snapshot, contract lock.
4. Phase 3: Dual-Funding Escrow
Funding instructions, webhooks, verification, timeout/refund jobs.
5. Phase 4: Auto Release + Payout
Release orchestrator, payout adapters, fallback routing, settlement posting.
6. Phase 5: Notifications + Admin
Template engine, in-app center, review queue, reconciliation reports.
7. Phase 6: Risk/Compliance Hardening
Sanctions hooks, velocity rules, enhanced review, audit export.
8. Phase 7: Pilot Readiness
E2E corridor tests (initial set includes USD, NGN, CAD, GBP), runbooks, incident playbooks, SLA dashboards.

## Testing and Acceptance Criteria
1. Unit:
State transitions, fee math, timeout logic, idempotency behavior.
2. Contract:
Webhook signature checks and provider verification adapters.
3. Integration:
Dual-funded happy path, one-leg timeout refund, payout retry/recovery.
4. Ledger integrity:
All workflows preserve double-entry invariants.
5. Concurrency:
Competing offer-accept attempts on same request.
6. Security:
Admin authz, replay-attack blocking, webhook tamper rejection.
7. UAT:
Post request, negotiate rate, fund both legs, auto-release, refund timeout path.
8. Non-functional:
P95 API latency, queue lag, notification SLO, reconciliation completeness.
9. Go-live gates:
No unresolved P1s, provider certification passed, compliance sign-off complete.

## Assumptions and Defaults
1. No local sample project code found in `/Users/chibuoyim/Documents/New project`; plan is based on your requirements plus external research.
2. Backend stack is Python + FastAPI.
3. Custody model is partner-led for MVP.
4. Matching UX is request board + counter-offers.
5. Timeout policy is auto-cancel + refund.
6. Notifications are in-app + email in MVP.
7. Initial enabled currencies are CAD, USD, GBP, NGN.
8. Additional currencies are enabled via config and provider onboarding, not redesign.
9. “Standard rate” is informational only; execution uses user-agreed rate.
