"""Application configuration."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings."""

    app_name: str = "Escrow FX Marketplace API"
    api_v1_prefix: str = "/api/v1"
    debug: bool = False
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/escrow_fx_marketplace"
    jwt_secret_key: str = "change-me-please-use-a-32-char-secret"
    jwt_algorithm: str = "HS256"
    access_token_expiry_minutes: int = 60
    jwt_issuer: str = "escrow-fx-marketplace"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="APP_",
        extra="ignore",
    )


settings = Settings()
