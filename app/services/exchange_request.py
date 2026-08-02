"""Exchange request service layer."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from app.domain.entities import ExchangeRequest, ExchangeRequestDetails
from app.domain.enums import (
    CorridorStatus,
    CurrencyStatus,
    ExchangeOfferStatus,
    ExchangeRequestStatus,
    KycStatus,
    UserStatus,
)
from app.domain.exceptions import (
    ConflictError,
    InvariantViolationError,
    NotFoundError,
    PreconditionFailedError,
)
from app.domain.lifecycle import (
    REQUEST_RELISTABLE_STATUSES,
    request_can_be_cancelled,
    request_can_be_edited,
)
from app.domain.value_objects import Money, Rate
from app.infrastructure.config import settings
from app.infrastructure.idempotency import (
    IdempotencyReplay,
    IdempotencyRequest,
    claim_idempotency,
    complete_idempotency,
)
from app.infrastructure.pagination import (
    decode_cursor,
    encode_next_cursor,
    normalize_date_range,
    validate_range,
)
from app.services._shared import (
    UnitOfWorkFactory,
    as_utc,
    build_uow,
    format_decimal,
    lock_users_in_order,
    utc_now,
)
from app.services.outbox import OutboxEventPublisher


class ExchangeRequestService:
    """Application service for exchange request creation and reads."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory | None = None,
        outbox_publisher: OutboxEventPublisher | None = None,
    ) -> None:
        self._uow_factory = uow_factory or build_uow
        self._outbox = outbox_publisher or OutboxEventPublisher()

    async def create_request(
        self,
        *,
        creator_user_id: UUID,
        from_currency_code: str,
        to_currency_code: str,
        from_amount: Decimal,
        preferred_rate: Decimal,
        min_rate: Decimal | None,
        idempotency: IdempotencyRequest | None = None,
    ) -> ExchangeRequestDetails | IdempotencyReplay:
        """Create a new exchange request for the authenticated user."""
        normalized_from = self._normalize_currency_code(from_currency_code)
        normalized_to = self._normalize_currency_code(to_currency_code)

        if normalized_from == normalized_to:
            raise InvariantViolationError("Source and destination currencies must differ.")

        money = Money(amount=from_amount, currency_code=normalized_from)
        preferred = Rate(value=preferred_rate)
        minimum = Rate(value=min_rate) if min_rate is not None else None
        if minimum is not None and minimum.value > preferred.value:
            raise InvariantViolationError("Minimum rate cannot be greater than preferred rate.")

        current_time = utc_now()
        async with self._uow_factory() as uow:
            claim = await claim_idempotency(uow, idempotency, now=current_time)
            if isinstance(claim, IdempotencyReplay):
                return claim
            user = await uow.users.get_for_update(creator_user_id)
            if user.status is not UserStatus.ACTIVE:
                raise PreconditionFailedError("Only active users can create exchange requests.")
            if user.kyc_status is not KycStatus.VERIFIED:
                raise PreconditionFailedError(
                    "Verified KYC is required to create exchange requests."
                )

            from_currency = await uow.currencies.get_by_code(normalized_from)
            to_currency = await uow.currencies.get_by_code(normalized_to)
            if from_currency.status is not CurrencyStatus.ACTIVE:
                raise NotFoundError(f"Currency '{normalized_from}' was not found.")
            if to_currency.status is not CurrencyStatus.ACTIVE:
                raise NotFoundError(f"Currency '{normalized_to}' was not found.")

            if money.amount < from_currency.min_amount:
                raise InvariantViolationError(
                    "Amount is below the configured minimum for that currency."
                )
            if money.amount > from_currency.max_amount:
                raise InvariantViolationError(
                    "Amount exceeds the configured maximum for that currency."
                )

            try:
                corridor = await uow.corridors.get_by_currency_pair(
                    from_currency.id, to_currency.id
                )
            except NotFoundError as exc:
                raise NotFoundError(
                    f"An active corridor for '{normalized_from}/{normalized_to}' was not found."
                ) from exc

            if corridor.status is not CorridorStatus.ACTIVE:
                raise NotFoundError(
                    f"An active corridor for '{normalized_from}/{normalized_to}' was not found."
                )

            current_time = utc_now()
            created = await uow.exchange_requests.add(
                ExchangeRequest(
                    id=uuid4(),
                    relisted_from_request_id=None,
                    creator_user_id=user.id,
                    from_currency_id=from_currency.id,
                    to_currency_id=to_currency.id,
                    from_amount=money.amount,
                    preferred_rate=preferred.value,
                    min_rate=minimum.value if minimum is not None else None,
                    status=ExchangeRequestStatus.REQUEST_OPEN,
                    expires_at=current_time
                    + timedelta(minutes=settings.exchange_request_expiry_minutes),
                    created_at=current_time,
                    updated_at=current_time,
                )
            )
            await self._outbox.exchange_request_created(
                uow,
                request_id=created.id,
                creator_user_id=user.id,
                from_currency_code=normalized_from,
                to_currency_code=normalized_to,
                from_amount=format_decimal(created.from_amount),
                preferred_rate=format_decimal(created.preferred_rate),
                min_rate=(
                    format_decimal(created.min_rate) if created.min_rate is not None else None
                ),
            )
            response = await uow.exchange_requests.get_details_for_user(created.id, user.id)
            await complete_idempotency(
                uow,
                claim,
                response_status_code=201,
                response=response,
                now=current_time,
            )
            await uow.commit()
            return response

    async def list_board_requests_page(
        self,
        viewer_user_id: UUID,
        *,
        cursor: str | None = None,
        limit: int = 50,
        statuses: tuple[ExchangeRequestStatus, ...] | None = None,
        from_currency_code: str | None = None,
        to_currency_code: str | None = None,
        min_amount: Decimal | None = None,
        max_amount: Decimal | None = None,
        min_preferred_rate: Decimal | None = None,
        max_preferred_rate: Decimal | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[list[ExchangeRequestDetails], str | None]:
        """List board requests using stable cursor pagination."""
        validate_range(min_amount, max_amount, "amount")
        validate_range(min_preferred_rate, max_preferred_rate, "preferred rate")
        created_from, created_to = normalize_date_range(created_from, created_to)
        async with self._uow_factory() as uow:
            items, next_position = await uow.exchange_requests.list_board_details_page(
                viewer_user_id,
                cursor=decode_cursor(cursor),
                limit=limit,
                statuses=statuses,
                from_currency_code=from_currency_code,
                to_currency_code=to_currency_code,
                min_amount=min_amount,
                max_amount=max_amount,
                min_preferred_rate=min_preferred_rate,
                max_preferred_rate=max_preferred_rate,
                created_from=created_from,
                created_to=created_to,
            )
            return items, encode_next_cursor(next_position)

    async def list_requests_for_user_page(
        self,
        user_id: UUID,
        *,
        cursor: str | None = None,
        limit: int = 50,
        statuses: tuple[ExchangeRequestStatus, ...] | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[list[ExchangeRequestDetails], str | None]:
        """List a user's requests using stable cursor pagination."""
        created_from, created_to = normalize_date_range(created_from, created_to)
        async with self._uow_factory() as uow:
            items, next_position = await uow.exchange_requests.list_details_for_user_page(
                user_id,
                cursor=decode_cursor(cursor),
                limit=limit,
                statuses=statuses,
                created_from=created_from,
                created_to=created_to,
            )
            return items, encode_next_cursor(next_position)

    async def get_visible_request(
        self,
        request_id: UUID,
        viewer_user_id: UUID,
    ) -> ExchangeRequestDetails:
        """Fetch a request visible to the authenticated viewer."""
        async with self._uow_factory() as uow:
            return await uow.exchange_requests.get_visible_details(request_id, viewer_user_id)

    async def cancel_request(
        self,
        *,
        request_id: UUID,
        requester_user_id: UUID,
        idempotency: IdempotencyRequest | None = None,
    ) -> ExchangeRequestDetails | IdempotencyReplay:
        """Cancel an open or pending request owned by the authenticated user."""
        current_time = utc_now()

        async with self._uow_factory() as uow:
            claim = await claim_idempotency(uow, idempotency, now=current_time)
            if isinstance(claim, IdempotencyReplay):
                return claim
            initial_request = await uow.exchange_requests.get(request_id)
            if initial_request.creator_user_id != requester_user_id:
                raise NotFoundError(f"Exchange request '{request_id}' was not found.")
            initial_offers = await uow.exchange_offers.list_for_request(request_id)
            await lock_users_in_order(
                uow,
                (
                    initial_request.creator_user_id,
                    *(offer.offer_user_id for offer in initial_offers),
                ),
            )
            exchange_request = await uow.exchange_requests.get_for_update(request_id)
            if exchange_request.creator_user_id != initial_request.creator_user_id:
                raise ConflictError("Exchange request participants changed; retry the operation.")

            current_offers = await uow.exchange_offers.list_for_request(request_id)
            initial_offer_owners = {offer.id: offer.offer_user_id for offer in initial_offers}
            current_offer_owners = {offer.id: offer.offer_user_id for offer in current_offers}
            if current_offer_owners != initial_offer_owners:
                raise ConflictError("Exchange request offers changed; retry the operation.")

            locked_offers = []
            for current_offer in sorted(current_offers, key=lambda offer: offer.id.int):
                locked_offer = await uow.exchange_offers.get_for_update(current_offer.id)
                if (
                    locked_offer.request_id != exchange_request.id
                    or locked_offer.offer_user_id != initial_offer_owners[locked_offer.id]
                ):
                    raise ConflictError(
                        "Exchange offer relationships changed; retry the operation."
                    )
                locked_offers.append(locked_offer)

            if not request_can_be_cancelled(exchange_request.status):
                raise InvariantViolationError("This exchange request can no longer be cancelled.")
            if as_utc(exchange_request.expires_at) <= current_time:
                raise InvariantViolationError("This exchange request has already expired.")

            await uow.exchange_requests.update(
                replace(
                    exchange_request,
                    status=ExchangeRequestStatus.CANCELLED,
                    updated_at=current_time,
                )
            )

            await self._outbox.exchange_request_cancelled(
                uow,
                request_id=exchange_request.id,
                requester_user_id=requester_user_id,
            )
            for offer in locked_offers:
                if offer.status is not ExchangeOfferStatus.ACTIVE:
                    continue
                await uow.exchange_offers.update(
                    replace(
                        offer,
                        status=ExchangeOfferStatus.REJECTED,
                        updated_at=current_time,
                    )
                )
                await self._outbox.exchange_offer_rejected(
                    uow,
                    offer_id=offer.id,
                    request_id=exchange_request.id,
                    recipient_user_id=offer.offer_user_id,
                    reason="request_cancelled",
                )

            response = await uow.exchange_requests.get_details_for_user(
                request_id, requester_user_id
            )
            await complete_idempotency(
                uow,
                claim,
                response_status_code=200,
                response=response,
                now=current_time,
            )
            await uow.commit()
            return response

    async def update_request(
        self,
        *,
        request_id: UUID,
        requester_user_id: UUID,
        fields: set[str],
        from_amount: Decimal | None,
        preferred_rate: Decimal | None,
        min_rate: Decimal | None,
    ) -> ExchangeRequestDetails:
        """Update request terms before any offer has ever been submitted."""
        current_time = utc_now()
        if not fields:
            raise InvariantViolationError("At least one exchange request term is required.")
        async with self._uow_factory() as uow:
            initial_request = await uow.exchange_requests.get(request_id)
            if initial_request.creator_user_id != requester_user_id:
                raise NotFoundError(f"Exchange request '{request_id}' was not found.")
            users = await lock_users_in_order(uow, (initial_request.creator_user_id,))
            user = users[initial_request.creator_user_id]
            self._require_active_verified_user(user.status, user.kyc_status)
            request = await uow.exchange_requests.get_for_update(request_id)
            if request.creator_user_id != initial_request.creator_user_id:
                raise ConflictError("Exchange request participants changed; retry the operation.")
            if not request_can_be_edited(request.status):
                raise InvariantViolationError("This exchange request can no longer be edited.")
            if as_utc(request.expires_at) <= current_time:
                raise InvariantViolationError("This exchange request has expired.")
            if await uow.exchange_requests.has_any_offers(request_id):
                raise InvariantViolationError(
                    "An exchange request cannot be edited after an offer has been submitted."
                )

            new_amount = from_amount if "from_amount" in fields else request.from_amount
            new_preferred = preferred_rate if "preferred_rate" in fields else request.preferred_rate
            new_minimum = min_rate if "min_rate" in fields else request.min_rate
            from_currency = await uow.currencies.get(request.from_currency_id)
            if new_amount is None or new_preferred is None:
                raise InvariantViolationError("Amount and preferred rate cannot be cleared.")
            self._validate_amount(new_amount, from_currency.min_amount, from_currency.max_amount)
            preferred = Rate(value=new_preferred)
            minimum = Rate(value=new_minimum) if new_minimum is not None else None
            if minimum is not None and minimum.value > preferred.value:
                raise InvariantViolationError("Minimum rate cannot be greater than preferred rate.")

            await uow.exchange_requests.update(
                replace(
                    request,
                    from_amount=Money(new_amount, from_currency.code).amount,
                    preferred_rate=preferred.value,
                    min_rate=minimum.value if minimum is not None else None,
                    updated_at=current_time,
                )
            )
            await uow.commit()
            return await uow.exchange_requests.get_details_for_user(request_id, requester_user_id)

    async def relist_request(
        self,
        *,
        request_id: UUID,
        requester_user_id: UUID,
        fields: set[str],
        from_amount: Decimal | None,
        preferred_rate: Decimal | None,
        min_rate: Decimal | None,
        idempotency: IdempotencyRequest | None = None,
    ) -> ExchangeRequestDetails | IdempotencyReplay:
        """Create a new request from an expired or cancelled request."""
        current_time = utc_now()
        async with self._uow_factory() as uow:
            claim = await claim_idempotency(uow, idempotency, now=current_time)
            if isinstance(claim, IdempotencyReplay):
                return claim
            initial_request = await uow.exchange_requests.get(request_id)
            if initial_request.creator_user_id != requester_user_id:
                raise NotFoundError(f"Exchange request '{request_id}' was not found.")
            users = await lock_users_in_order(uow, (initial_request.creator_user_id,))
            user = users[initial_request.creator_user_id]
            self._require_active_verified_user(user.status, user.kyc_status)
            original = await uow.exchange_requests.get_for_update(request_id)
            if original.creator_user_id != initial_request.creator_user_id:
                raise ConflictError("Exchange request participants changed; retry the operation.")
            if original.status not in REQUEST_RELISTABLE_STATUSES:
                raise InvariantViolationError(
                    "Only cancelled or expired exchange requests can be relisted."
                )
            if await uow.exchange_requests.has_relisted_successor(original.id):
                raise ConflictError("This exchange request has already been relisted.")

            from_currency = await uow.currencies.get(original.from_currency_id)
            to_currency = await uow.currencies.get(original.to_currency_id)
            if (
                from_currency.status is not CurrencyStatus.ACTIVE
                or to_currency.status is not CurrencyStatus.ACTIVE
            ):
                raise NotFoundError(
                    "The currencies for this exchange request are no longer available."
                )
            try:
                corridor = await uow.corridors.get_by_currency_pair(
                    from_currency.id, to_currency.id
                )
            except NotFoundError as exc:
                raise NotFoundError(
                    "An active corridor for this exchange request was not found."
                ) from exc
            if corridor.status is not CorridorStatus.ACTIVE:
                raise NotFoundError("An active corridor for this exchange request was not found.")

            amount = from_amount if "from_amount" in fields else original.from_amount
            preferred_value = (
                preferred_rate if "preferred_rate" in fields else original.preferred_rate
            )
            minimum_value = min_rate if "min_rate" in fields else original.min_rate
            if amount is None or preferred_value is None:
                raise InvariantViolationError("Amount and preferred rate cannot be cleared.")
            self._validate_amount(amount, from_currency.min_amount, from_currency.max_amount)
            preferred = Rate(value=preferred_value)
            minimum = Rate(value=minimum_value) if minimum_value is not None else None
            if minimum is not None and minimum.value > preferred.value:
                raise InvariantViolationError("Minimum rate cannot be greater than preferred rate.")

            created = await uow.exchange_requests.add(
                ExchangeRequest(
                    id=uuid4(),
                    relisted_from_request_id=original.id,
                    creator_user_id=requester_user_id,
                    from_currency_id=from_currency.id,
                    to_currency_id=to_currency.id,
                    from_amount=Money(amount, from_currency.code).amount,
                    preferred_rate=preferred.value,
                    min_rate=minimum.value if minimum is not None else None,
                    status=ExchangeRequestStatus.REQUEST_OPEN,
                    expires_at=current_time
                    + timedelta(minutes=settings.exchange_request_expiry_minutes),
                    created_at=current_time,
                    updated_at=current_time,
                )
            )
            await self._outbox.exchange_request_relisted(
                uow,
                request_id=created.id,
                original_request_id=original.id,
                creator_user_id=requester_user_id,
                from_currency_code=from_currency.code,
                to_currency_code=to_currency.code,
                from_amount=format_decimal(created.from_amount),
                preferred_rate=format_decimal(created.preferred_rate),
                min_rate=format_decimal(created.min_rate) if created.min_rate is not None else None,
                expires_at=created.expires_at,
            )
            response = await uow.exchange_requests.get_details_for_user(
                created.id, requester_user_id
            )
            await complete_idempotency(
                uow,
                claim,
                response_status_code=201,
                response=response,
                now=current_time,
            )
            await uow.commit()
            return response

    @staticmethod
    def _require_active_verified_user(status: UserStatus, kyc_status: KycStatus) -> None:
        if status is not UserStatus.ACTIVE:
            raise PreconditionFailedError("Only active users can manage exchange requests.")
        if kyc_status is not KycStatus.VERIFIED:
            raise PreconditionFailedError("Verified KYC is required to manage exchange requests.")

    @staticmethod
    def _validate_amount(amount: Decimal, minimum: Decimal, maximum: Decimal) -> None:
        if amount < minimum:
            raise InvariantViolationError(
                "Amount is below the configured minimum for that currency."
            )
        if amount > maximum:
            raise InvariantViolationError(
                "Amount exceeds the configured maximum for that currency."
            )

    @staticmethod
    def _normalize_currency_code(code: str) -> str:
        """Normalize a currency code for lookups."""
        return code.strip().upper()


def get_exchange_request_service() -> ExchangeRequestService:
    """Build the default exchange request service."""
    return ExchangeRequestService()
