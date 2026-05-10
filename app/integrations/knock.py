"""Knock notification provider integration."""

from __future__ import annotations

from typing import Any, Protocol

from knockapi import AsyncKnock
from knockapi.types import InlineIdentifyUserRequestParam

from app.domain.entities import OutboxEvent, User
from app.infrastructure.config import settings
from app.services._shared import UnitOfWorkFactory, build_uow


class KnockClientProtocol(Protocol):
    """Small SDK client surface used by the provider."""

    @property
    def users(self) -> Any:
        """User resource from the Knock SDK."""

    @property
    def workflows(self) -> Any:
        """Workflow resource from the Knock SDK."""


class KnockNotificationProvider:
    """Notification provider that triggers Knock workflows from outbox events."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory | None = None,
        api_key: str | None = None,
        branch: str | None = None,
        client: KnockClientProtocol | None = None,
    ) -> None:
        self._uow_factory = uow_factory or build_uow
        self._client = client or self._build_client(api_key=api_key, branch=branch)

    async def send(self, event: OutboxEvent) -> None:
        """Trigger the Knock workflow that corresponds to this outbox event."""
        if event.recipient_user_id is None:
            return

        async with self._uow_factory() as uow:
            recipient = await uow.users.get(event.recipient_user_id)

        await self._upsert_recipient(event, recipient)
        await self._client.workflows.trigger(
            self._workflow_key(event.event_type),
            recipients=[self._recipient(recipient)],
            data=self._data(event, recipient),
            idempotency_key=str(event.id),
        )

    @staticmethod
    def _build_client(
        *,
        api_key: str | None = None,
        branch: str | None = None,
    ) -> KnockClientProtocol:
        resolved_api_key = api_key or settings.knock_api_key
        if not resolved_api_key:
            raise RuntimeError(
                "APP_KNOCK_API_KEY is required when APP_NOTIFICATION_PROVIDER=knock."
            )
        return AsyncKnock(
            api_key=resolved_api_key,
            branch=branch or settings.knock_branch or None,
        )

    @staticmethod
    def _workflow_key(event_type: str) -> str:
        return event_type.replace("_", "-").replace(".", "-")

    async def _upsert_recipient(self, event: OutboxEvent, user: User) -> None:
        await self._client.users.update(
            str(user.id),
            email=user.email,
            name=self._display_name(user.email),
            phone_number=user.phone,
            idempotency_key=f"{event.id}:recipient-upsert",
        )

    @staticmethod
    def _recipient(user: User) -> InlineIdentifyUserRequestParam:
        return {
            "id": str(user.id),
            "email": user.email,
            "name": KnockNotificationProvider._display_name(user.email),
        }

    @staticmethod
    def _display_name(email: str) -> str:
        return email.split("@", maxsplit=1)[0]

    @staticmethod
    def _data(event: OutboxEvent, recipient: User) -> dict[str, object]:
        data: dict[str, object] = KnockNotificationProvider._uppercase_mapping(event.payload)
        data.update(
            {
                "EVENT_ID": str(event.id),
                "EVENT_TYPE": event.event_type,
                "AGGREGATE_TYPE": event.aggregate_type,
                "AGGREGATE_ID": str(event.aggregate_id),
                "RECIPIENT_USER_ID": str(recipient.id),
                "RECIPIENT_EMAIL": recipient.email,
                "USER_NAME": KnockNotificationProvider._display_name(recipient.email),
                "USER_EMAIL": recipient.email,
            }
        )
        KnockNotificationProvider._add_variable_aliases(data)
        return data

    @staticmethod
    def _uppercase_mapping(payload: dict[str, Any]) -> dict[str, object]:
        return {
            key.upper(): KnockNotificationProvider._json_safe(value)
            for key, value in payload.items()
        }

    @staticmethod
    def _add_variable_aliases(knock_variables: dict[str, object]) -> None:
        if "TRADE_CONTRACT_ID" in knock_variables:
            knock_variables.setdefault("TRADE_ID", knock_variables["TRADE_CONTRACT_ID"])
        if "ACCEPTED_OFFER_ID" in knock_variables:
            knock_variables.setdefault("OFFER_ID", knock_variables["ACCEPTED_OFFER_ID"])

        base_url = settings.notification_public_base_url.rstrip("/")
        if "REQUEST_ID" in knock_variables:
            request_id = str(knock_variables["REQUEST_ID"])
            knock_variables.setdefault(
                "REQUEST_URL",
                f"{base_url}/api/v1/exchange-requests/{request_id}",
            )
        if "TRADE_ID" in knock_variables:
            trade_id = str(knock_variables["TRADE_ID"])
            knock_variables.setdefault("TRADE_URL", f"{base_url}/api/v1/trades/{trade_id}")

        knock_variables.setdefault("BOARD_URL", f"{base_url}/api/v1/exchange-requests/board")
        knock_variables.setdefault("CREATE_REQUEST_URL", f"{base_url}/api/v1/exchange-requests")

    @staticmethod
    def _json_safe(value: Any) -> object:
        if value is None or isinstance(value, str | int | float | bool):
            return value
        if isinstance(value, list):
            return [KnockNotificationProvider._json_safe(item) for item in value]
        if isinstance(value, dict):
            return {
                str(key): KnockNotificationProvider._json_safe(item) for key, item in value.items()
            }
        return str(value)
