"""Application service for Knock-backed notification preferences."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from app.domain.entities import User
from app.domain.exceptions import InvariantViolationError
from app.schemas.notification_preferences import (
    NotificationCategory,
    NotificationPreferencesPatch,
    NotificationPreferencesResponse,
)
from app.services._shared import UnitOfWorkFactory, build_uow

PREFERENCE_SET_ID = "default"
MANDATORY_CATEGORIES = frozenset(
    {
        NotificationCategory.SECURITY.value,
        NotificationCategory.KYC.value,
        NotificationCategory.TRADE.value,
    }
)
KNOWN_CATEGORIES = (
    NotificationCategory.SECURITY.value,
    NotificationCategory.KYC.value,
    NotificationCategory.TRADE.value,
    NotificationCategory.MARKETPLACE.value,
)


@dataclass(frozen=True, slots=True)
class NotificationPreferenceState:
    """Provider-neutral effective preference state."""

    preference_set_id: str
    email_enabled_by_category: dict[str, bool]


class NotificationPreferenceGateway(Protocol):
    """Provider-neutral gateway for immediate preference operations."""

    async def upsert_recipient(self, user: User, *, idempotency_key: str) -> None:
        """Create or update the provider recipient."""

    async def get_preferences(
        self, user_id: UUID, *, preference_set_id: str
    ) -> NotificationPreferenceState | None:
        """Read a user preference set, or return None when it does not exist."""

    async def set_preferences(
        self,
        user_id: UUID,
        *,
        preference_set_id: str,
        email_enabled_by_category: dict[str, bool],
        idempotency_key: str,
    ) -> NotificationPreferenceState:
        """Merge a partial category preference update."""


def _default_categories() -> dict[str, bool]:
    """Return Knock's effective default for all public categories."""
    return dict.fromkeys(KNOWN_CATEGORIES, True)


def _recipient_upsert_idempotency_key(user: User) -> str:
    """Scope recipient identification to the current persisted user version."""
    return f"preferences:{user.id}:upsert:{user.updated_at.isoformat()}"


class NotificationPreferenceService:
    """Read and update the current user's provider-backed preferences."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory | None = None,
        gateway: NotificationPreferenceGateway,
    ) -> None:
        self._uow_factory = uow_factory or build_uow
        self._gateway = gateway

    async def get(self, *, user_id: UUID) -> NotificationPreferencesResponse:
        """Return all known categories with enabled-by-default fallback."""
        user = await self._get_user(user_id)
        await self._gateway.upsert_recipient(
            user,
            idempotency_key=_recipient_upsert_idempotency_key(user),
        )
        state = await self._gateway.get_preferences(
            user.id,
            preference_set_id=PREFERENCE_SET_ID,
        )
        return self._response(state)

    async def update(
        self,
        *,
        user_id: UUID,
        payload: NotificationPreferencesPatch,
    ) -> NotificationPreferencesResponse:
        """Merge a permitted preference update and return effective state."""
        updates = {
            category.value: value.email_enabled for category, value in payload.categories.items()
        }
        forbidden = sorted(MANDATORY_CATEGORIES.intersection(updates))
        if forbidden:
            categories = ", ".join(forbidden)
            raise InvariantViolationError(
                f"Notification preferences for {categories} cannot be changed."
            )

        user = await self._get_user(user_id)
        await self._gateway.upsert_recipient(
            user,
            idempotency_key=_recipient_upsert_idempotency_key(user),
        )
        state = await self._gateway.set_preferences(
            user.id,
            preference_set_id=PREFERENCE_SET_ID,
            email_enabled_by_category=updates,
            idempotency_key=f"preferences:{user.id}:{uuid4()}",
        )
        return self._response(state)

    async def _get_user(self, user_id: UUID) -> User:
        async with self._uow_factory() as uow:
            return await uow.users.get(user_id)

    @staticmethod
    def _response(state: NotificationPreferenceState | None) -> NotificationPreferencesResponse:
        enabled = _default_categories()
        preference_set_id = PREFERENCE_SET_ID
        if state is not None:
            preference_set_id = state.preference_set_id
            marketplace_enabled = state.email_enabled_by_category.get(
                NotificationCategory.MARKETPLACE.value
            )
            if marketplace_enabled is not None:
                enabled[NotificationCategory.MARKETPLACE.value] = marketplace_enabled
        return NotificationPreferencesResponse(
            preference_set_id=preference_set_id,
            security={"email_enabled": enabled["security"], "mutable": False},
            kyc={"email_enabled": enabled["kyc"], "mutable": False},
            trade={"email_enabled": enabled["trade"], "mutable": False},
            marketplace={"email_enabled": enabled["marketplace"], "mutable": True},
        )


def get_notification_preference_service() -> NotificationPreferenceService:
    """Build the configured notification preference service."""
    from app.integrations.knock import build_notification_preference_gateway

    return NotificationPreferenceService(gateway=build_notification_preference_gateway())
