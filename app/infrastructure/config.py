"""Application configuration."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings."""

    app_name: str = "Escrow FX Marketplace API"
    api_v1_prefix: str = "/api/v1"
    debug: bool = False
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


settings = Settings()
