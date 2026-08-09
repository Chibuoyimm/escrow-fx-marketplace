"""Durable, policy-driven API rate limiting."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from app.domain.exceptions import RateLimitExceededError
from app.infrastructure.config import settings
from app.infrastructure.exceptions import InfrastructureError
from app.infrastructure.metrics import observe_rate_limit
from app.services._shared import UnitOfWorkFactory, build_uow, utc_now


class RateLimitFailureMode(StrEnum):
    """Behavior when the persistent limiter cannot access its storage."""

    FAIL_OPEN = "fail_open"
    FAIL_CLOSED = "fail_closed"


@dataclass(frozen=True, slots=True)
class RateLimitRule:
    """One counter dimension within a named policy."""

    name: str
    dimension: str
    limit: int
    window_seconds: int


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    """A named set of counters applied to one endpoint category."""

    name: str
    category: str
    rules: tuple[RateLimitRule, ...]
    failure_mode: RateLimitFailureMode


@dataclass(frozen=True, slots=True)
class RateLimitStatus:
    """Current state of one policy rule."""

    limit: int
    remaining: int
    reset_at: datetime
    blocked: bool


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Headers and enforcement result returned by the limiter."""

    limited: bool
    headers: dict[str, str]
    retry_after: int | None = None
    bypassed_completed_replay: bool = False
    storage_unavailable: bool = False


AUTH_REGISTER = "auth.register"
AUTH_LOGIN = "auth.login"
AUTH_RESEND_VERIFICATION = "auth.resend-verification"
AUTH_FORGOT_PASSWORD = "auth.forgot-password"
AUTH_VERIFY_EMAIL = "auth.verify-email"
AUTH_RESET_PASSWORD = "auth.reset-password"
AUTH_CHANGE_PASSWORD = "auth.change-password"
ACCOUNT_MUTATION = "account.mutation"
ACCOUNT_DEACTIVATE = "account.deactivate"
KYC_SUBMIT = "kyc.submit"
MARKETPLACE_MUTATION = "marketplace.mutation"
ADMIN_MUTATION = "admin.mutation"


_BASE_POLICIES: dict[str, RateLimitPolicy] = {
    AUTH_REGISTER: RateLimitPolicy(
        name=AUTH_REGISTER,
        category="auth",
        rules=(
            RateLimitRule("ip", "ip", 5, 15 * 60),
            RateLimitRule("account", "account", 3, 60 * 60),
        ),
        failure_mode=RateLimitFailureMode.FAIL_CLOSED,
    ),
    AUTH_LOGIN: RateLimitPolicy(
        name=AUTH_LOGIN,
        category="auth",
        rules=(
            RateLimitRule("ip", "ip", 30, 15 * 60),
            RateLimitRule("account", "account", 10, 15 * 60),
        ),
        failure_mode=RateLimitFailureMode.FAIL_CLOSED,
    ),
    AUTH_RESEND_VERIFICATION: RateLimitPolicy(
        name=AUTH_RESEND_VERIFICATION,
        category="auth",
        rules=(
            RateLimitRule("ip", "ip", 10, 60 * 60),
            RateLimitRule("account", "account", 3, 60 * 60),
        ),
        failure_mode=RateLimitFailureMode.FAIL_CLOSED,
    ),
    AUTH_FORGOT_PASSWORD: RateLimitPolicy(
        name=AUTH_FORGOT_PASSWORD,
        category="auth",
        rules=(
            RateLimitRule("ip", "ip", 10, 60 * 60),
            RateLimitRule("account", "account", 3, 60 * 60),
        ),
        failure_mode=RateLimitFailureMode.FAIL_CLOSED,
    ),
    AUTH_VERIFY_EMAIL: RateLimitPolicy(
        name=AUTH_VERIFY_EMAIL,
        category="auth",
        rules=(
            RateLimitRule("ip", "ip", 20, 15 * 60),
            RateLimitRule("token", "token", 5, 15 * 60),
        ),
        failure_mode=RateLimitFailureMode.FAIL_CLOSED,
    ),
    AUTH_RESET_PASSWORD: RateLimitPolicy(
        name=AUTH_RESET_PASSWORD,
        category="auth",
        rules=(
            RateLimitRule("ip", "ip", 10, 15 * 60),
            RateLimitRule("token", "token", 5, 15 * 60),
        ),
        failure_mode=RateLimitFailureMode.FAIL_CLOSED,
    ),
    AUTH_CHANGE_PASSWORD: RateLimitPolicy(
        name=AUTH_CHANGE_PASSWORD,
        category="account",
        rules=(RateLimitRule("user", "user", 5, 60 * 60),),
        failure_mode=RateLimitFailureMode.FAIL_CLOSED,
    ),
    ACCOUNT_MUTATION: RateLimitPolicy(
        name=ACCOUNT_MUTATION,
        category="account",
        rules=(RateLimitRule("user", "user", 20, 15 * 60),),
        failure_mode=RateLimitFailureMode.FAIL_CLOSED,
    ),
    ACCOUNT_DEACTIVATE: RateLimitPolicy(
        name=ACCOUNT_DEACTIVATE,
        category="account",
        rules=(RateLimitRule("user", "user", 3, 60 * 60),),
        failure_mode=RateLimitFailureMode.FAIL_CLOSED,
    ),
    KYC_SUBMIT: RateLimitPolicy(
        name=KYC_SUBMIT,
        category="kyc",
        rules=(RateLimitRule("user", "user", 5, 60 * 60),),
        failure_mode=RateLimitFailureMode.FAIL_CLOSED,
    ),
    MARKETPLACE_MUTATION: RateLimitPolicy(
        name=MARKETPLACE_MUTATION,
        category="marketplace",
        rules=(RateLimitRule("user", "user", 30, 60),),
        failure_mode=RateLimitFailureMode.FAIL_OPEN,
    ),
    ADMIN_MUTATION: RateLimitPolicy(
        name=ADMIN_MUTATION,
        category="account",
        rules=(RateLimitRule("user", "user", 60, 60),),
        failure_mode=RateLimitFailureMode.FAIL_CLOSED,
    ),
}


def get_rate_limit_policy(name: str) -> RateLimitPolicy:
    """Return a named policy with validated environment overrides applied."""
    try:
        policy = _BASE_POLICIES[name]
    except KeyError as exc:
        raise ValueError(f"Unknown rate-limit policy '{name}'.") from exc

    overrides = settings.rate_limit_policy_overrides
    rules: list[RateLimitRule] = []
    for rule in policy.rules:
        override = overrides.get(f"{name}.{rule.name}", {})
        limit = int(override.get("limit", rule.limit))
        window_seconds = int(override.get("window_seconds", rule.window_seconds))
        if limit < 1 or window_seconds < 1:
            raise ValueError(f"Invalid rate-limit override for '{name}.{rule.name}'.")
        rules.append(replace(rule, limit=limit, window_seconds=window_seconds))

    failure_mode = {
        "auth": (
            RateLimitFailureMode.FAIL_CLOSED
            if settings.rate_limit_fail_closed_auth
            else RateLimitFailureMode.FAIL_OPEN
        ),
        "account": (
            RateLimitFailureMode.FAIL_CLOSED
            if settings.rate_limit_fail_closed_account
            else RateLimitFailureMode.FAIL_OPEN
        ),
        "kyc": (
            RateLimitFailureMode.FAIL_CLOSED
            if settings.rate_limit_fail_closed_kyc
            else RateLimitFailureMode.FAIL_OPEN
        ),
        "marketplace": (
            RateLimitFailureMode.FAIL_CLOSED
            if settings.rate_limit_fail_closed_marketplace
            else RateLimitFailureMode.FAIL_OPEN
        ),
    }[policy.category]
    return replace(policy, rules=tuple(rules), failure_mode=failure_mode)


def hash_rate_limit_key(value: str) -> str:
    """HMAC a transient identity key before it reaches persistence."""
    secret = settings.rate_limit_key_secret or settings.jwt_secret_key
    return hmac.new(
        secret.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _window(now: datetime, seconds: int) -> tuple[datetime, datetime]:
    timestamp = now.timestamp()
    start = datetime.fromtimestamp(math.floor(timestamp / seconds) * seconds, tz=UTC)
    return start, start + timedelta(seconds=seconds)


def _status_headers(
    policy: RateLimitPolicy,
    statuses: list[RateLimitStatus],
    *,
    now: datetime,
) -> tuple[dict[str, str], int | None]:
    if not statuses:
        return {}, None
    remaining = min(status.remaining for status in statuses)
    reset_seconds = max(
        (math.ceil(max(status.reset_at - now, timedelta()).total_seconds()) for status in statuses),
        default=0,
    )
    blocked = [status for status in statuses if status.blocked]
    retry_after = None
    if blocked:
        retry_after = max(
            1,
            max(
                (
                    math.ceil(max(status.reset_at - now, timedelta()).total_seconds())
                    for status in blocked
                ),
                default=0,
            ),
        )
        reset_seconds = retry_after
    headers = {
        "RateLimit-Limit": str(min(status.limit for status in statuses)),
        "RateLimit-Remaining": str(remaining),
        "RateLimit-Reset": str(reset_seconds),
        "RateLimit-Policy": ", ".join(
            f"{rule.limit};w={rule.window_seconds}" for rule in policy.rules
        ),
    }
    return headers, retry_after


class RateLimitService:
    """Application-facing durable limiter."""

    def __init__(self, uow_factory: UnitOfWorkFactory | None = None) -> None:
        self._uow_factory = uow_factory or build_uow

    async def enforce(
        self,
        *,
        policy_name: str,
        identities: Mapping[str, str],
        principal_user_id: str | None = None,
        idempotency_scope: str | None = None,
        idempotency_key_hash: str | None = None,
        idempotency_request_fingerprint: str | None = None,
    ) -> RateLimitDecision:
        """Consume all applicable dimensions and raise a stable 429 when blocked."""
        policy = get_rate_limit_policy(policy_name)
        if not settings.rate_limit_enabled:
            observe_rate_limit(policy.name, "disabled")
            return RateLimitDecision(limited=False, headers={})

        now = utc_now()
        try:
            async with self._uow_factory() as uow:
                bypass_replay = False
                if (
                    principal_user_id is not None
                    and idempotency_scope is not None
                    and idempotency_key_hash is not None
                    and idempotency_request_fingerprint is not None
                ):
                    from uuid import UUID

                    replay = await uow.idempotency_records.get_completed(
                        principal_user_id=UUID(principal_user_id),
                        operation_scope=idempotency_scope,
                        key_hash=idempotency_key_hash,
                        request_fingerprint=idempotency_request_fingerprint,
                        now=now,
                    )
                    bypass_replay = replay is not None

                statuses: list[RateLimitStatus] = []
                for rule in policy.rules:
                    identity = identities.get(rule.dimension)
                    if identity is None:
                        continue
                    window_started_at, expires_at = _window(now, rule.window_seconds)
                    key_hash = hash_rate_limit_key(f"{policy.name}:{rule.name}:{identity}")
                    if bypass_replay:
                        bucket = await uow.rate_limits.get(
                            policy_name=policy.name,
                            key_hash=key_hash,
                            window_started_at=window_started_at,
                            now=now,
                        )
                    else:
                        bucket = await uow.rate_limits.consume(
                            policy_name=policy.name,
                            key_hash=key_hash,
                            window_started_at=window_started_at,
                            expires_at=expires_at,
                            limit=rule.limit,
                            now=now,
                        )
                    count = bucket.request_count if bucket is not None else 0
                    statuses.append(
                        RateLimitStatus(
                            limit=rule.limit,
                            remaining=max(rule.limit - count, 0),
                            reset_at=expires_at,
                            blocked=count > rule.limit,
                        )
                    )
                headers, retry_after = _status_headers(policy, statuses, now=now)
                if not bypass_replay:
                    await uow.commit()
                decision = RateLimitDecision(
                    limited=retry_after is not None and not bypass_replay,
                    headers=headers,
                    retry_after=retry_after if not bypass_replay else None,
                    bypassed_completed_replay=bypass_replay,
                )
                observe_rate_limit(
                    policy.name,
                    "rejected" if decision.limited else "allowed",
                )
                return decision
        except Exception as exc:
            # The limiter is an infrastructure boundary: do not expose storage
            # details, and do not let an unexpected store failure bypass the
            # configured policy decision.
            if policy.failure_mode is RateLimitFailureMode.FAIL_OPEN:
                observe_rate_limit(policy.name, "storage_unavailable")
                return RateLimitDecision(
                    limited=False,
                    headers={},
                    storage_unavailable=True,
                )
            observe_rate_limit(policy.name, "storage_unavailable")
            raise InfrastructureError(
                title="Rate Limiting Unavailable",
                detail="The rate-limit store could not be reached.",
            ) from exc

    async def enforce_or_raise(
        self,
        *,
        policy_name: str,
        identities: Mapping[str, str],
        principal_user_id: str | None = None,
        idempotency_scope: str | None = None,
        idempotency_key_hash: str | None = None,
        idempotency_request_fingerprint: str | None = None,
    ) -> RateLimitDecision:
        """Enforce a policy and convert exhaustion to the API's 429 error."""
        decision = await self.enforce(
            policy_name=policy_name,
            identities=identities,
            principal_user_id=principal_user_id,
            idempotency_scope=idempotency_scope,
            idempotency_key_hash=idempotency_key_hash,
            idempotency_request_fingerprint=idempotency_request_fingerprint,
        )
        if decision.limited:
            raise RateLimitExceededError(
                retry_after=decision.retry_after or 1,
                headers=decision.headers,
            )
        return decision


def resolve_client_ip(direct_peer: str | None, forwarded_for: str | None) -> str:
    """Resolve a client address without trusting forwarding headers by default."""
    peer = _parse_ip(direct_peer) or "unknown"
    trusted_networks = _trusted_networks()
    if not trusted_networks or not _is_trusted(peer, trusted_networks):
        return peer

    forwarded = []
    if forwarded_for:
        forwarded = [candidate.strip() for candidate in forwarded_for.split(",")]
    for candidate in reversed(forwarded):
        parsed = _parse_ip(candidate)
        if parsed is not None and not _is_trusted(parsed, trusted_networks):
            return parsed
    forwarded_peer = _parse_ip(forwarded[0]) if forwarded else None
    return forwarded_peer or peer


def _trusted_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for value in settings.trusted_proxy_networks.split(","):
        value = value.strip()
        if not value:
            continue
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError as exc:
            raise ValueError(f"Invalid trusted proxy network '{value}'.") from exc
    return tuple(networks)


def _parse_ip(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def _is_trusted(
    value: str,
    networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...],
) -> bool:
    address = ipaddress.ip_address(value)
    return any(address in network for network in networks)


def get_rate_limit_service() -> RateLimitService:
    """Build the configured rate-limit service."""
    return RateLimitService()
