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
    # Invitation token TTL (B6). 7 days per doc 04 J7.
    invitation_ttl_seconds: int = 60 * 60 * 24 * 7  # 7 days

    # --- MFA / TOTP (B4) -----------------------------------------------------
    # Fernet key used to encrypt TOTP secrets at rest. Falls back to
    # ``secret_key`` in dev/self-host so no extra setup is needed. Production
    # MUST set a dedicated key (rotating ``secret_key`` would invalidate stored
    # TOTP secrets — users would need to re-enroll).
    mfa_totp_encryption_key: str | None = None
    # Number of backup/recovery codes to generate at MFA activation.
    mfa_backup_code_count: int = 10
    # TTL of the short-lived MFA challenge token (seconds). After the first
    # factor (email+password) succeeds and MFA is required, the server issues
    # a short-lived "mfa_challenge" JWT; the client presents it with the TOTP
    # code at /auth/mfa/verify to obtain the full access+refresh pair.
    mfa_challenge_ttl_seconds: int = 300  # 5 minutes
    # TOTP issuer name shown in authenticator apps.
    mfa_totp_issuer: str = "CivicSignals"

    # --- Google OAuth2 (B2) ---------------------------------------------------
    # Standard authorization-code flow (signed-state HMAC nonce; no PKCE).
    # Set GOOGLE_OAUTH_CLIENT_ID + SECRET to enable; leave unset (default) to
    # disable the /auth/oauth/google/* endpoints at runtime (they are mounted
    # regardless; a missing client-id returns 501).
    # GOOGLE_OAUTH_REDIRECT_URI MUST be set in production to the API callback
    # URL registered with Google, e.g.
    # ``https://api.civicsignals.io/api/v1/auth/oauth/google/callback``.
    # The redirect URI must point to the API server (not the web app) so Google
    # sends the authorization code directly to the server.
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None
    # The OAuth2 state nonce is signed+encoded in the redirect URL so the
    # callback can verify it without server-side session storage. The HMAC key
    # falls back to ``secret_key`` when unset.
    google_oauth_state_secret: str | None = None
    # Google token endpoint + userinfo — overridable in tests so no live Google
    # call is made. DO NOT change these in production; they exist solely for
    # testability (threat-model §4.2: no live vendor in CI).
    google_oauth_token_url: str = "https://oauth2.googleapis.com/token"
    google_oauth_userinfo_url: str = "https://www.googleapis.com/oauth2/v3/userinfo"
    # Where Google should redirect back to (must match the URI registered in
    # Google Cloud Console). Defaults to a local dev URL; always override in
    # staging/production via GOOGLE_OAUTH_REDIRECT_URI.
    google_oauth_redirect_uri: str | None = None

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

    # --- Smart-search daily LLM budget (I5) ----------------------------------
    # Soft cap on LLM-assisted smart-search calls per workspace per UTC day.
    # When a workspace reaches this limit the endpoint falls back to keyword-only
    # retrieval (BM25/FTS + structured filters, no LLM rewrite or summary) and
    # returns ``budget_exhausted=True`` in the response — never a hard error.
    # Set to 0 to disable the daily budget entirely (all requests go through the
    # full LLM-assisted path, which is appropriate for self-hosted deployments
    # where the operator controls LLM costs directly).
    # Env-var: SMART_SEARCH_DAILY_LLM_CALL_LIMIT
    smart_search_daily_llm_call_limit: int = Field(
        default=50,
        description=(
            "Per-workspace daily cap on LLM-assisted smart-search calls. "
            "When reached the endpoint falls back to keyword-only retrieval. "
            "Set to 0 to disable (unlimited)."
        ),
    )

    # --- Public-surface rate limiting (P4; doc 13 §4.6) ----------------------
    # Per-client (IP) fixed-window cap on the *public, unauthenticated* read
    # endpoints only: the public signal projection + source citations
    # (``/signals/{id}/public``, ``/signals/{id}/sources``) and the public entity
    # directory (list / get / children). Authenticated API routes are NOT rate
    # limited here (they have their own quotas, e.g. the I5 smart-search budget).
    #
    # The default — 120 requests / 60s per IP — is generous for a human browsing
    # the directory and for a crawler honouring the robots ``Crawl-delay`` (P4),
    # while throttling a tight scrape loop. The window is keyed in Redis (the same
    # store the D4 locks use) with an in-process fallback if Redis is unreachable.
    #
    # Env-vars: PUBLIC_RATE_LIMIT_PER_WINDOW / PUBLIC_RATE_LIMIT_WINDOW_SECONDS.
    # Set PUBLIC_RATE_LIMIT_PER_WINDOW to 0 to disable (e.g. when fronting the API
    # with your own WAF / edge rate limiting).
    public_rate_limit_per_window: int = Field(
        default=120,
        description=(
            "Max requests per window per client IP on the public unauthenticated "
            "endpoints. 0 disables the public rate limiter."
        ),
    )
    public_rate_limit_window_seconds: int = Field(
        default=60,
        description="Length of the public rate-limit fixed window, in seconds.",
    )

    # --- Embeddings (I1, doc 19 §4/§7.4) ------------------------------------
    # Each extracted signal is embedded into ``signals_signal.vector_embedding``
    # (a pgvector column) for fuzzy dedupe (E10) + smart-search / hybrid retrieval
    # (I3). Embeddings route through the gateway like completions (no module calls a
    # vendor SDK directly) but to a *separate* embeddings model — Anthropic has no
    # first-party embeddings endpoint, so the default provider is OpenAI.
    #
    # ``embedding_model`` is "provider:model" (or just "model", using
    # ``embedding_provider``). ``embedding_dim`` MUST equal the
    # ``signals_signal.vector_embedding`` column width (``signals.models``
    # ``EMBEDDING_DIM`` = 1536); the default model (OpenAI ``text-embedding-3-small``)
    # natively outputs 1536 dims, so they line up with no truncation. A self-hoster
    # pointing at an Ollama embeddings model whose native dim differs must set BOTH
    # this and a matching migration — the embed service refuses a dim mismatch
    # rather than silently writing a wrong-width vector.
    embedding_provider: Literal["openai", "ollama"] = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536

    api_v1_prefix: str = Field(default="/api/v1")

    # ---------------------------------------------------------------------------
    # Outbound integrations framework (K1).  The generic OAuth2 + push-log seam
    # that K2 (Salesforce), K3 (HubSpot), K4 (idempotent push), K5 (recovery),
    # L1 (Slack), L3 (webhooks) build on.  All optional — empty/unset disables
    # the providers that need them (the framework still loads).
    # ---------------------------------------------------------------------------
    # Symmetric key used to encrypt integration OAuth access/refresh tokens at
    # rest (Fernet, threat-model §4.2). Unlike API tokens (hashed, never
    # recovered), OAuth tokens must be decryptable to call the provider. A raw
    # 32-byte urlsafe-base64 Fernet key, OR any passphrase (it is HKDF/SHA-256
    # derived into a Fernet key — see integrations.services.token_cipher). Falls
    # back to ``secret_key`` in development so tests/dev run without extra setup;
    # production MUST set a dedicated key (rotating ``secret_key`` would also
    # invalidate stored tokens). TODO LC: provision a dedicated key in Cloud.
    integrations_token_encryption_key: str | None = None
    # Public base URL the provider redirects back to after OAuth consent. The
    # callback path (``/api/v1/integrations/oauth/callback``) is appended. In dev
    # this points at the API; in Cloud it is the public ingress host.
    integrations_oauth_redirect_base_url: str = "http://localhost:8000"
    # OAuth ``state`` token TTL (seconds): the signed state minted at /start must
    # be presented at /callback within this window (CSRF + replay guard).
    integrations_oauth_state_ttl_seconds: int = 600  # 10 minutes
    # Retry policy for failed pushes (K1 push-log; K5 recovery UI surfaces these).
    # ``retry_failed_pushes`` (Celery beat) retries up to ``max_attempts`` total
    # with exponential backoff (base * 2**(attempt-1), capped), then dead-letters.
    integrations_push_max_attempts: int = 5
    integrations_push_retry_base_seconds: int = 60
    integrations_push_retry_max_seconds: int = 60 * 60  # 1 hour cap

    # Per-provider OAuth2 client credentials (K2/K3/L1 wire the real clients;
    # K1 only needs the config seam). Empty/unset means that provider cannot be
    # connected via OAuth (POST returns a clear 422). TODO K2/K3/L1: document the
    # required scopes per provider once their connectors land.
    salesforce_client_id: str | None = None
    salesforce_client_secret: str | None = None
    hubspot_client_id: str | None = None
    hubspot_client_secret: str | None = None
    slack_client_id: str | None = None
    slack_client_secret: str | None = None

    # ---------------------------------------------------------------------------
    # Stripe billing (N1).  All optional — empty/unset means billing is inactive.
    # Live keys are provisioned by LC-13 (external Stripe account setup).
    # ``stripe_price_id_*`` are the Stripe price IDs for each self-serve plan;
    # populated by LC-13 / N2 when price objects are created in Stripe dashboard.
    # ---------------------------------------------------------------------------
    # TODO LC-13: set STRIPE_SECRET_KEY + STRIPE_WEBHOOK_SECRET from Stripe dashboard.
    # TODO N2: add per-plan price IDs once plan definitions land.
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    # Self-serve Stripe price ids (set by LC-13/N2; empty by default so tests pass
    # without a real Stripe account).
    stripe_price_id_solo: str | None = None
    stripe_price_id_starter: str | None = None
    stripe_price_id_pro: str | None = None

    # ---------------------------------------------------------------------------
    # Bounded-queue backpressure (D15; doc 18 §6.4).
    #
    # Per-queue soft + hard depth thresholds.  When the ``ingest`` queue depth
    # reaches the hard threshold the scheduler pauses new recipe dispatches;
    # once paused, it resumes only when the depth drops below the soft threshold
    # (hysteresis prevents flapping at the boundary).
    #
    # Defaults are sized for a single-node VPS (Phase 0) where a large backlog
    # signals that workers are behind and new enqueues would only worsen memory
    # pressure.  Scale them up on multi-node deployments with more worker_ingest
    # replicas.
    #
    # Env-vars: INGEST_QUEUE_SOFT_THRESHOLD / INGEST_QUEUE_HARD_THRESHOLD.
    # Constraint: soft < hard (enforced at runtime by get_thresholds()).
    # ---------------------------------------------------------------------------
    ingest_queue_soft_threshold: int = Field(
        default=500,
        description=(
            "Resume dispatching when the ingest queue depth drops below this value "
            "(after a hard-threshold pause). Must be < ingest_queue_hard_threshold."
        ),
    )
    ingest_queue_hard_threshold: int = Field(
        default=1000,
        description=(
            "Pause new recipe dispatches when the ingest queue depth reaches this value. "
            "Must be > ingest_queue_soft_threshold."
        ),
    )

    # Recipe DSL + runner (doc 18 §3). Both default to the monorepo layout
    # (discovered by walking up from the installed package) and can be overridden
    # in containers where the repo root sits elsewhere.
    recipe_schema_dir: str | None = None
    recipes_dir: str | None = None

    # --- Recipe drift detection (E7; doc 18 §3.2) ---------------------------
    # Rolling per-recipe metrics auto-pause a recipe whose extraction success rate
    # drops below ``drift_extraction_success_threshold`` (24h window) and the LLM
    # fallback rate over ``drift_llm_fallback_threshold`` (7d) is surfaced as a
    # degradation alert. Auto-pause stops new runs (D4's scheduler skips paused
    # recipes); past signals stay visible (doc 18 §3.2). Thresholds are guesses we
    # tune from real baselines (doc 18 §8) — hence configurable, not hard-coded.
    drift_extraction_success_threshold: float = 0.5
    drift_llm_fallback_threshold: float = 0.2
    # A recipe needs at least this many recorded runs in the window before
    # auto-pause can fire — a recipe with one bad run shouldn't trip on noise
    # (doc 18 §8: low-volume recipes false-alarm).
    drift_min_runs_for_pause: int = 3

    # GitHub auto-issue on auto-pause (doc 18 §3.2). When ``github_token`` is unset
    # the issue-opener is a no-op (dev / self-host without a token): drift still
    # pauses the recipe, it just doesn't file an issue. ``github_repo`` is
    # ``owner/name`` (defaults to the recipes repo). The token needs ``issues:write``
    # on that repo. Injectable/mockable via ``recipes.services.override_github_client``.
    github_token: str | None = None
    github_repo: str = "CivicSignals/monorepo"
    github_api_base_url: str = "https://api.github.com"

    # --- Headless browser fetcher (D2; doc 18 §1 cat. D, §4 "Headless browser") -
    # The Playwright-backed fetcher (``http_browser`` connector) renders JS-heavy
    # pages the static fetcher can't. It runs only on ``worker_ingest`` (the lean
    # ``api`` image carries neither the ``playwright`` extra nor the browser
    # binary), so these settings tune the per-worker browser pool.
    #
    # ``browser_pool_size`` bounds concurrent pages/contexts per worker process —
    # each can balloon to ~hundreds of MB, so this caps memory blast radius (doc
    # 18 §6.1). Burst is absorbed by ingest queue depth + HPA, not by an unbounded
    # pool (autoscaling on queue depth lives in O3's Helm chart — workerIngest.hpa
    # in infra/helm/.../templates/hpa.yaml).
    browser_pool_size: int = 4
    # Per-navigation timeout: a page that never finishes loading fails the fetch
    # rather than hanging the worker (doc 18 §4 "Page never finishes loading").
    browser_nav_timeout_ms: int = 30_000
    # Wait policy after navigation: "load" | "domcontentloaded" | "networkidle".
    # "networkidle" is the default for SPAs whose content only appears after the
    # async XHR/fetch settle; recipes that just need DOMContentLoaded can override.
    browser_wait_until: Literal["load", "domcontentloaded", "networkidle"] = "networkidle"
    # Bound time spent waiting for a recipe-supplied "ready" selector to appear
    # before giving up and returning whatever rendered.
    browser_selector_timeout_ms: int = 10_000
    # Time budget for launching the browser process on first use (a cold launch
    # has to start Chromium); separate from the per-navigation timeout above.
    browser_launch_timeout_ms: int = 30_000
    # Browser launch is headless; never run headful in containers. Exposed so a
    # local debugger can flip it.
    browser_headless: bool = True

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

    # Staff-only recipe preview gate (TODO D5). Real RBAC is TODO B7; until that
    # lands, the staff preview endpoint (POST /recipes/preview) is gated by a
    # shared token. Unset means "development convenience": the endpoint is open
    # only when `environment == "development"`. In staging/production an unset
    # token disables the endpoint entirely (fail-closed). See recipes/routes.py.
    recipe_preview_staff_token: str | None = None

    # ---------------------------------------------------------------------------
    # Anonymous telemetry ping (O6).  OFF by default; set
    # CIVICSIGNALS_TELEMETRY_ENABLED=true to opt in.  No PII is ever sent —
    # only coarse aggregate counts, version, and a random instance-id that is
    # NOT linked to any user or workspace.  See docs/self-host/telemetry.md.
    # ---------------------------------------------------------------------------
    civicsignals_telemetry_enabled: bool = False
    # Where to POST the anonymous ping.  Change if you run an internal telemetry
    # collector; leave unset to use the project default (noop until the endpoint
    # is live; a connection error is silently swallowed).
    civicsignals_telemetry_endpoint: str = "https://telemetry.civicsignals.io/v1/ping"
    # Path to a small state file that persists the random instance-id between
    # restarts.  Defaults to /tmp so no write permissions are needed; operators
    # can override to a persistent volume path so the id survives container
    # restarts.
    civicsignals_telemetry_id_path: str = "/tmp/civicsignals-instance-id"


@lru_cache
def get_settings() -> Settings:
    return Settings()
