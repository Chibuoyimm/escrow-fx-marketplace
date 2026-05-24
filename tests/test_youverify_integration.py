from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pytest

from app.domain.enums import KycIdType, KycProvider, KycVerificationStatus
from app.integrations.youverify import KycProviderRequest, YouverifyKycProvider

pytestmark = pytest.mark.anyio


class FakeHttpClient:
    """HTTP client test double for Youverify integration tests."""

    def __init__(self, response_payload: dict[str, Any]) -> None:
        self.response_payload = response_payload
        self.calls: list[dict[str, Any]] = []

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
    ) -> httpx.Response:
        self.calls.append({"url": url, "headers": headers, "json": json})
        request = httpx.Request("POST", url)
        return httpx.Response(200, json=self.response_payload, request=request)

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
    ) -> httpx.Response:
        self.calls.append({"url": url, "headers": headers})
        request = httpx.Request("GET", url)
        return httpx.Response(200, json=self.response_payload, request=request)


def provider_request(id_type: KycIdType = KycIdType.BVN) -> KycProviderRequest:
    return KycProviderRequest(
        user_id="user-123",
        id_type=id_type,
        id_number="22222222221",
        first_name="Chibuoyim",
        last_name="Onuigwe",
        date_of_birth=date(1997, 5, 16),
    )


async def test_youverify_provider_posts_bvn_request_to_configured_endpoint() -> None:
    client = FakeHttpClient(
        {
            "success": True,
            "data": {
                "id": "identity-123",
                "status": "verified",
                "validation": {
                    "matches": {
                        "firstName": True,
                        "lastName": True,
                        "dateOfBirth": True,
                    }
                },
            },
        }
    )
    provider = YouverifyKycProvider(
        api_key="test-key",
        base_url="https://sandbox.example.test",
        bvn_endpoint="/v2/api/identity/ng/bvn-basic",
        http_client=client,
    )

    result = await provider.verify_identity(provider_request())

    assert result.provider is KycProvider.YOUVERIFY
    assert result.provider_reference_id == "identity-123"
    assert result.status is KycVerificationStatus.VERIFIED
    assert result.field_match_summary == {
        "id_type": "bvn",
        "firstName": True,
        "lastName": True,
        "dateOfBirth": True,
    }
    assert client.calls == [
        {
            "url": "https://sandbox.example.test/v2/api/identity/ng/bvn-basic",
            "headers": {
                "Authorization": "Bearer test-key",
                "Content-Type": "application/json",
            },
            "json": {
                "id": "22222222221",
                "isSubjectConsent": True,
                "metadata": {
                    "user_id": "user-123",
                    "id_type": "bvn",
                },
                "validation": {
                    "data": {
                        "firstName": "Chibuoyim",
                        "lastName": "Onuigwe",
                        "dateOfBirth": "1997-05-16",
                    },
                },
            },
        }
    ]


async def test_youverify_provider_uses_nin_endpoint() -> None:
    client = FakeHttpClient({"success": True, "data": {"id": "identity-456"}})
    provider = YouverifyKycProvider(api_key="test-key", http_client=client)

    result = await provider.verify_identity(provider_request(KycIdType.NIN))

    assert result.provider_reference_id == "identity-456"
    assert client.calls[0]["url"].endswith("/v2/api/identity/ng/nin")


async def test_youverify_provider_maps_rejected_response() -> None:
    client = FakeHttpClient(
        {
            "success": False,
            "message": "Identity could not be verified.",
            "data": {
                "id": "identity-789",
                "status": "failed",
            },
        }
    )
    provider = YouverifyKycProvider(api_key="test-key", http_client=client)

    result = await provider.verify_identity(provider_request(KycIdType.VNIN))

    assert result.status is KycVerificationStatus.REJECTED
    assert result.rejection_reason == "Identity could not be verified."
    assert client.calls[0]["url"].endswith("/v2/api/identity/ng/vnin")


async def test_youverify_provider_retrieves_identity_status() -> None:
    client = FakeHttpClient(
        {
            "success": True,
            "data": {
                "id": "identity-123",
                "status": "verified",
            },
        }
    )
    provider = YouverifyKycProvider(
        api_key="test-key",
        base_url="https://sandbox.example.test",
        http_client=client,
    )

    result = await provider.retrieve_identity(
        provider_reference_id="identity-123",
        id_type=KycIdType.BVN,
    )

    assert result.status is KycVerificationStatus.VERIFIED
    assert client.calls == [
        {
            "url": "https://sandbox.example.test/v2/api/identity/identity-123",
            "headers": {"Authorization": "Bearer test-key"},
        }
    ]
