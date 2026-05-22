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

    # --- Auth / JWT (B1) -----------------------------------------------------
    # Bearer JWTs are signed with ``jwt_secret`` (falls back to ``secret_key``)
    # using ``jwt_algorithm``. ``jwt_issuer`` is set as the ``iss`` claim and
    # verified on decode. Access tokens expire after ``access_token_ttl_seconds``
    # (above); refresh tokens after ``refresh_token_ttl_seconds``. The
    # email-verification token (single-use, hashed at rest) lives for
    # ``email_verification_ttl_seconds``. ``web_base_url`` builds the link in the
    # verification email (points at the web app's /verify-email page).
    jwt_secret: str | None = None
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "civicsignals"
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 30  # 30 days
    email_verification_ttl_seconds: int = 60 * 60 * 24  # 24 hours
    # Require email verification before /auth/login succeeds. Defaults off so the
    # dev/self-host first-run flow is frictionless; Cloud sets it true.
    require_email_verification: bool = False
    web_base_url: str = "http://localhost:3000"
    # Password-reset token TTL (B3). Short window reduces the attack surface for
    # a stolen reset link (threat-model §4.2).
    password_reset_ttl_seconds: int = 60 * 60  # 1 hour

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

    # ---------------------------------------------------------------------------
    # Observability (A6) — all optional, safe defaults = disabled.
    # No external accounts are required to run the app; these enable integrations
    # with self-hosted or cloud observability back-ends.
    # ---------------------------------------------------------------------------

    # Sentry error tracking (doc 06 §9). Set to a full DSN string to enable.
    # Leave unset (default) or empty for a no-op; no Sentry network calls are made.
    # Self-host options: Sentry OSS (https://develop.sentry.dev/self-hosted/) or
    # GlitchTip (https://glitchtip.com/). Source-map / release tagging: TODO LC-15.
    sentry_dsn: str | None = None
    # Sentry sample rates (0.0-1.0). Safe defaults = 10% errors, 5% traces.
    sentry_traces_sample_rate: float = 0.05
    sentry_profiles_sample_rate: float = 0.0

    # OpenTelemetry tracing (doc 06 §9). Set to your OTLP receiver endpoint
    # (e.g. "http://otel-collector:4317") to enable. Leave unset for no-op.
    # Exporter: OTLP/gRPC (opentelemetry-exporter-otlp-proto-grpc).
    # Self-host: run the OTel Collector + Tempo/Jaeger (see infra/observability/).
    otel_exporter_otlp_endpoint: str | None = None
    # OTLP headers as a comma-separated "key=value,..." string (for cloud providers
    # such as Honeycomb or Grafana Cloud that require an API key header).
    otel_exporter_otlp_headers: str | None = None
    # Service name reported in spans (defaults to the project name).
    otel_service_name: str = "civicsignals-api"
    # Sample ratio for OTel traces (1.0 = all; fraction = head-based sampling).
    otel_traces_sample_ratio: float = 1.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
