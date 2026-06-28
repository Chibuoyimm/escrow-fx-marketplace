"""KYC application service."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

from app.domain.entities import KycVerification
from app.domain.enums import KycIdType, KycStatus, KycVerificationStatus, UserStatus
from app.domain.exceptions import InvariantViolationError, NotFoundError, PreconditionFailedError
from app.infrastructure.config import settings
from app.infrastructure.database.unit_of_work import AbstractUnitOfWork
from app.integrations.youverify import (
    KycProviderProtocol,
    KycProviderRequest,
    KycProviderResult,
    YouverifyKycProvider,
    build_kyc_provider,
)
from app.services._shared import UnitOfWorkFactory, as_utc, build_uow, utc_now
from app.services.outbox import OutboxEventPublisher


def hash_identifier(id_type: KycIdType, identifier: str) -> str:
    """Hash an identity number for storage and lookup without retaining the raw value."""
    normalized = identifier.strip().upper()
    return sha256(f"{id_type.value}:{normalized}".encode()).hexdigest()


def mask_identifier(identifier: str) -> str:
    """Mask an identity number for display and audit trails."""
    normalized = identifier.strip()
    if len(normalized) <= 4:
        return "*" * len(normalized)
    return f"{normalized[:2]}{'*' * max(len(normalized) - 6, 0)}{normalized[-4:]}"


class KycService:
    """Application service for customer KYC checks."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory | None = None,
        provider: KycProviderProtocol | None = None,
        outbox_publisher: OutboxEventPublisher | None = None,
    ) -> None:
        self._uow_factory = uow_factory or build_uow
        self._provider = provider or build_kyc_provider()
        self._outbox = outbox_publisher or OutboxEventPublisher()

    async def submit_identity_check(
        self,
        *,
        user_id: UUID,
        id_type: KycIdType,
        id_number: str,
        first_name: str,
        last_name: str,
        date_of_birth: date,
        subject_consent: bool,
    ) -> KycVerification:
        """Submit a provider-backed identity check for the authenticated user."""
        if not subject_consent:
            raise InvariantViolationError("Subject consent is required before KYC verification.")

        current_time = utc_now()
        async with self._uow_factory() as uow:
            user = await uow.users.get(user_id)
            if user.status is not UserStatus.ACTIVE:
                raise PreconditionFailedError("Only active users can submit KYC.")
            if user.country != "NG":
                raise InvariantViolationError(
                    "Nigeria KYC is currently supported for NG users only."
                )
            if user.kyc_status is KycStatus.VERIFIED:
                raise PreconditionFailedError("Your KYC has already been verified.")
            if user.kyc_status is KycStatus.REQUIRES_REVIEW:
                raise PreconditionFailedError("Your KYC submission is already under review.")
            await self._enforce_submission_controls(uow, user_id=user.id, now=current_time)

            result = self._apply_decision_policy(
                await self._provider.verify_identity(
                    KycProviderRequest(
                        user_id=str(user.id),
                        id_type=id_type,
                        id_number=id_number,
                        first_name=first_name,
                        last_name=last_name,
                        date_of_birth=date_of_birth,
                    )
                )
            )

            completed_at = (
                current_time
                if result.status
                in {
                    KycVerificationStatus.VERIFIED,
                    KycVerificationStatus.REJECTED,
                    KycVerificationStatus.REQUIRES_REVIEW,
                }
                else None
            )
            verification = await uow.kyc_verifications.add(
                KycVerification(
                    id=uuid4(),
                    user_id=user.id,
                    provider=result.provider,
                    provider_reference_id=result.provider_reference_id,
                    id_type=id_type,
                    masked_identifier=mask_identifier(id_number),
                    identifier_hash=hash_identifier(id_type, id_number),
                    status=result.status,
                    provider_status=result.provider_status,
                    field_match_summary=result.field_match_summary,
                    review_events=[],
                    rejection_reason=result.rejection_reason,
                    consented_at=current_time,
                    submitted_at=current_time,
                    completed_at=completed_at,
                    created_at=current_time,
                    updated_at=current_time,
                )
            )

            await self._outbox.user_kyc_submitted(
                uow,
                user_id=user.id,
                verification_id=verification.id,
                id_type=id_type.value,
                provider=result.provider.value,
            )

            if result.status is KycVerificationStatus.VERIFIED:
                await uow.users.update(
                    replace(
                        user,
                        kyc_status=KycStatus.VERIFIED,
                        updated_at=current_time,
                    )
                )
                await self._outbox.user_kyc_verified(
                    uow,
                    user_id=user.id,
                    verification_id=verification.id,
                    id_type=id_type.value,
                    provider=result.provider.value,
                )
            elif result.status is KycVerificationStatus.REQUIRES_REVIEW:
                await uow.users.update(
                    replace(
                        user,
                        kyc_status=KycStatus.REQUIRES_REVIEW,
                        updated_at=current_time,
                    )
                )
                await self._outbox.user_kyc_requires_review(
                    uow,
                    user_id=user.id,
                    verification_id=verification.id,
                    id_type=id_type.value,
                    provider=result.provider.value,
                    reason=self._review_reason(result),
                )
            elif result.status is KycVerificationStatus.REJECTED:
                await uow.users.update(
                    replace(
                        user,
                        kyc_status=KycStatus.REJECTED,
                        updated_at=current_time,
                    )
                )
                await self._outbox.user_kyc_rejected(
                    uow,
                    user_id=user.id,
                    verification_id=verification.id,
                    id_type=id_type.value,
                    provider=result.provider.value,
                    reason=result.rejection_reason or "KYC verification was rejected.",
                )

            await uow.commit()
            return verification

    async def get_status(self, user_id: UUID) -> KycVerification:
        """Fetch the latest KYC verification attempt for a user."""
        async with self._uow_factory() as uow:
            return await uow.kyc_verifications.get_latest_for_user(user_id)

    async def approve_review(
        self,
        *,
        verification_id: UUID,
        reviewer_user_id: UUID,
    ) -> KycVerification:
        """Approve a KYC verification that requires manual review."""
        current_time = utc_now()
        async with self._uow_factory() as uow:
            verification = await uow.kyc_verifications.get(verification_id)
            if verification.status is not KycVerificationStatus.REQUIRES_REVIEW:
                raise PreconditionFailedError(
                    "Only KYC verifications requiring review can be approved."
                )

            updated = await uow.kyc_verifications.update(
                replace(
                    verification,
                    status=KycVerificationStatus.VERIFIED,
                    provider_status="approved_by_admin",
                    review_events=self._append_review_event(
                        verification.review_events,
                        event_type="decision",
                        reviewer_user_id=reviewer_user_id,
                        created_at=current_time,
                        decision=KycVerificationStatus.VERIFIED.value,
                    ),
                    completed_at=verification.completed_at or current_time,
                    updated_at=current_time,
                )
            )
            user = await uow.users.get(updated.user_id)
            await uow.users.update(
                replace(
                    user,
                    kyc_status=KycStatus.VERIFIED,
                    updated_at=current_time,
                )
            )
            await self._outbox.user_kyc_verified(
                uow,
                user_id=user.id,
                verification_id=updated.id,
                id_type=updated.id_type.value,
                provider=updated.provider.value,
            )
            await uow.commit()
            return updated

    async def add_review_note(
        self,
        *,
        verification_id: UUID,
        reviewer_user_id: UUID,
        note: str,
    ) -> KycVerification:
        """Add an internal note to a KYC verification under review."""
        current_time = utc_now()
        async with self._uow_factory() as uow:
            verification = await uow.kyc_verifications.get(verification_id)
            if verification.status is not KycVerificationStatus.REQUIRES_REVIEW:
                raise PreconditionFailedError(
                    "Only KYC verifications requiring review can receive review notes."
                )

            updated = await uow.kyc_verifications.update(
                replace(
                    verification,
                    review_events=self._append_review_event(
                        verification.review_events,
                        event_type="note",
                        reviewer_user_id=reviewer_user_id,
                        created_at=current_time,
                        note=note,
                    ),
                    updated_at=current_time,
                )
            )
            await uow.commit()
            return updated

    async def reject_review(
        self,
        *,
        verification_id: UUID,
        reviewer_user_id: UUID,
        reason: str,
    ) -> KycVerification:
        """Reject a KYC verification that requires manual review."""
        current_time = utc_now()
        async with self._uow_factory() as uow:
            verification = await uow.kyc_verifications.get(verification_id)
            if verification.status is not KycVerificationStatus.REQUIRES_REVIEW:
                raise PreconditionFailedError(
                    "Only KYC verifications requiring review can be rejected."
                )

            updated = await uow.kyc_verifications.update(
                replace(
                    verification,
                    status=KycVerificationStatus.REJECTED,
                    provider_status="rejected_by_admin",
                    review_events=self._append_review_event(
                        verification.review_events,
                        event_type="decision",
                        reviewer_user_id=reviewer_user_id,
                        created_at=current_time,
                        decision=KycVerificationStatus.REJECTED.value,
                        reason=reason,
                    ),
                    rejection_reason=reason,
                    completed_at=verification.completed_at or current_time,
                    updated_at=current_time,
                )
            )
            user = await uow.users.get(updated.user_id)
            await uow.users.update(
                replace(
                    user,
                    kyc_status=KycStatus.REJECTED,
                    updated_at=current_time,
                )
            )
            await self._outbox.user_kyc_rejected(
                uow,
                user_id=user.id,
                verification_id=updated.id,
                id_type=updated.id_type.value,
                provider=updated.provider.value,
                reason=reason,
            )
            await uow.commit()
            return updated

    async def process_youverify_webhook(self, payload: dict[str, object]) -> KycVerification:
        """Apply a verified Youverify webhook payload to the matching KYC attempt."""
        provider_reference_id = YouverifyKycProvider.provider_reference_from_payload(payload)
        async with self._uow_factory() as uow:
            verification = await uow.kyc_verifications.get_by_provider_reference(
                provider_reference_id
            )
            result = YouverifyKycProvider.result_from_webhook(
                id_type=verification.id_type,
                payload=payload,
            )
            updated = await self._apply_provider_result(uow, verification, result)
            await uow.commit()
            return updated

    async def reconcile_pending(self, *, limit: int | None = None) -> int:
        """Refresh pending KYC attempts from the configured provider."""
        batch_size = limit or settings.kyc_reconciliation_batch_size
        completed = 0

        async with self._uow_factory() as uow:
            pending_verifications = await uow.kyc_verifications.list_by_status(
                KycVerificationStatus.PENDING,
                limit=batch_size,
            )
            for verification in pending_verifications:
                result = await self._provider.retrieve_identity(
                    provider_reference_id=verification.provider_reference_id,
                    id_type=verification.id_type,
                )
                updated = await self._apply_provider_result(uow, verification, result)
                if (
                    updated.status is not KycVerificationStatus.PENDING
                    and verification.status is KycVerificationStatus.PENDING
                ):
                    completed += 1

            await uow.commit()

        return completed

    async def _apply_provider_result(
        self,
        uow: AbstractUnitOfWork,
        verification: KycVerification,
        result: KycProviderResult,
    ) -> KycVerification:
        """Apply a provider result to a KYC verification idempotently."""
        result = self._apply_decision_policy(result)
        if verification.status in {
            KycVerificationStatus.VERIFIED,
            KycVerificationStatus.REJECTED,
            KycVerificationStatus.REQUIRES_REVIEW,
        }:
            return verification
        if result.status is KycVerificationStatus.PENDING:
            return verification

        current_time = utc_now()
        updated = await uow.kyc_verifications.update(
            replace(
                verification,
                status=result.status,
                provider_status=result.provider_status,
                field_match_summary=result.field_match_summary,
                rejection_reason=result.rejection_reason,
                completed_at=current_time
                if result.status
                in {
                    KycVerificationStatus.VERIFIED,
                    KycVerificationStatus.REJECTED,
                    KycVerificationStatus.REQUIRES_REVIEW,
                }
                else verification.completed_at,
                updated_at=current_time,
            )
        )

        user = await uow.users.get(updated.user_id)
        if result.status is KycVerificationStatus.VERIFIED:
            await uow.users.update(
                replace(
                    user,
                    kyc_status=KycStatus.VERIFIED,
                    updated_at=current_time,
                )
            )
            await self._outbox.user_kyc_verified(
                uow,
                user_id=user.id,
                verification_id=updated.id,
                id_type=updated.id_type.value,
                provider=updated.provider.value,
            )
        elif result.status is KycVerificationStatus.REQUIRES_REVIEW:
            await uow.users.update(
                replace(
                    user,
                    kyc_status=KycStatus.REQUIRES_REVIEW,
                    updated_at=current_time,
                )
            )
            await self._outbox.user_kyc_requires_review(
                uow,
                user_id=user.id,
                verification_id=updated.id,
                id_type=updated.id_type.value,
                provider=updated.provider.value,
                reason=self._review_reason(result),
            )
        elif result.status is KycVerificationStatus.REJECTED:
            await uow.users.update(
                replace(
                    user,
                    kyc_status=KycStatus.REJECTED,
                    updated_at=current_time,
                )
            )
            await self._outbox.user_kyc_rejected(
                uow,
                user_id=user.id,
                verification_id=updated.id,
                id_type=updated.id_type.value,
                provider=updated.provider.value,
                reason=result.rejection_reason or "KYC verification was rejected.",
            )

        return updated

    async def _enforce_submission_controls(
        self,
        uow: AbstractUnitOfWork,
        *,
        user_id: UUID,
        now: datetime,
    ) -> None:
        """Block rapid or excessive repeated KYC submissions."""
        try:
            latest = await uow.kyc_verifications.get_latest_for_user(user_id)
        except NotFoundError:
            latest = None

        cooldown_minutes = max(settings.kyc_submission_cooldown_minutes, 0)
        if latest is not None and cooldown_minutes > 0:
            cooldown_threshold = now - timedelta(minutes=cooldown_minutes)
            if as_utc(latest.submitted_at) >= cooldown_threshold:
                raise PreconditionFailedError("Please wait before submitting another KYC attempt.")

        max_attempts = max(settings.kyc_max_attempts_per_window, 0)
        window_hours = max(settings.kyc_attempt_window_hours, 0)
        if max_attempts <= 0 or window_hours <= 0:
            return

        window_start = now - timedelta(hours=window_hours)
        recent_attempts = await uow.kyc_verifications.list_submitted_since(
            user_id=user_id,
            since=window_start,
            limit=max_attempts,
        )
        if len(recent_attempts) >= max_attempts:
            raise PreconditionFailedError(
                "You have reached the KYC attempt limit for now. Please try again later."
            )

    @staticmethod
    def _apply_decision_policy(result: KycProviderResult) -> KycProviderResult:
        """Apply internal KYC decision rules on top of provider output."""
        if result.status is not KycVerificationStatus.VERIFIED:
            return result

        required_matches = {
            "first_name": KycService._match_flag(
                result.field_match_summary,
                "first_name",
                "firstName",
            ),
            "last_name": KycService._match_flag(
                result.field_match_summary,
                "last_name",
                "lastName",
            ),
            "date_of_birth": KycService._match_flag(
                result.field_match_summary,
                "date_of_birth",
                "dateOfBirth",
            ),
        }
        all_required_match = all(value is True for value in required_matches.values())
        if all_required_match:
            return result

        return replace(
            result,
            status=KycVerificationStatus.REQUIRES_REVIEW,
            field_match_summary={
                **result.field_match_summary,
                "policy": {
                    "decision": KycVerificationStatus.REQUIRES_REVIEW.value,
                    "reason": "required_identity_fields_incomplete",
                    "required_matches": required_matches,
                },
            },
        )

    @staticmethod
    def _match_flag(summary: dict[str, object], *keys: str) -> bool | None:
        """Return the first explicit boolean match flag found for a field."""
        for key in keys:
            value = summary.get(key)
            if isinstance(value, bool):
                return value
        return None

    @staticmethod
    def _review_reason(result: KycProviderResult) -> str:
        """Return a stable reason string for review-needed KYC outcomes."""
        policy = result.field_match_summary.get("policy")
        if isinstance(policy, dict):
            reason = policy.get("reason")
            if isinstance(reason, str) and reason:
                return reason
        return "kyc_requires_manual_review"

    @staticmethod
    def _append_review_event(
        review_events: list[dict[str, object]],
        *,
        event_type: str,
        reviewer_user_id: UUID,
        created_at: datetime,
        decision: str | None = None,
        reason: str | None = None,
        note: str | None = None,
    ) -> list[dict[str, object]]:
        """Append a structured review history event."""
        event: dict[str, object] = {
            "event_type": event_type,
            "reviewer_user_id": str(reviewer_user_id),
            "created_at": created_at.isoformat(),
        }
        if decision is not None:
            event["decision"] = decision
        if reason is not None:
            event["reason"] = reason
        if note is not None:
            event["note"] = note
        return [*review_events, event]


def get_kyc_service() -> KycService:
    """Build the default KYC service."""
    return KycService()
