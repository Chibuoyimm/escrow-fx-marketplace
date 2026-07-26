"""Marketplace lifecycle predicates shared by application services."""

from __future__ import annotations

from app.domain.enums import ExchangeOfferStatus, ExchangeRequestStatus

REQUEST_BOARD_STATUSES = frozenset(
    {ExchangeRequestStatus.REQUEST_OPEN, ExchangeRequestStatus.OFFER_PENDING}
)
REQUEST_EDITABLE_STATUSES = frozenset({ExchangeRequestStatus.REQUEST_OPEN})
REQUEST_CANCELLABLE_STATUSES = REQUEST_BOARD_STATUSES
REQUEST_RELISTABLE_STATUSES = frozenset(
    {ExchangeRequestStatus.CANCELLED, ExchangeRequestStatus.EXPIRED}
)
OFFER_ACTIVE_STATUSES = frozenset({ExchangeOfferStatus.ACTIVE})


def request_can_accept_offers(status: ExchangeRequestStatus) -> bool:
    """Return whether a request may receive or accept an offer."""
    return status in REQUEST_BOARD_STATUSES


def request_can_be_edited(status: ExchangeRequestStatus) -> bool:
    """Return whether marketplace terms may be edited."""
    return status in REQUEST_EDITABLE_STATUSES


def request_can_be_cancelled(status: ExchangeRequestStatus) -> bool:
    """Return whether a request may be cancelled by its owner."""
    return status in REQUEST_CANCELLABLE_STATUSES


def offer_is_active(status: ExchangeOfferStatus) -> bool:
    """Return whether an offer may be changed or accepted."""
    return status in OFFER_ACTIVE_STATUSES
