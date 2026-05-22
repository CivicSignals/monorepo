"""Public service interface for the integrations module (K1 + L3).

Other modules call integrations only through the functions defined here — never
by importing integrations's models or routes directly (doc 06 §3). This is the
generic outbound-integration framework K2/K3/K4/K5/L1/L3 build on:

- **Secrets at rest.** :class:`TokenCipher` Fernet-encrypts OAuth access/refresh
  tokens and webhook HMAC secrets (they must be *recoverable* to call the provider
  / sign deliveries, so — unlike B8 API tokens, which are hashed — they are
  symmetrically encrypted). Secrets are never logged (threat-model §4.2).
- **OAuth flow.** :func:`start_oauth` builds the authorize URL + a signed,
  expiring ``state`` (CSRF/replay guard binding workspace + connection); the
  callback validates the state and exchanges the code via the provider, storing
  encrypted tokens. :func:`ensure_fresh_access_token` auto-refreshes on expiry.
- **Push framework.** :func:`execute_push` runs a provider push, records every
  attempt to the push-log with scope-aware typed errors, and computes the
  retry/dead-letter schedule. :func:`due_failed_pushes` is the surface the
  ``retry_failed_pushes`` Celery task drives.
- **Discovery + field mapping (K2).** :func:`discover_objects` /
  :func:`describe_object` populate the field-mapping UI from the provider's
  describe API; :func:`upsert_field_mapping` (and friends) persist a
  per-connection :class:`~.models.FieldMapping`; :func:`push_source` resolves the
  target, applies the mapping to a signal/pipeline-item, and runs the push.
- **Webhook subscriber CRUD + delivery (L3).** :func:`create_webhook_subscription`
  / :func:`list_webhook_subscriptions` / :func:`update_webhook_subscription` /
  :func:`delete_webhook_subscription` — workspace-scoped CRUD (RequireAdmin).
  :func:`deliver_event_to_subscribers` fans out one event to all matching active
  subscriptions (called from the events-bus handler registered at startup).
  :func:`execute_webhook_delivery` POSTs the HMAC-signed payload to one subscriber
  URL, recording the attempt in :class:`~.models.WebhookDelivery`.
  :func:`due_failed_webhook_deliveries` feeds the retry Celery task.

The HTTP layer is injectable (``http_client`` arg / a default factory) so tests
mock the transport.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
import uuid as _uuid_module
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.config import Settings, get_settings

from .models import (
    NON_RETRYABLE_ERROR_CODES,
    WEBHOOK_MAX_ATTEMPTS,
    WEBHOOK_RETRY_DELAYS,
    Connection,
    ConnectionStatus,
    FieldMapping,
    IntegrationProviderKind,
    PushErrorCode,
    PushLog,
    PushStatus,
    WebhookDelivery,
    WebhookDeliveryStatus,
    WebhookSubscription,
)
from .providers import (
    FieldDescriptor,
    IntegrationProvider,
    ObjectDescriptor,
    ProviderError,
    PushRequest,
    PushResult,
    TokenSet,
    get_provider,
    is_registered,
)

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class IntegrationError(Exception):
    """Base for integration-layer failures the routes map to RFC 7807 problems."""


class ProviderNotConfiguredError(IntegrationError):
    """The requested provider has no OAuth client wired up (settings missing)."""


class ProviderNotRegisteredError(IntegrationError):
    """No provider class is registered for the requested kind (K2/K3/L1 pending)."""


class OAuthStateError(IntegrationError):
    """The OAuth callback ``state`` was missing, tampered, expired, or replayed."""


class ConnectionNotConnectedError(IntegrationError):
    """A push/refresh was attempted on a connection with no usable token."""


class DiscoveryNotSupportedError(IntegrationError):
    """The connection's provider does not support object/field discovery (K2)."""


class DiscoveryFailedError(IntegrationError):
    """A discovery call to the provider failed (auth/transient/etc.; K2).

    Carries the scope-aware :class:`PushErrorCode` so the route can branch (e.g.
    ``auth`` → prompt reconnect) the same way the push runner does.
    """

    def __init__(self, code: PushErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Token encryption at rest (Fernet)
# ---------------------------------------------------------------------------


class TokenCipher:
    """Symmetric encryption for integration OAuth tokens at rest (K1).

    OAuth access/refresh tokens must be **decryptable** (to call the provider),
    so — unlike B8 API tokens (hashed, never recovered) — they are Fernet-
    encrypted. The key is taken from ``integrations_token_encryption_key`` (a raw
    Fernet key if it looks like one, else any passphrase HKDF/SHA-256-derived
    into a 32-byte Fernet key). Falls back to ``secret_key`` in dev so tests run
    without extra setup. Plaintext tokens never leave this object as logs.
    """

    def __init__(self, key_material: str) -> None:
        self._fernet = Fernet(self._derive_key(key_material))

    @staticmethod
    def _derive_key(key_material: str) -> bytes:
        # Accept a ready-made urlsafe-base64 32-byte Fernet key verbatim so
        # operators can rotate with a real key; otherwise derive one so any
        # passphrase works (dev/self-host convenience).
        raw = key_material.encode("utf-8")
        try:
            decoded = base64.urlsafe_b64decode(raw)
            if len(decoded) == 32:
                return raw
        except (binascii.Error, ValueError):
            pass
        digest = hashlib.sha256(raw).digest()
        return base64.urlsafe_b64encode(digest)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise IntegrationError("token decryption failed (wrong key?)") from exc


def token_cipher(settings: Settings | None = None) -> TokenCipher:
    """Return the configured :class:`TokenCipher` (dev falls back to secret_key)."""
    settings = settings or get_settings()
    key = settings.integrations_token_encryption_key or settings.secret_key
    return TokenCipher(key)


# ---------------------------------------------------------------------------
# OAuth ``state`` signing (CSRF + replay guard)
# ---------------------------------------------------------------------------
# The ``state`` round-trips the workspace + connection id through the provider's
# consent screen. It is HMAC-signed (so a forged state is rejected) and carries
# a timestamp (so a captured state expires). It is *not* a session — it only
# binds the callback back to the connection it started.


def _state_secret(settings: Settings) -> bytes:
    return (settings.jwt_secret or settings.secret_key).encode("utf-8")


def sign_oauth_state(
    *, workspace_id: UUID, connection_id: UUID, settings: Settings | None = None
) -> str:
    """Mint a signed, timestamped OAuth ``state`` binding workspace+connection."""
    settings = settings or get_settings()
    payload = {
        "w": str(workspace_id),
        "c": str(connection_id),
        "n": secrets.token_urlsafe(8),
        "t": int(time.time()),
    }
    body = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    sig = hmac.new(_state_secret(settings), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


@dataclass(frozen=True, slots=True)
class OAuthState:
    workspace_id: UUID
    connection_id: UUID


def verify_oauth_state(state: str, *, settings: Settings | None = None) -> OAuthState:
    """Validate a signed ``state`` and return its bound ids, or raise.

    Rejects (with :class:`OAuthStateError`) a missing, malformed, tampered
    (bad HMAC), or expired (older than the configured TTL) state.
    """
    settings = settings or get_settings()
    try:
        body, sig = state.split(".", 1)
    except ValueError as exc:
        raise OAuthStateError("malformed state") from exc
    expected = hmac.new(_state_secret(settings), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise OAuthStateError("state signature mismatch")
    try:
        payload = json.loads(base64.urlsafe_b64decode(body.encode("ascii")))
        issued_at = int(payload["t"])
        workspace_id = UUID(str(payload["w"]))
        connection_id = UUID(str(payload["c"]))
    except (binascii.Error, ValueError, KeyError, TypeError) as exc:
        raise OAuthStateError("unreadable state") from exc
    if time.time() - issued_at > settings.integrations_oauth_state_ttl_seconds:
        raise OAuthStateError("state expired")
    return OAuthState(workspace_id=workspace_id, connection_id=connection_id)


# ---------------------------------------------------------------------------
# HTTP client factory (injectable for tests)
# ---------------------------------------------------------------------------


def default_http_client() -> httpx.AsyncClient:
    """Create the default async HTTP client used for provider calls."""
    return httpx.AsyncClient(timeout=30.0)


def _resolve_provider(
    kind: IntegrationProviderKind, settings: Settings, http: httpx.AsyncClient
) -> IntegrationProvider:
    if not is_registered(kind):
        raise ProviderNotRegisteredError(f"no provider registered for '{kind.value}'")
    return get_provider(kind, settings, http)


# ---------------------------------------------------------------------------
# Connection CRUD (workspace-scoped, B5)
# ---------------------------------------------------------------------------


async def create_connection(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    provider: IntegrationProviderKind,
    name: str,
    created_by_user_id: UUID | None,
    default_targets: Sequence[str] | None = None,
) -> Connection:
    """Create a connection in ``pending_oauth`` (or pending-secret) state.

    The caller commits. For OAuth providers the connection is created first so
    the OAuth ``state`` can bind to its id; tokens are filled in by the callback.
    """
    connection = Connection(
        workspace_id=workspace_id,
        provider=provider,
        name=name,
        status=ConnectionStatus.PENDING_OAUTH,
        created_by_user_id=created_by_user_id,
        default_targets=list(default_targets or []),
    )
    session.add(connection)
    await session.flush()
    return connection


async def get_connection(
    session: AsyncSession, workspace_id: UUID, connection_id: UUID
) -> Connection | None:
    """Return a connection by id *scoped to ``workspace_id``*, or ``None``.

    Scoping to the workspace here (not just by id) is the isolation guard: a
    caller in workspace A can never resolve workspace B's connection.
    """
    result = await session.execute(
        select(Connection).where(
            Connection.id == connection_id,
            Connection.workspace_id == workspace_id,
        )
    )
    return result.scalar_one_or_none()


async def get_connection_unscoped(session: AsyncSession, connection_id: UUID) -> Connection | None:
    """Return a connection by id alone (OAuth callback resolves via signed state).

    Used only after :func:`verify_oauth_state` has already proven the caller is
    completing the flow it started; the state carries the workspace binding.
    """
    result = await session.execute(select(Connection).where(Connection.id == connection_id))
    return result.scalar_one_or_none()


async def list_connections(session: AsyncSession, workspace_id: UUID) -> list[Connection]:
    """List a workspace's connections, newest first.

    Includes revoked rows so a reconnect is discoverable in the UI (doc 04 J7).
    """
    result = await session.execute(
        select(Connection)
        .where(Connection.workspace_id == workspace_id)
        .order_by(Connection.id.desc())
    )
    return list(result.scalars().all())


async def delete_connection(session: AsyncSession, connection: Connection) -> None:
    """Hard-delete a connection (admin disconnect). The caller commits.

    Push-log rows cascade-delete with the connection (the connection is the
    evidence anchor; a disconnected provider's pushes are no longer actionable).
    """
    await session.delete(connection)
    await session.flush()


# ---------------------------------------------------------------------------
# OAuth flow
# ---------------------------------------------------------------------------


def oauth_redirect_uri(settings: Settings | None = None) -> str:
    """The fixed callback URI the provider redirects back to after consent."""
    settings = settings or get_settings()
    base = settings.integrations_oauth_redirect_base_url.rstrip("/")
    return f"{base}{settings.api_v1_prefix}/integrations/oauth/callback"


@dataclass(frozen=True, slots=True)
class OAuthStart:
    """The authorize redirect for a freshly-created OAuth connection."""

    connection: Connection
    redirect_url: str


def build_authorize_url(provider: IntegrationProvider, *, state: str, redirect_uri: str) -> str:
    """Build the provider's authorize URL with our client/scope/state params."""
    config = provider.oauth_config()
    params = {
        "response_type": "code",
        "client_id": config.client_id or "",
        "redirect_uri": redirect_uri,
        "scope": " ".join(config.scopes),
        "state": state,
        **config.extra_authorize_params,
    }
    sep = "&" if "?" in config.authorize_url else "?"
    return f"{config.authorize_url}{sep}{urlencode(params)}"


async def start_oauth(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    provider: IntegrationProviderKind,
    name: str,
    created_by_user_id: UUID | None,
    settings: Settings | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> OAuthStart:
    """Create a pending connection and return the provider authorize redirect.

    Raises :class:`ProviderNotRegisteredError` (K2/K3/L1 not landed yet) or
    :class:`ProviderNotConfiguredError` (operator hasn't supplied client creds).
    The caller commits.
    """
    settings = settings or get_settings()
    http = http_client or default_http_client()
    own_http = http_client is None
    try:
        prov = _resolve_provider(provider, settings, http)
        # Non-OAuth providers (is_oauth=False, e.g. L3 webhook) are not managed
        # via the OAuth connections flow. They are managed by their own dedicated
        # endpoints (/webhooks). Raise as if unregistered so the caller gets a
        # clear 422 (the provider IS registered, but the OAuth flow does not apply).
        if not prov.is_oauth:
            raise ProviderNotRegisteredError(
                f"provider '{provider.value}' does not use OAuth — use its dedicated endpoints"
            )
        config = prov.oauth_config()
        if not config.configured:
            raise ProviderNotConfiguredError(
                f"provider '{provider.value}' OAuth client is not configured"
            )
        connection = await create_connection(
            session,
            workspace_id=workspace_id,
            provider=provider,
            name=name,
            created_by_user_id=created_by_user_id,
        )
        state = sign_oauth_state(
            workspace_id=workspace_id, connection_id=connection.id, settings=settings
        )
        redirect_url = build_authorize_url(
            prov, state=state, redirect_uri=oauth_redirect_uri(settings)
        )
        return OAuthStart(connection=connection, redirect_url=redirect_url)
    finally:
        if own_http:
            await http.aclose()


def _apply_tokenset(connection: Connection, tokens: TokenSet, cipher: TokenCipher) -> None:
    """Persist a :class:`TokenSet` onto a connection (tokens encrypted)."""
    connection.access_token_encrypted = cipher.encrypt(tokens.access_token)
    if tokens.refresh_token is not None:
        connection.refresh_token_encrypted = cipher.encrypt(tokens.refresh_token)
    connection.token_expires_at = tokens.expires_at
    if tokens.scopes:
        connection.scopes = list(tokens.scopes)
    if tokens.provider_account:
        connection.provider_account = dict(tokens.provider_account)
    connection.status = ConnectionStatus.HEALTHY
    if connection.connected_at is None:
        connection.connected_at = datetime.now(UTC)


async def complete_oauth(
    session: AsyncSession,
    *,
    code: str,
    state: str,
    settings: Settings | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> Connection:
    """Validate the callback ``state``, exchange ``code``, store encrypted tokens.

    Raises :class:`OAuthStateError` (bad/expired state) or
    :class:`IntegrationError` (unknown connection / exchange failure). The caller
    commits.
    """
    settings = settings or get_settings()
    parsed = verify_oauth_state(state, settings=settings)
    connection = await get_connection_unscoped(session, parsed.connection_id)
    if connection is None or connection.workspace_id != parsed.workspace_id:
        raise IntegrationError("unknown connection for this state")

    http = http_client or default_http_client()
    own_http = http_client is None
    try:
        prov = _resolve_provider(connection.provider, settings, http)
        try:
            tokens = await prov.exchange_code(code=code, redirect_uri=oauth_redirect_uri(settings))
        except ProviderError as exc:
            connection.status = ConnectionStatus.NEEDS_REAUTH
            await session.flush()
            raise IntegrationError(f"token exchange failed: {exc.message}") from exc
    finally:
        if own_http:
            await http.aclose()

    _apply_tokenset(connection, tokens, token_cipher(settings))
    await session.flush()
    return connection


async def ensure_fresh_access_token(
    session: AsyncSession,
    connection: Connection,
    *,
    settings: Settings | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    """Return a usable plaintext access token, refreshing it if expired.

    The auto-refresh seam: if the stored token is expired (or missing) and a
    refresh token is present, exchange it and persist the new (encrypted) token.
    Raises :class:`ConnectionNotConnectedError` when there is nothing to use and
    marks the connection ``needs_reauth`` when the refresh itself fails. The
    caller commits.
    """
    settings = settings or get_settings()
    cipher = token_cipher(settings)

    if connection.access_token_encrypted is None:
        raise ConnectionNotConnectedError("connection has no access token")

    if not connection.is_token_expired:
        return cipher.decrypt(connection.access_token_encrypted)

    if connection.refresh_token_encrypted is None:
        connection.status = ConnectionStatus.NEEDS_REAUTH
        await session.flush()
        raise ConnectionNotConnectedError("access token expired and no refresh token")

    refresh_token = cipher.decrypt(connection.refresh_token_encrypted)
    http = http_client or default_http_client()
    own_http = http_client is None
    try:
        prov = _resolve_provider(connection.provider, settings, http)
        try:
            tokens = await prov.refresh(refresh_token=refresh_token)
        except ProviderError as exc:
            connection.status = ConnectionStatus.NEEDS_REAUTH
            await session.flush()
            raise ConnectionNotConnectedError(f"token refresh failed: {exc.message}") from exc
    finally:
        if own_http:
            await http.aclose()

    _apply_tokenset(connection, tokens, cipher)
    await session.flush()
    return tokens.access_token


# ---------------------------------------------------------------------------
# Object / field discovery (K2 field-mapping UI)
# ---------------------------------------------------------------------------


async def discover_objects(
    session: AsyncSession,
    connection: Connection,
    *,
    settings: Settings | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> list[ObjectDescriptor]:
    """List the provider objects this connection can push to (K2).

    Auto-refreshes the access token first, then queries the provider describe.
    Raises :class:`DiscoveryNotSupportedError` (provider has no discovery, e.g.
    webhook), :class:`ConnectionNotConnectedError` (no usable token), or
    :class:`DiscoveryFailedError` (a provider-side error, with scope-aware code).
    """
    settings = settings or get_settings()
    http = http_client or default_http_client()
    own_http = http_client is None
    try:
        prov = _resolve_provider(connection.provider, settings, http)
        if not prov.supports_discovery:
            raise DiscoveryNotSupportedError(
                f"provider '{connection.provider.value}' does not support discovery"
            )
        access_token = await ensure_fresh_access_token(
            session, connection, settings=settings, http_client=http
        )
        try:
            return await prov.discover_objects(
                access_token=access_token, provider_account=dict(connection.provider_account)
            )
        except ProviderError as exc:
            raise DiscoveryFailedError(exc.code, exc.message) from exc
    finally:
        if own_http:
            await http.aclose()


async def describe_object(
    session: AsyncSession,
    connection: Connection,
    *,
    object_name: str,
    settings: Settings | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> list[FieldDescriptor]:
    """List the writable fields on ``object_name`` for this connection (K2)."""
    settings = settings or get_settings()
    http = http_client or default_http_client()
    own_http = http_client is None
    try:
        prov = _resolve_provider(connection.provider, settings, http)
        if not prov.supports_discovery:
            raise DiscoveryNotSupportedError(
                f"provider '{connection.provider.value}' does not support discovery"
            )
        access_token = await ensure_fresh_access_token(
            session, connection, settings=settings, http_client=http
        )
        try:
            return await prov.describe_object(
                access_token=access_token,
                provider_account=dict(connection.provider_account),
                object_name=object_name,
            )
        except ProviderError as exc:
            raise DiscoveryFailedError(exc.code, exc.message) from exc
    finally:
        if own_http:
            await http.aclose()


# ---------------------------------------------------------------------------
# Field mapping CRUD (K2; per connection + target object)
# ---------------------------------------------------------------------------


async def list_field_mappings(
    session: AsyncSession, *, workspace_id: UUID, connection_id: UUID
) -> list[FieldMapping]:
    """List a connection's field mappings (workspace-scoped)."""
    result = await session.execute(
        select(FieldMapping)
        .where(
            FieldMapping.workspace_id == workspace_id,
            FieldMapping.connection_id == connection_id,
        )
        .order_by(FieldMapping.target_object.asc())
    )
    return list(result.scalars().all())


async def get_field_mapping(
    session: AsyncSession, *, workspace_id: UUID, connection_id: UUID, target_object: str
) -> FieldMapping | None:
    """Return the mapping for (connection, target object), or ``None``."""
    result = await session.execute(
        select(FieldMapping).where(
            FieldMapping.workspace_id == workspace_id,
            FieldMapping.connection_id == connection_id,
            FieldMapping.target_object == target_object,
        )
    )
    return result.scalar_one_or_none()


async def upsert_field_mapping(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    connection_id: UUID,
    target_object: str,
    field_map: dict[str, object],
    constants: dict[str, object] | None = None,
) -> FieldMapping:
    """Create or update the mapping for (connection, target object). Caller commits.

    The field-mapping UI saves one mapping per target object; re-saving the same
    target replaces ``field_map``/``constants`` (the K6 template seam reuses this).
    """
    mapping = await get_field_mapping(
        session,
        workspace_id=workspace_id,
        connection_id=connection_id,
        target_object=target_object,
    )
    if mapping is None:
        mapping = FieldMapping(
            workspace_id=workspace_id,
            connection_id=connection_id,
            target_object=target_object,
            field_map=dict(field_map),
            constants=dict(constants or {}),
        )
        session.add(mapping)
    else:
        mapping.field_map = dict(field_map)
        mapping.constants = dict(constants or {})
    await session.flush()
    return mapping


async def delete_field_mapping(session: AsyncSession, mapping: FieldMapping) -> None:
    """Delete a field mapping. Caller commits."""
    await session.delete(mapping)
    await session.flush()


# ---------------------------------------------------------------------------
# Apply a field mapping to a source object → provider payload (K2)
# ---------------------------------------------------------------------------


def resolve_source_path(source: dict[str, object], path: str) -> object | None:
    """Resolve a dotted ``path`` (e.g. ``signal.title``) against ``source``.

    The push caller supplies a plain ``source`` dict (the signal / pipeline-item
    field values, already serialized — integrations never imports other modules'
    models, doc 06 §3). Missing keys resolve to ``None`` (the field is omitted).
    """
    node: object | None = source
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


def apply_field_mapping(
    mapping: FieldMapping | None, source: dict[str, object]
) -> dict[str, object]:
    """Build the provider field→value body from a mapping + a source object (K2).

    Constants are written verbatim; mapped fields resolve their source path and
    are omitted when the source value is ``None`` (so a push never sends an
    explicit null for an unmapped value). With no mapping, the (already-shaped)
    ``source`` is passed through unchanged so a connection can push without first
    configuring a mapping.
    """
    if mapping is None:
        return dict(source)
    payload: dict[str, object] = dict(mapping.constants)
    for provider_field, source_path in mapping.field_map.items():
        if not isinstance(source_path, str):
            continue
        value = resolve_source_path(source, source_path)
        if value is not None:
            payload[provider_field] = value
    return payload


# ---------------------------------------------------------------------------
# Idempotency key derivation (K4)
# ---------------------------------------------------------------------------


def make_idempotency_key(
    *,
    workspace_id: UUID,
    connection_id: UUID,
    target: str,
    signal_id: str | None = None,
    pipeline_item_id: str | None = None,
    extra: str | None = None,
) -> str:
    """Derive a stable, deterministic idempotency key for an outbound push (K4).

    The key is a URL-safe SHA-256 hex digest over the 4-tuple
    ``(workspace_id, connection_id, source_record_id, target)`` — the same
    logical push always produces the same key regardless of payload content.

    ``signal_id`` is used when available; fall back to ``pipeline_item_id``;
    ``extra`` is an escape hatch for callers that supply their own source
    record identifier. At least one source record identifier must be given.

    The hex digest is truncated to 64 characters (256 bits of input entropy
    → 32 hex bytes → well within the 255-char column limit). The key is
    workspace-scoped and connection-scoped so two workspaces' pushes of the
    same signal never collide even if they share a CRM.
    """
    source_record_id = signal_id or pipeline_item_id or extra
    if not source_record_id:
        raise ValueError(
            "make_idempotency_key requires at least one of: signal_id, pipeline_item_id, or extra"
        )
    raw = json.dumps(
        [str(workspace_id), str(connection_id), source_record_id, target],
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# Push framework + push-log
# ---------------------------------------------------------------------------


def _retry_delay_seconds(attempt: int, settings: Settings) -> int:
    """Exponential backoff (base * 2**(attempt-1)) capped at the configured max."""
    base = settings.integrations_push_retry_base_seconds
    delay: int = base * (2 ** max(0, attempt - 1))
    cap: int = settings.integrations_push_retry_max_seconds
    return delay if delay < cap else cap


async def create_push_log(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    connection_id: UUID,
    target: str,
    request: dict[str, object],
    signal_id: str | None = None,
    pipeline_item_id: str | None = None,
    idempotency_key: str | None = None,
) -> PushLog:
    """Create a ``pending`` push-log row for an outbound push. Caller commits."""
    log = PushLog(
        workspace_id=workspace_id,
        connection_id=connection_id,
        target=target,
        request=dict(request),
        signal_id=signal_id,
        pipeline_item_id=pipeline_item_id,
        idempotency_key=idempotency_key,
        status=PushStatus.PENDING,
        attempt_count=0,
    )
    session.add(log)
    await session.flush()
    return log


def _record_success(log: PushLog, result: PushResult) -> None:
    log.status = PushStatus.SUCCESS
    log.external_id = result.external_id
    log.response = dict(result.response)
    log.provider_response_id = result.provider_response_id
    log.error_code = None
    log.error_message = None
    log.retry_at = None
    log.attempted_at = datetime.now(UTC)
    log.attempt_count += 1


def _record_failure(log: PushLog, exc: ProviderError, settings: Settings) -> None:
    log.attempt_count += 1
    log.attempted_at = datetime.now(UTC)
    log.error_code = exc.code
    log.error_message = exc.message
    log.provider_response_id = exc.provider_response_id
    if exc.response is not None:
        log.response = dict(exc.response)

    exhausted = log.attempt_count >= settings.integrations_push_max_attempts
    non_retryable = exc.code in NON_RETRYABLE_ERROR_CODES
    if exhausted or non_retryable:
        log.status = PushStatus.DEAD_LETTER
        log.retry_at = None
    else:
        log.status = PushStatus.FAILED
        log.retry_at = datetime.now(UTC) + timedelta(
            seconds=_retry_delay_seconds(log.attempt_count, settings)
        )


async def execute_push(
    session: AsyncSession,
    *,
    connection: Connection,
    log: PushLog,
    request: PushRequest,
    settings: Settings | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> PushLog:
    """Run one push attempt against the provider and record it to the push-log.

    Auto-refreshes the access token first (so a 401 is avoided where possible),
    then calls the provider's ``push``. On success records the external id; on a
    :class:`ProviderError` records the scope-aware typed error and computes the
    retry/dead-letter schedule. An ``auth`` failure also flips the connection to
    ``needs_reauth`` (K5 recovery). Never raises on a provider failure — the
    outcome lives in the returned log. The caller commits.
    """
    settings = settings or get_settings()
    http = http_client or default_http_client()
    own_http = http_client is None
    try:
        prov = _resolve_provider(connection.provider, settings, http)
        try:
            access_token = await ensure_fresh_access_token(
                session, connection, settings=settings, http_client=http
            )
        except ConnectionNotConnectedError as exc:
            _record_failure(log, ProviderError(PushErrorCode.AUTH, str(exc)), settings)
            connection.status = ConnectionStatus.NEEDS_REAUTH
            await session.flush()
            return log

        # Inject the connection's non-secret provider account metadata (e.g. the
        # Salesforce ``instance_url``) so the provider can route the REST call to
        # the right org. This is a control key (``__``-prefixed) the provider pops
        # off; it is NOT what was persisted to the push-log ``request`` (that is
        # the clean mapped payload set at ``create_push_log``), so retries that
        # rebuild the request from the log still get a fresh, correct account.
        enriched_payload = dict(request.payload)
        enriched_payload["__provider_account__"] = dict(connection.provider_account or {})
        enriched = PushRequest(
            target=request.target,
            payload=enriched_payload,
            idempotency_key=request.idempotency_key,
            external_id=request.external_id,
        )
        try:
            result = await prov.push(access_token=access_token, request=enriched)
        except ProviderError as exc:
            _record_failure(log, exc, settings)
            if exc.code is PushErrorCode.AUTH:
                connection.status = ConnectionStatus.NEEDS_REAUTH
            elif exc.code in (PushErrorCode.RATE_LIMITED, PushErrorCode.TRANSIENT):
                connection.status = ConnectionStatus.DEGRADED
            await session.flush()
            return log
    finally:
        if own_http:
            await http.aclose()

    _record_success(log, result)
    connection.last_push_at = datetime.now(UTC)
    connection.status = ConnectionStatus.HEALTHY
    await session.flush()
    return log


def _resolve_target(connection: Connection, target: str | None) -> str:
    """Resolve the provider-qualified push target (e.g. ``salesforce.Opportunity``).

    Uses the explicit ``target`` if given, else the connection's first
    ``default_targets`` entry, else ``Opportunity`` for Salesforce. Always
    returns a provider-qualified ``<provider>.<object>`` string for the push-log.
    """
    provider = connection.provider.value
    obj: str
    if target:
        # Accept either a bare object name or an already-qualified target.
        obj = target.split(".", 1)[1] if target.startswith(f"{provider}.") else target
    elif connection.default_targets:
        obj = connection.default_targets[0]
    else:
        obj = "Opportunity"
    return f"{provider}.{obj}"


async def push_source(
    session: AsyncSession,
    *,
    connection: Connection,
    source: dict[str, object],
    target: str | None = None,
    field_map_override: dict[str, object] | None = None,
    signal_id: str | None = None,
    pipeline_item_id: str | None = None,
    idempotency_key: str | None = None,
    settings: Settings | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> PushLog:
    """Push one signal/pipeline-item to the connection's provider (K4 idempotent).

    Resolves the target object, loads the connection's saved :class:`FieldMapping`
    for that object (an inline ``field_map_override`` from the request wins), maps
    the ``source`` field values to the provider body, records a push-log row, and
    runs :func:`execute_push`.

    **Idempotency (K4):** If no ``idempotency_key`` is supplied but a
    ``signal_id`` or ``pipeline_item_id`` is, one is derived deterministically
    via :func:`make_idempotency_key` so re-pushing the same logical record always
    resolves to the same key. Before pushing we look up any prior *successful*
    push for that key; if found, its ``external_id`` is passed to the provider so
    the push is a CRM update, not a duplicate create.

    **Race guard:** two concurrent pushes of the same key may both see no prior
    success and both call the provider's create. The unique partial index on
    ``(connection_id, idempotency_key)`` WHERE ``status = 'success'`` prevents
    both rows from being committed as successes. When the second push encounters
    the ``IntegrityError`` it rolls back its success update, looks up the
    ``external_id`` persisted by the first push, and re-runs against the provider
    as an update — recording its own success with the same external id.

    Never raises on a provider failure — the outcome lives in the returned log.
    The caller commits.
    """
    settings = settings or get_settings()
    qualified_target = _resolve_target(connection, target)
    object_name = qualified_target.split(".", 1)[1]

    mapping = await get_field_mapping(
        session,
        workspace_id=connection.workspace_id,
        connection_id=connection.id,
        target_object=object_name,
    )
    if field_map_override is not None:
        # An inline override is applied on top of the saved mapping (or alone).
        override = FieldMapping(
            workspace_id=connection.workspace_id,
            connection_id=connection.id,
            target_object=object_name,
            field_map=dict(field_map_override),
            constants=dict(mapping.constants) if mapping is not None else {},
        )
        payload = apply_field_mapping(override, source)
    else:
        payload = apply_field_mapping(mapping, source)

    # K4: auto-derive a stable idempotency key from the source record identity
    # when the caller did not supply one explicitly.
    resolved_key: str | None = idempotency_key
    if resolved_key is None and (signal_id or pipeline_item_id):
        resolved_key = make_idempotency_key(
            workspace_id=connection.workspace_id,
            connection_id=connection.id,
            target=qualified_target,
            signal_id=signal_id,
            pipeline_item_id=pipeline_item_id,
        )

    # K4 seam: a prior successful push of the same source object (same
    # idempotency key) re-pushes as an update against its external id.
    prior_external_id: str | None = None
    if resolved_key:
        prior = await _last_successful_push(
            session, connection_id=connection.id, idempotency_key=resolved_key
        )
        if prior is not None:
            prior_external_id = prior.external_id

    log = await create_push_log(
        session,
        workspace_id=connection.workspace_id,
        connection_id=connection.id,
        target=qualified_target,
        request=payload,
        signal_id=signal_id,
        pipeline_item_id=pipeline_item_id,
        idempotency_key=resolved_key,
    )
    request = PushRequest(
        target=qualified_target,
        payload=payload,
        idempotency_key=resolved_key,
        external_id=prior_external_id,
    )
    try:
        return await execute_push(
            session,
            connection=connection,
            log=log,
            request=request,
            settings=settings,
            http_client=http_client,
        )
    except IntegrityError:
        # K4 race guard: the unique partial index on (connection_id,
        # idempotency_key) WHERE status='success' fired — a concurrent push of
        # the same key already committed a success with an external_id. Roll
        # back the session to a clean state, fetch the winner's external_id, and
        # re-run the push as an update so this attempt also records a success
        # without creating a duplicate CRM object.
        await session.rollback()
        winner = await _last_successful_push(
            session,
            connection_id=connection.id,
            idempotency_key=resolved_key or "",
        )
        winner_external_id = winner.external_id if winner is not None else None
        log2 = await create_push_log(
            session,
            workspace_id=connection.workspace_id,
            connection_id=connection.id,
            target=qualified_target,
            request=payload,
            signal_id=signal_id,
            pipeline_item_id=pipeline_item_id,
            idempotency_key=resolved_key,
        )
        retry_request = PushRequest(
            target=qualified_target,
            payload=payload,
            idempotency_key=resolved_key,
            external_id=winner_external_id,
        )
        return await execute_push(
            session,
            connection=connection,
            log=log2,
            request=retry_request,
            settings=settings,
            http_client=http_client,
        )


async def _last_successful_push(
    session: AsyncSession, *, connection_id: UUID, idempotency_key: str
) -> PushLog | None:
    """Return the most recent successful push for an idempotency key (K4 seam)."""
    result = await session.execute(
        select(PushLog)
        .where(
            PushLog.connection_id == connection_id,
            PushLog.idempotency_key == idempotency_key,
            PushLog.status == PushStatus.SUCCESS,
            PushLog.external_id.is_not(None),
        )
        .order_by(PushLog.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Retry sweeper surface (driven by the retry_failed_pushes Celery task)
# ---------------------------------------------------------------------------


async def due_failed_pushes(
    session: AsyncSession, *, now: datetime | None = None, limit: int = 100
) -> list[PushLog]:
    """Return failed push-log rows whose ``retry_at`` is due (oldest first)."""
    now = now or datetime.now(UTC)
    result = await session.execute(
        select(PushLog)
        .where(
            PushLog.status == PushStatus.FAILED,
            PushLog.retry_at.is_not(None),
            PushLog.retry_at <= now,
        )
        .order_by(PushLog.retry_at.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Push-log read surface (cursor pagination, same UUID-v7 keyset as admin/B9)
# ---------------------------------------------------------------------------

DEFAULT_LIMIT: int = 25
MAX_LIMIT: int = 100


def _encode_cursor(row_id: UUID) -> str:
    return base64.urlsafe_b64encode(row_id.bytes).decode("ascii")


def _decode_cursor(cursor: str) -> UUID:
    try:
        return _uuid_module.UUID(bytes=base64.urlsafe_b64decode(cursor.encode("ascii")))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid cursor") from exc


@dataclass(slots=True)
class PushLogPage:
    items: list[PushLog]
    next_cursor: str | None


async def list_push_log(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
    connection_id: UUID | None = None,
    status: PushStatus | None = None,
) -> PushLogPage:
    """Return a cursor-paginated, newest-first page of push-log rows.

    Scoped to ``workspace_id`` (workspace isolation). Optional filters narrow by
    connection or status. ``cursor`` is the opaque ``next_cursor`` returned
    verbatim (keyset on the time-ordered UUID v7 id).
    """
    limit = max(1, min(limit, MAX_LIMIT))
    stmt = select(PushLog).where(PushLog.workspace_id == workspace_id).order_by(PushLog.id.desc())
    if connection_id is not None:
        stmt = stmt.where(PushLog.connection_id == connection_id)
    if status is not None:
        stmt = stmt.where(PushLog.status == status)
    if cursor is not None:
        stmt = stmt.where(PushLog.id < _decode_cursor(cursor))
    stmt = stmt.limit(limit + 1)

    rows = list((await session.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = _encode_cursor(items[-1].id) if has_more and items else None
    return PushLogPage(items=items, next_cursor=next_cursor)


# ---------------------------------------------------------------------------
# L3: Webhook subscription CRUD
# ---------------------------------------------------------------------------

# Prefix for the HMAC secret shown to the admin at create time (doc 08 §3.6).
_WEBHOOK_SECRET_PREFIX = "whsec_"
# Number of random bytes in the secret (32 bytes ≈ 43 base64 chars).
_WEBHOOK_SECRET_BYTES = 32
# Maximum response body to store per delivery attempt (diagnostic only).
_MAX_RESPONSE_BODY_BYTES = 4096


def generate_webhook_secret() -> str:
    """Generate a fresh HMAC secret for a new webhook subscription (L3).

    Returns a ``whsec_<base64url>`` string (URL-safe base64, no padding).
    The caller displays it *once* to the admin (like B8 API tokens) and then
    passes it to :func:`store_webhook_secret` to encrypt at rest.
    """
    raw = secrets.token_bytes(_WEBHOOK_SECRET_BYTES)
    return _WEBHOOK_SECRET_PREFIX + base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def store_webhook_secret(plaintext_secret: str, *, settings: Settings | None = None) -> str:
    """Fernet-encrypt a plaintext webhook secret for storage at rest (L3)."""
    return token_cipher(settings).encrypt(plaintext_secret)


def recover_webhook_secret(ciphertext: str, *, settings: Settings | None = None) -> str:
    """Decrypt a stored webhook secret ciphertext (L3).

    Used by the delivery worker to recover the signing key. Never logged.
    """
    return token_cipher(settings).decrypt(ciphertext)


async def create_webhook_subscription(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    url: str,
    subscribed_events: list[str],
    description: str | None,
    created_by_user_id: UUID | None,
    settings: Settings | None = None,
) -> tuple[WebhookSubscription, str]:
    """Create a webhook subscription, returning (row, plaintext_secret) (L3).

    The plaintext secret is shown **once** (doc 08 §3.6); the caller is
    responsible for returning it in the response. It is not stored in the clear
    and cannot be recovered via the API. The caller commits.
    """
    plaintext_secret = generate_webhook_secret()
    encrypted_secret = store_webhook_secret(plaintext_secret, settings=settings)
    sub = WebhookSubscription(
        workspace_id=workspace_id,
        url=url,
        secret_encrypted=encrypted_secret,
        subscribed_events=list(subscribed_events),
        active=True,
        description=description,
        created_by_user_id=created_by_user_id,
    )
    session.add(sub)
    await session.flush()
    return sub, plaintext_secret


async def get_webhook_subscription(
    session: AsyncSession,
    workspace_id: UUID,
    subscription_id: UUID,
) -> WebhookSubscription | None:
    """Return a subscription by id *scoped to workspace_id*, or None (L3).

    Workspace isolation: a caller in workspace A can never resolve workspace B's
    subscription.
    """
    result = await session.execute(
        select(WebhookSubscription).where(
            WebhookSubscription.id == subscription_id,
            WebhookSubscription.workspace_id == workspace_id,
        )
    )
    return result.scalar_one_or_none()


async def list_webhook_subscriptions(
    session: AsyncSession,
    workspace_id: UUID,
) -> list[WebhookSubscription]:
    """List a workspace's webhook subscriptions, newest first (L3)."""
    result = await session.execute(
        select(WebhookSubscription)
        .where(WebhookSubscription.workspace_id == workspace_id)
        .order_by(WebhookSubscription.id.desc())
    )
    return list(result.scalars().all())


async def update_webhook_subscription(
    session: AsyncSession,
    subscription: WebhookSubscription,
    *,
    url: str | None = None,
    subscribed_events: list[str] | None = None,
    active: bool | None = None,
    description: str | None = None,
) -> WebhookSubscription:
    """Partial-update a webhook subscription in place (L3). Caller commits."""
    if url is not None:
        subscription.url = url
    if subscribed_events is not None:
        subscription.subscribed_events = list(subscribed_events)
    if active is not None:
        subscription.active = active
    if description is not None:
        subscription.description = description
    await session.flush()
    return subscription


async def delete_webhook_subscription(
    session: AsyncSession, subscription: WebhookSubscription
) -> None:
    """Hard-delete a webhook subscription (admin disconnect). Caller commits.

    Delivery rows cascade-delete with the subscription.
    """
    await session.delete(subscription)
    await session.flush()


# ---------------------------------------------------------------------------
# L3: HMAC signing (doc 08 §1.10 — Stripe webhook signature pattern)
# ---------------------------------------------------------------------------


def build_webhook_signature(
    *,
    timestamp: int,
    raw_body: bytes,
    secret: str,
) -> str:
    """Build the ``X-CivicSignals-Signature`` header value (L3).

    Format: ``t=<unix>,v1=<hmac-sha256-hex>``. The HMAC covers the string
    ``<t>.<raw_body>`` (timestamp dot body) with the plaintext webhook secret.
    This follows the Stripe webhook signature pattern (doc 08 §1.10) so
    subscribers can reuse Stripe-compatible verification libraries.
    """
    signed_payload = f"{timestamp}.".encode("ascii") + raw_body
    # Use the plaintext secret bytes (stripping the "whsec_" prefix if present)
    # as the HMAC key. The prefix is display-only metadata.
    key_part = secret.removeprefix(_WEBHOOK_SECRET_PREFIX)
    # Pad base64url if needed before decoding.
    padding_needed = (4 - len(key_part) % 4) % 4
    key_bytes = base64.urlsafe_b64decode(key_part + "=" * padding_needed)
    digest = hmac.new(key_bytes, signed_payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def verify_webhook_signature(
    *,
    header_value: str,
    raw_body: bytes,
    secret: str,
    max_age_seconds: int = 300,
) -> bool:
    """Verify the ``X-CivicSignals-Signature`` header (L3, subscriber-side helper).

    Returns True iff the HMAC is valid and the timestamp is within
    ``max_age_seconds`` of now (replay protection). This function is provided
    for the SDK + tests; the delivery worker does not verify outbound signatures
    (it produces them).
    """
    try:
        parts = dict(p.split("=", 1) for p in header_value.split(",") if "=" in p)
        t = int(parts["t"])
        v1 = parts["v1"]
    except (KeyError, ValueError):
        return False
    if abs(int(time.time()) - t) > max_age_seconds:
        return False
    expected = build_webhook_signature(timestamp=t, raw_body=raw_body, secret=secret)
    expected_v1 = expected.split("v1=", 1)[1]
    return hmac.compare_digest(expected_v1, v1)


# ---------------------------------------------------------------------------
# L3: Webhook delivery (HTTP POST + delivery log)
# ---------------------------------------------------------------------------


async def _deliver_one(
    session: AsyncSession,
    *,
    subscription: WebhookSubscription,
    delivery: WebhookDelivery,
    http: httpx.AsyncClient,
    settings: Settings | None = None,
) -> WebhookDelivery:
    """Attempt one HTTP delivery and record the outcome on the delivery row.

    Builds the HMAC signature, POSTs to the subscriber URL, and updates the
    delivery status/retry schedule. The caller commits.

    A 2xx response is success; anything else is failure. Network errors are
    recorded as failure (no response status). Secrets are never logged or stored
    in the response body.
    """
    # Recover the plaintext secret (never logged).
    secret = recover_webhook_secret(subscription.secret_encrypted, settings=settings)

    # Serialize the payload — the stored request_body is the canonical form.
    raw_body = json.dumps(delivery.request_body, separators=(",", ":")).encode("utf-8")
    ts = int(time.time())
    sig = build_webhook_signature(timestamp=ts, raw_body=raw_body, secret=secret)

    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "X-CivicSignals-Event": delivery.event_type,
        "X-CivicSignals-Delivery": str(delivery.event_id),
        "X-CivicSignals-Signature": sig,
    }

    delivery.attempt_count += 1
    delivery.attempted_at = datetime.now(UTC)
    # Store the headers we sent (signature is re-computed on retry; this is the
    # non-signature headers for the log — redact the signature itself).
    delivery.request_headers = {k: v for k, v in headers.items() if k != "X-CivicSignals-Signature"}

    try:
        resp = await http.post(subscription.url, content=raw_body, headers=headers, timeout=30.0)
        delivery.response_status = resp.status_code
        # Truncate the response body (best-effort diagnostic, not signed data).
        body_text = resp.text[:_MAX_RESPONSE_BODY_BYTES] if resp.text else None
        delivery.response_body = body_text
        success = 200 <= resp.status_code < 300
    except httpx.HTTPError:
        delivery.response_status = None
        delivery.response_body = None
        success = False

    if success:
        delivery.status = WebhookDeliveryStatus.SUCCESS
        delivery.retry_at = None
    else:
        exhausted = delivery.attempt_count >= WEBHOOK_MAX_ATTEMPTS
        if exhausted:
            delivery.status = WebhookDeliveryStatus.DEAD_LETTER
            delivery.retry_at = None
        else:
            delivery.status = WebhookDeliveryStatus.FAILED
            delay = WEBHOOK_RETRY_DELAYS[delivery.attempt_count - 1]
            delivery.retry_at = datetime.now(UTC) + timedelta(seconds=delay)

    await session.flush()
    return delivery


async def execute_webhook_delivery(
    session: AsyncSession,
    *,
    subscription: WebhookSubscription,
    delivery: WebhookDelivery,
    http_client: httpx.AsyncClient | None = None,
    settings: Settings | None = None,
) -> WebhookDelivery:
    """Execute one webhook delivery attempt and record it (L3).

    Injectable HTTP client so tests can mock the transport. The caller commits.
    """
    http = http_client or default_http_client()
    own_http = http_client is None
    try:
        return await _deliver_one(
            session,
            subscription=subscription,
            delivery=delivery,
            http=http,
            settings=settings,
        )
    finally:
        if own_http:
            await http.aclose()


async def deliver_event_to_subscribers(
    session: AsyncSession,
    *,
    event_type: str,
    payload: dict[str, object],
    workspace_id: UUID,
    http_client: httpx.AsyncClient | None = None,
    settings: Settings | None = None,
) -> list[WebhookDelivery]:
    """Fan out one event to all matching active subscriptions in a workspace (L3).

    Creates one :class:`WebhookDelivery` row per matching subscription and
    immediately attempts delivery. Used by the events-bus handler registered at
    app startup. The caller commits.

    ``event_id`` is generated once per subscription delivery (not shared across
    subscriptions) so each subscriber gets its own idempotency token.
    """
    http = http_client or default_http_client()
    own_http = http_client is None
    deliveries: list[WebhookDelivery] = []
    try:
        # Find all active subscriptions for this workspace that subscribe to this event.
        result = await session.execute(
            select(WebhookSubscription).where(
                WebhookSubscription.workspace_id == workspace_id,
                WebhookSubscription.active.is_(True),
            )
        )
        subscriptions = list(result.scalars().all())
        matching = [s for s in subscriptions if event_type in s.subscribed_events]

        for sub in matching:
            delivery = WebhookDelivery(
                workspace_id=workspace_id,
                subscription_id=sub.id,
                event_type=event_type,
                request_body=dict(payload),
                status=WebhookDeliveryStatus.PENDING,
                attempt_count=0,
            )
            session.add(delivery)
            await session.flush()
            await _deliver_one(
                session, subscription=sub, delivery=delivery, http=http, settings=settings
            )
            deliveries.append(delivery)
    finally:
        if own_http:
            await http.aclose()
    return deliveries


# ---------------------------------------------------------------------------
# L3: Retry sweeper surface (driven by the retry_failed_webhook_deliveries task)
# ---------------------------------------------------------------------------


async def due_failed_webhook_deliveries(
    session: AsyncSession, *, now: datetime | None = None, limit: int = 100
) -> list[WebhookDelivery]:
    """Return failed delivery rows whose ``retry_at`` is due (oldest first) (L3)."""
    now = now or datetime.now(UTC)
    result = await session.execute(
        select(WebhookDelivery)
        .where(
            WebhookDelivery.status == WebhookDeliveryStatus.FAILED,
            WebhookDelivery.retry_at.is_not(None),
            WebhookDelivery.retry_at <= now,
        )
        .order_by(WebhookDelivery.retry_at.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_webhook_subscription_unscoped(
    session: AsyncSession, subscription_id: UUID
) -> WebhookSubscription | None:
    """Return a subscription by id alone (for the retry task). No workspace gate (L3)."""
    result = await session.execute(
        select(WebhookSubscription).where(WebhookSubscription.id == subscription_id)
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# L3: Delivery log read surface (cursor pagination)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class WebhookDeliveryPage:
    """A cursor-paginated page of webhook delivery rows (L3)."""

    items: list[WebhookDelivery]
    next_cursor: str | None


async def list_webhook_deliveries(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    subscription_id: UUID | None = None,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> WebhookDeliveryPage:
    """Return a cursor-paginated, newest-first page of delivery rows (L3).

    Scoped to ``workspace_id`` (isolation). Optional ``subscription_id`` narrows
    to one subscription's deliveries.
    """
    limit = max(1, min(limit, MAX_LIMIT))
    stmt = (
        select(WebhookDelivery)
        .where(WebhookDelivery.workspace_id == workspace_id)
        .order_by(WebhookDelivery.id.desc())
    )
    if subscription_id is not None:
        stmt = stmt.where(WebhookDelivery.subscription_id == subscription_id)
    if cursor is not None:
        stmt = stmt.where(WebhookDelivery.id < _decode_cursor(cursor))
    stmt = stmt.limit(limit + 1)

    rows = list((await session.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = _encode_cursor(items[-1].id) if has_more and items else None
    return WebhookDeliveryPage(items=items, next_cursor=next_cursor)
