"""Application settings, loaded from environment (Pydantic v2 settings)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"

    # Postgres: app traffic goes through PgBouncer; direct URL bypasses it for
    # LISTEN/NOTIFY + long export jobs (doc 06 §4, doc 18 §6.3).
    database_url: str = "postgresql+asyncpg://civic:civic@localhost:6432/civicsignals"
    database_direct_url: str | None = None

    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    s3_endpoint_url: str | None = None
    s3_access_key_id: str = "civic"
    s3_secret_access_key: str = "civic-secret"
    s3_raw_bucket: str = "civic-raw"
    s3_region: str = "us-east-1"

    smtp_host: str = "localhost"
    smtp_port: int = 1025
    email_from: str = "notifications@civicsignals.io"

    secret_key: str = "dev-only-change-me"
    access_token_ttl_seconds: int = 3600

    llm_default_provider: Literal["anthropic", "openai", "ollama"] = "anthropic"
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434"

    api_v1_prefix: str = Field(default="/api/v1")


@lru_cache
def get_settings() -> Settings:
    return Settings()
