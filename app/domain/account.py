"""Provider-neutral account value normalization."""

from __future__ import annotations


def normalize_international_phone(phone: str | None) -> str | None:
    """Normalize a basic international phone representation.

    This validates shape and length only. It does not claim that a number is
    valid for a particular country or currently reachable.
    """
    if phone is None:
        return None

    normalized = "".join(phone.split())
    if not normalized.startswith("+"):
        raise ValueError("Phone must start with '+'.")
    digits = normalized[1:]
    if not digits.isdigit():
        raise ValueError("Phone must contain only digits with an optional leading '+'.")
    if not 7 <= len(digits) <= 15:
        raise ValueError("Phone must contain between 7 and 15 digits.")
    return normalized
