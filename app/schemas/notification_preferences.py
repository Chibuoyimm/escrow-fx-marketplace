"""Provider-neutral notification preference schemas."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class NotificationCategory(StrEnum):
    """Notification categories exposed by the account API."""

    SECURITY = "security"
    KYC = "kyc"
    TRADE = "trade"
    MARKETPLACE = "marketplace"


class NotificationCategoryPreferences(BaseModel):
    """Effective email preference for one notification category."""

    email_enabled: bool
    mutable: bool


class NotificationPreferencesResponse(BaseModel):
    """Effective preferences for the current user."""

    preference_set_id: str
    security: NotificationCategoryPreferences
    kyc: NotificationCategoryPreferences
    trade: NotificationCategoryPreferences
    marketplace: NotificationCategoryPreferences


class NotificationCategoryUpdate(BaseModel):
    """Mutable fields for one notification category."""

    model_config = ConfigDict(extra="forbid")

    email_enabled: bool


class NotificationPreferencesPatch(BaseModel):
    """Partial preference update for one or more categories."""

    model_config = ConfigDict(extra="forbid")

    categories: dict[NotificationCategory, NotificationCategoryUpdate] = Field(min_length=1)
