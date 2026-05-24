"""Youverify KYC provider integration boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol
from uuid import uuid4

import httpx

from app.domain.enums import KycIdType, KycProvider, KycVerificationStatus
from app.infrastructure.config import settings


@dataclass(frozen=True, slots=True)
class KycProviderRequest:
    """Provider-neutral KYC verification request."""

    user_id: str
    id_type: KycIdType
    id_number: str
    first_name: str
    last_name: str
    date_of_birth: date


@dataclass(frozen=True, slots=True)
class KycProviderResult:
    """Provider-neutral KYC verification result."""

    provider: KycProvider
    provider_reference_id: str
    status: KycVerificationStatus
    provider_status: str
    field_match_summary: dict[str, object]
    rejection_reason: str | None


class KycProviderProtocol(Protocol):
    """Small KYC provider surface used by the service layer."""

    async def verify_identity(self, request: KycProviderRequest) -> KycProviderResult:
        """Run an identity verification check."""

    async def retrieve_identity(
        self,
        *,
        provider_reference_id: str,
        id_type: KycIdType,
    ) -> KycProviderResult:
        """Retrieve the latest provider status for an identity verification."""


class AsyncHttpClientProtocol(Protocol):
    """Small async HTTP client surface used by the Youverify integration."""

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
    ) -> httpx.Response:
        """Send a POST request."""

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
    ) -> httpx.Response:
        """Send a GET request."""


class LocalKycProvider:
    """Deterministic local KYC provider used until real credentials are available."""

    async def verify_identity(self, request: KycProviderRequest) -> KycProviderResult:
        """Return a deterministic local result for development and tests."""
        normalized_id = request.id_number.strip()
        status = (
            KycVerificationStatus.REJECTED
            if normalized_id.endswith("0")
            else KycVerificationStatus.VERIFIED
        )
        return KycProviderResult(
            provider=KycProvider.LOCAL,
            provider_reference_id=f"local_{uuid4()}",
            status=status,
            provider_status=status.value,
            field_match_summary={
                "first_name": status is KycVerificationStatus.VERIFIED,
                "last_name": status is KycVerificationStatus.VERIFIED,
                "date_of_birth": status is KycVerificationStatus.VERIFIED,
            },
            rejection_reason="Local KYC rejected this identifier."
            if status is KycVerificationStatus.REJECTED
            else None,
        )

    async def retrieve_identity(
        self,
        *,
        provider_reference_id: str,
        id_type: KycIdType,
    ) -> KycProviderResult:
        """Return the current local result for a pending verification."""
        return KycProviderResult(
            provider=KycProvider.LOCAL,
            provider_reference_id=provider_reference_id,
            status=KycVerificationStatus.PENDING,
            provider_status=KycVerificationStatus.PENDING.value,
            field_match_summary={"id_type": id_type.value},
            rejection_reason=None,
        )


class YouverifyKycProvider:
    """Youverify provider integration."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        bvn_endpoint: str | None = None,
        http_client: AsyncHttpClientProtocol | None = None,
    ) -> None:
        self._api_key = api_key or settings.youverify_api_key
        self._base_url = (base_url or settings.youverify_base_url).rstrip("/")
        self._bvn_endpoint = bvn_endpoint or settings.youverify_bvn_endpoint
        self._http_client = http_client or httpx.AsyncClient(timeout=30)

    async def verify_identity(self, request: KycProviderRequest) -> KycProviderResult:
        """Run an identity verification check against Youverify."""
        if not self._api_key:
            raise RuntimeError("APP_YOUVERIFY_API_KEY is required when APP_KYC_PROVIDER=youverify.")

        response = await self._http_client.post(
            self._url_for(request.id_type),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json=self._payload(request),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Youverify returned an unexpected response payload.")
        return self._result_from_payload(request.id_type, payload)

    async def retrieve_identity(
        self,
        *,
        provider_reference_id: str,
        id_type: KycIdType,
    ) -> KycProviderResult:
        """Retrieve the latest Youverify status for an identity verification."""
        if not self._api_key:
            raise RuntimeError("APP_YOUVERIFY_API_KEY is required when APP_KYC_PROVIDER=youverify.")

        response = await self._http_client.get(
            f"{self._base_url}/v2/api/identity/{provider_reference_id}",
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Youverify returned an unexpected response payload.")
        return self._result_from_payload(id_type, payload)

    def _url_for(self, id_type: KycIdType) -> str:
        if id_type is KycIdType.BVN:
            return f"{self._base_url}{self._bvn_endpoint}"
        if id_type is KycIdType.NIN:
            return f"{self._base_url}/v2/api/identity/ng/nin"
        if id_type is KycIdType.VNIN:
            return f"{self._base_url}/v2/api/identity/ng/vnin"
        raise RuntimeError(f"Unsupported Youverify ID type '{id_type}'.")

    @staticmethod
    def _payload(request: KycProviderRequest) -> dict[str, Any]:
        return {
            "id": request.id_number,
            "isSubjectConsent": True,
            "metadata": {
                "user_id": request.user_id,
                "id_type": request.id_type.value,
            },
            "validation": {
                "data": {
                    "firstName": request.first_name,
                    "lastName": request.last_name,
                    "dateOfBirth": request.date_of_birth.isoformat(),
                },
            },
        }

    @staticmethod
    def _result_from_payload(
        id_type: KycIdType,
        payload: dict[str, Any],
    ) -> KycProviderResult:
        data = YouverifyKycProvider._as_dict(payload.get("data"))
        provider_reference_id = YouverifyKycProvider._provider_reference(payload, data)
        provider_status = YouverifyKycProvider._provider_status(payload, data)
        status = YouverifyKycProvider._status(provider_status)
        field_match_summary = YouverifyKycProvider._field_match_summary(data)
        return KycProviderResult(
            provider=KycProvider.YOUVERIFY,
            provider_reference_id=provider_reference_id,
            status=status,
            provider_status=provider_status,
            field_match_summary={
                "id_type": id_type.value,
                **field_match_summary,
            },
            rejection_reason=YouverifyKycProvider._rejection_reason(payload, data, status),
        )

    @staticmethod
    def _provider_reference(payload: dict[str, Any], data: dict[str, Any]) -> str:
        for source in (data, payload):
            for key in ("id", "identityId", "reference", "referenceId", "_id"):
                value = source.get(key)
                if value:
                    return str(value)
        raise RuntimeError("Youverify response did not include a provider reference.")

    @staticmethod
    def _provider_status(payload: dict[str, Any], data: dict[str, Any]) -> str:
        for source in (data, payload):
            for key in ("status", "verificationStatus", "state"):
                value = source.get(key)
                if value:
                    return str(value).lower()
        success = payload.get("success")
        if success is True:
            return "verified"
        return "pending"

    @staticmethod
    def _status(provider_status: str) -> KycVerificationStatus:
        normalized = provider_status.lower()
        if normalized in {"verified", "success", "successful", "completed", "found"}:
            return KycVerificationStatus.VERIFIED
        if normalized in {"rejected", "failed", "declined", "not_found", "not-found"}:
            return KycVerificationStatus.REJECTED
        if normalized in {"review", "requires_review", "manual_review"}:
            return KycVerificationStatus.REQUIRES_REVIEW
        return KycVerificationStatus.PENDING

    @staticmethod
    def _field_match_summary(data: dict[str, Any]) -> dict[str, object]:
        validation = YouverifyKycProvider._as_dict(data.get("validation"))
        matches = YouverifyKycProvider._as_dict(validation.get("matches"))
        if matches:
            return {
                str(key): YouverifyKycProvider._json_safe(value) for key, value in matches.items()
            }
        return {}

    @staticmethod
    def _rejection_reason(
        payload: dict[str, Any],
        data: dict[str, Any],
        status: KycVerificationStatus,
    ) -> str | None:
        if status is not KycVerificationStatus.REJECTED:
            return None
        for source in (data, payload):
            for key in ("reason", "message", "error"):
                value = source.get(key)
                if value:
                    return str(value)
        return "Youverify rejected this identity verification."

    @staticmethod
    def _as_dict(value: object) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _json_safe(value: object) -> object:
        if value is None or isinstance(value, str | int | float | bool):
            return value
        if isinstance(value, list):
            return [YouverifyKycProvider._json_safe(item) for item in value]
        if isinstance(value, dict):
            return {str(key): YouverifyKycProvider._json_safe(item) for key, item in value.items()}
        return str(value)


def build_kyc_provider() -> KycProviderProtocol:
    """Build the configured KYC provider."""
    provider = settings.kyc_provider.lower().strip()
    if provider == KycProvider.LOCAL:
        return LocalKycProvider()
    if provider == KycProvider.YOUVERIFY:
        return YouverifyKycProvider()
    raise RuntimeError(f"Unsupported KYC provider '{settings.kyc_provider}'.")
