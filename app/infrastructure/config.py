"""Application configuration."""

from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings."""

    app_name: str = "Escrow FX Marketplace API"
    api_v1_prefix: str = "/api/v1"
    debug: bool = False
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"
    metrics_enabled: bool = True
    metrics_path: str = "/metrics"
    readiness_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5433/escrow_fx_marketplace"
    )
    jwt_secret_key: str = "change-me-please-use-a-32-char-secret"
    jwt_algorithm: str = "HS256"
    access_token_expiry_minutes: int = 60
    email_verification_token_expiry_minutes: int = 60
    email_verification_frontend_url: str = "http://localhost:8000/verify-email"
    password_reset_token_expiry_minutes: int = 60
    password_reset_frontend_url: str = "http://localhost:8000/reset-password"
    exchange_request_expiry_minutes: int = 1440
    notification_dispatch_batch_size: int = 50
    notification_processing_timeout_seconds: int = 300
    notification_max_attempts: int = 5
    notification_retry_base_seconds: int = 30
    notification_retry_max_seconds: int = 3600
    idempotency_retention_hours: int = 24
    idempotency_processing_timeout_seconds: int = 300
    idempotency_cleanup_batch_size: int = 1000
    rate_limit_enabled: bool = True
    rate_limit_cleanup_batch_size: int = 1000
    rate_limit_policy_overrides: dict[str, dict[str, int]] = Field(default_factory=dict)
    rate_limit_key_secret: str | None = None
    rate_limit_fail_closed_auth: bool = True
    rate_limit_fail_closed_account: bool = True
    rate_limit_fail_closed_kyc: bool = True
    rate_limit_fail_closed_marketplace: bool = False
    trusted_proxy_networks: str = ""
    notification_provider: str = "logging"
    notification_public_base_url: str = "http://localhost:8000"
    knock_api_key: str | None = None
    knock_branch: str | None = None
    kyc_provider: str = "local"
    kyc_reconciliation_batch_size: int = 50
    kyc_submission_cooldown_minutes: int = 1
    kyc_max_attempts_per_window: int = 5
    kyc_attempt_window_hours: int = 24
    youverify_base_url: str = "https://api.youverify.co"
    youverify_api_key: str | None = None
    youverify_webhook_secret: str | None = None
    youverify_bvn_endpoint: str = "/v2/api/identity/ng/bvn"
    jwt_issuer: str = "escrow-fx-marketplace"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="APP_",
        extra="ignore",
    )

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> str:
        """Normalize configured levels before the logging module consumes them."""
        normalized = str(value).upper()
        if normalized not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError("APP_LOG_LEVEL must be a standard Python logging level.")
        return normalized

    @field_validator("metrics_path")
    @classmethod
    def validate_metrics_path(cls, value: str) -> str:
        """Require a path-only metrics endpoint."""
        if (
            len(value) > 128
            or not value.startswith("/")
            or value == "/"
            or "//" in value
            or any(
                character.isspace() or ord(character) < 32 or ord(character) == 127
                for character in value
            )
            or any(character in value for character in "?#{}")
        ):
            raise ValueError(
                "APP_METRICS_PATH must be a bounded absolute path without dynamic segments."
            )
        return value.rstrip("/") or "/"


settings = Settings()
