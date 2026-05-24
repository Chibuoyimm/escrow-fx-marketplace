"""KYC application service."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from hashlib import sha256
from uuid import UUID, uuid4

from app.domain.entities import KycVerification
from app.domain.enums import KycIdType, KycStatus, KycVerificationStatus, UserStatus
from app.domain.exceptions import InvariantViolationError, PreconditionFailedError
from app.infrastructure.config import settings
from app.integrations.youverify import KycProviderProtocol, KycProviderRequest, build_kyc_provider
from app.services._shared import UnitOfWorkFactory, build_uow, utc_now
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

            result = await self._provider.verify_identity(
                KycProviderRequest(
                    user_id=str(user.id),
                    id_type=id_type,
                    id_number=id_number,
                    first_name=first_name,
                    last_name=last_name,
                    date_of_birth=date_of_birth,
                )
            )

            completed_at = (
                current_time if result.status is not KycVerificationStatus.PENDING else None
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

    async def reconcile_pending(self, *, limit: int | None = None) -> int:
        """Refresh pending KYC attempts from the configured provider."""
        batch_size = limit or settings.kyc_reconciliation_batch_size
        completed = 0
        current_time = utc_now()

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
                if result.status is KycVerificationStatus.PENDING:
                    continue

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
                    completed += 1
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
                    completed += 1

            await uow.commit()

        return completed


def get_kyc_service() -> KycService:
    """Build the default KYC service."""
    return KycService()
