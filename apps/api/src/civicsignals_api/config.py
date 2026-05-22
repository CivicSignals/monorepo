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

    # LLM gateway (doc 06 §7, doc 18 §6.6). Vendor SDKs are imported lazily by
    # the backends; only the keys/base URLs configured here are needed.
    llm_default_provider: Literal["anthropic", "openai", "ollama"] = "anthropic"
    anthropic_api_key: str | None = None
    anthropic_base_url: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    ollama_base_url: str = "http://localhost:11434"
    # Retry attempts (incl. the first) on transient LLM errors.
    llm_max_attempts: int = 3
    # Per-task model overrides: task name -> "provider:model" (or just "model",
    # which uses llm_default_provider). Empty by default; the gateway falls back
    # to its built-in cheap-Haiku/Sonnet defaults (DEFAULT_TASK_MODELS).
    # Example: {"classify": "anthropic:claude-3-5-haiku-latest"}.
    llm_task_models: dict[str, str] = Field(default_factory=dict)

    api_v1_prefix: str = Field(default="/api/v1")

    # Recipe DSL + runner (doc 18 §3). Both default to the monorepo layout
    # (discovered by walking up from the installed package) and can be overridden
    # in containers where the repo root sits elsewhere.
    recipe_schema_dir: str | None = None
    recipes_dir: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
