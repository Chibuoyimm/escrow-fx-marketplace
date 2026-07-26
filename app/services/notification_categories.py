"""Central workflow-category map for Knock configuration and review."""

from __future__ import annotations

from types import MappingProxyType

from app.domain.exceptions import InvariantViolationError

# Keep this explicit list aligned with OutboxEventPublisher and the workflows
# committed in Knock. Dashboard category/override settings remain manual.
WORKFLOW_CATEGORY_MAP = MappingProxyType(
    {
        "user.email_verification_requested": "security",
        "user.password_reset_requested": "security",
        "user.password_reset_completed": "security",
        "user.password_changed": "security",
        "user.profile_updated": "security",
        "user.account_deactivated": "security",
        "user.account_suspended": "security",
        "user.account_reactivated": "security",
        "user.kyc_submitted": "kyc",
        "user.kyc_verified": "kyc",
        "user.kyc_requires_review": "kyc",
        "user.kyc_rejected": "kyc",
        "exchange_request.created": "marketplace",
        "exchange_request.cancelled": "marketplace",
        "exchange_request.expired": "marketplace",
        "exchange_request.reopened": "marketplace",
        "exchange_request.relisted": "marketplace",
        "exchange_offer.created": "marketplace",
        "exchange_offer.updated": "marketplace",
        "exchange_offer.withdrawn": "marketplace",
        "exchange_offer.rejected": "marketplace",
        "exchange_offer.expired": "marketplace",
        "exchange_offer.accepted": "trade",
        "trade_contract.locked": "trade",
        "trade_contract.cancelled": "trade",
        "marketplace_expiry.completed": "none",
    }
)


def workflow_category(event_type: str) -> str:
    """Return the configured Knock category for an outbox event type."""
    try:
        return WORKFLOW_CATEGORY_MAP[event_type]
    except KeyError as exc:
        raise InvariantViolationError(
            f"No notification category is configured for event '{event_type}'."
        ) from exc
