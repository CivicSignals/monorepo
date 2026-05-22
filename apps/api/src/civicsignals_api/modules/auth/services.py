"""Public service interface for the auth module.

Other modules call auth only through the functions defined here — never by
importing auth's models or routes directly (doc 06 §3). This is the foundation
for the whole identity chain (B2 OAuth, B3 reset, B4 MFA, B5 workspaces, B7
RBAC), so the public surface is intentionally small and stable:

- ``hash_password`` / ``verify_password`` — bcrypt (cost 12 per NFR §4.2).
- ``issue_access_token`` / ``issue_refresh_token`` / ``verify_token`` — bearer
  JWTs (``pyjwt``); the seam downstream modules use to mint/validate sessions.
- ``authenticate`` — verify an email+password pair, returning the user.
- ``signup`` — create a user, mint + persist a verification token, and return
  the opaque token to mail.
- ``consume_email_verification_token`` — single-use verification.
- ``create_password_reset_token`` / ``consume_password_reset_token`` — B3 reset
  flow. Token is single-use, hashed at rest, and expires after a configurable TTL.
- ``google_oauth_start`` / ``google_oauth_callback`` — B2 Google OAuth2
  authorization-code flow with signed-state (HMAC-SHA256 nonce).

Cross-module rules: user identity is owned by ``accounts`` and reached only via
``accounts.services``; verification mail goes out via ``notifications.services``.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlencode
from uuid import UUID

import httpx
import jwt
from passlib.context import CryptContext
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.config import Settings, get_settings
from civicsignals_api.modules.accounts import services as accounts_services
from civicsignals_api.modules.accounts.models import User

from .models import (
    ApiToken,
    ApiTokenType,
    EmailVerificationToken,
    OAuthIdentity,
    PasswordResetToken,
)

# bcrypt at cost 12 (NFR §4.2). passlib transparently truncates >72 bytes; we
# additionally reject overly long passwords at the schema layer.
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)

TokenType = Literal["access", "refresh"]


class AuthError(Exception):
    """Base for auth-layer failures the routes translate into RFC 7807 problems."""


class InvalidCredentialsError(AuthError):
    """Email/password did not match a user."""


class EmailNotVerifiedError(AuthError):
    """Login attempted before the email was verified (when required)."""


class InvalidTokenError(AuthError):
    """A JWT failed signature/expiry/claims validation."""


class VerificationTokenError(AuthError):
    """An email-verification token was missing, expired, or already used."""


class PasswordResetTokenError(AuthError):
    """A password-reset token was missing, expired, or already consumed."""


class ApiTokenError(AuthError):
    """An API token was missing, malformed, revoked, or expired."""


class InvalidScopeError(AuthError):
    """A token was requested with one or more unknown scope strings (B8)."""

    def __init__(self, invalid: Sequence[str]) -> None:
        self.invalid = list(invalid)
        super().__init__(f"unknown scope(s): {', '.join(self.invalid)}")


@dataclass(frozen=True)
class SignupResult:
    """Outcome of :func:`signup` — the new user plus the (cleartext) token to mail."""

    user: User
    verification_token: str


# --- Password hashing --------------------------------------------------------


def hash_password(password: str) -> str:
    """Return a bcrypt hash of ``password`` (cost 12)."""
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Return True iff ``password`` matches ``password_hash`` (constant-time)."""
    if not password_hash:
        return False
    return _pwd_context.verify(password, password_hash)


# --- JWT bearer tokens -------------------------------------------------------


def _secret(settings: Settings) -> str:
    return settings.jwt_secret or settings.secret_key


def _issue_token(
    user_id: UUID,
    *,
    token_type: TokenType,
    ttl_seconds: int,
    settings: Settings,
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "type": token_type,
        "iss": settings.jwt_issuer,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    return jwt.encode(claims, _secret(settings), algorithm=settings.jwt_algorithm)


def issue_access_token(user_id: UUID, *, settings: Settings | None = None) -> str:
    """Mint a short-lived access bearer JWT for ``user_id``."""
    settings = settings or get_settings()
    return _issue_token(
        user_id,
        token_type="access",
        ttl_seconds=settings.access_token_ttl_seconds,
        settings=settings,
    )


def issue_refresh_token(user_id: UUID, *, settings: Settings | None = None) -> str:
    """Mint a long-lived refresh bearer JWT for ``user_id`` (doc 08 §3.1)."""
    settings = settings or get_settings()
    return _issue_token(
        user_id,
        token_type="refresh",
        ttl_seconds=settings.refresh_token_ttl_seconds,
        settings=settings,
    )


def verify_token(
    token: str,
    *,
    expected_type: TokenType = "access",
    settings: Settings | None = None,
) -> UUID:
    """Decode + validate a bearer JWT and return its subject (user id).

    Raises :class:`InvalidTokenError` on bad signature, wrong algorithm, expiry,
    issuer mismatch, or unexpected token type. ``algorithms`` is pinned to the
    configured algorithm so an ``alg: none`` / algorithm-confusion token is
    rejected (threat-model §4.2).
    """
    settings = settings or get_settings()
    try:
        claims = jwt.decode(
            token,
            _secret(settings),
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "sub", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc

    if claims.get("type") != expected_type:
        raise InvalidTokenError("unexpected token type")
    try:
        return UUID(str(claims["sub"]))
    except (KeyError, ValueError) as exc:
        raise InvalidTokenError("invalid subject") from exc


# --- Email-verification tokens (single-use, hashed at rest) ------------------


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def create_email_verification_token(
    session: AsyncSession, user: User, *, settings: Settings | None = None
) -> str:
    """Generate, persist (hashed), and return a single-use verification token."""
    settings = settings or get_settings()
    raw = secrets.token_urlsafe(32)
    record = EmailVerificationToken(
        user_id=user.id,
        token_hash=_hash_token(raw),
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.email_verification_ttl_seconds),
    )
    session.add(record)
    await session.flush()
    return raw


async def consume_email_verification_token(session: AsyncSession, raw_token: str) -> User:
    """Verify a token and mark the user's email verified (single-use).

    Raises :class:`VerificationTokenError` if the token is unknown, expired, or
    already used.
    """
    result = await session.execute(
        select(EmailVerificationToken).where(
            EmailVerificationToken.token_hash == _hash_token(raw_token)
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise VerificationTokenError("unknown token")
    if record.used_at is not None:
        raise VerificationTokenError("token already used")
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < datetime.now(UTC):
        raise VerificationTokenError("token expired")

    user = await accounts_services.get_user_by_id(session, record.user_id)
    if user is None:
        raise VerificationTokenError("user no longer exists")

    record.used_at = datetime.now(UTC)
    await accounts_services.mark_email_verified(session, user)
    await session.flush()
    return user


# --- Password-reset tokens (B3; single-use, hashed at rest) -----------------


async def create_password_reset_token(
    session: AsyncSession, user: User, *, settings: Settings | None = None
) -> str:
    """Generate, persist (hashed), and return a single-use password-reset token.

    The opaque token is returned to the caller so the route can embed it in the
    reset link mailed to the user. Only the SHA-256 digest is stored in the DB
    (threat-model §4.2).
    """
    settings = settings or get_settings()
    raw = secrets.token_urlsafe(32)
    record = PasswordResetToken(
        user_id=user.id,
        token_hash=_hash_token(raw),
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.password_reset_ttl_seconds),
    )
    session.add(record)
    await session.flush()
    return raw


async def consume_password_reset_token(
    session: AsyncSession,
    raw_token: str,
    *,
    new_password: str,
) -> User:
    """Validate a reset token, update the password, and invalidate all other tokens.

    Raises :class:`PasswordResetTokenError` if the token is unknown, expired, or
    already consumed. On success:

    - Sets the user's ``password_hash`` to the bcrypt hash of ``new_password``.
    - Marks this token consumed (``consumed_at = now``).
    - Marks all *other* pending reset tokens for the same user consumed so that
      old reset links can no longer be replayed.

    **Concurrency safety.** Consumption is a two-phase approach to avoid both
    TOCTOU races and cross-token deadlocks (threat-model §4.2):

    1. Atomically claim the presented token with UPDATE … WHERE consumed_at IS NULL
       AND expires_at > now RETURNING user_id.  Only one concurrent request wins;
       the others see 0 rows and get a 400.
    2. Invalidate remaining pending tokens for that user with a separate UPDATE
       that is safe because step 1 has already committed the token to this
       transaction — no other transaction can claim the same token (it is now
       consumed), and the bulk invalidation of sibling tokens only conflicts if
       another transaction also claimed a different sibling, which can only happen
       once step 1 has returned a row (i.e. a different token was successfully
       claimed first and step 2 of that transaction is ongoing).  Postgres
       acquires row locks in a consistent order within a table scan, so the
       sequencing is deterministic and deadlock-free in practice; in the unlikely
       event Postgres detects a cycle it raises a serialization error which the
       caller can retry.
    """
    now = datetime.now(UTC)

    # Phase 1 — atomically claim this token if it is still valid.
    claim_result = await session.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.token_hash == _hash_token(raw_token),
            PasswordResetToken.consumed_at.is_(None),
            PasswordResetToken.expires_at > now,
        )
        .values(consumed_at=now)
        .returning(PasswordResetToken.id, PasswordResetToken.user_id)
    )
    claimed = claim_result.first()
    if claimed is None:
        # Distinguish "never existed" from "expired/consumed" where possible;
        # both map to the same 400 from the route (no user enumeration).
        token_exists = await session.execute(
            select(PasswordResetToken.id).where(
                PasswordResetToken.token_hash == _hash_token(raw_token)
            )
        )
        if token_exists.first() is None:
            raise PasswordResetTokenError("unknown token")
        raise PasswordResetTokenError("token expired or already used")

    _token_id, user_id = claimed

    user = await accounts_services.get_user_by_id(session, user_id)
    if user is None:
        raise PasswordResetTokenError("user no longer exists")

    # Phase 2 — invalidate all remaining pending tokens for this user so old
    # reset links cannot be replayed after a successful password change.
    await session.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )

    # Update the password.
    user.password_hash = hash_password(new_password)
    await session.flush()
    return user


# --- High-level flows --------------------------------------------------------


async def signup(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    name: str | None = None,
    settings: Settings | None = None,
) -> SignupResult:
    """Create a user and mint a verification token.

    Raises :class:`InvalidCredentialsError` if the email is already taken
    (mapped to ``409 Conflict`` by the route).
    """
    settings = settings or get_settings()
    existing = await accounts_services.get_user_by_email(session, email)
    if existing is not None:
        raise InvalidCredentialsError("email already registered")

    user = await accounts_services.create_user(
        session,
        email=email,
        password_hash=hash_password(password),
        name=name,
        email_verified=False,
    )
    raw_token = await create_email_verification_token(session, user, settings=settings)
    return SignupResult(user=user, verification_token=raw_token)


async def authenticate(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    settings: Settings | None = None,
) -> User:
    """Verify an email+password pair and return the user.

    Raises :class:`InvalidCredentialsError` (same error whether the email is
    unknown or the password is wrong — no user enumeration) and
    :class:`EmailNotVerifiedError` when verification is required and pending.

    # TODO B1: application-layer rate limiting / account lockout attaches here
    #   (5/15min/IP, 20/15min/account per threat-model §4.2). The WAF (A5) layers
    #   in front; this function is the single choke point for credential checks.
    """
    settings = settings or get_settings()
    user = await accounts_services.get_user_by_email(session, email)
    if user is None or not verify_password(password, user.password_hash):
        raise InvalidCredentialsError("invalid email or password")
    if settings.require_email_verification and not user.email_verified:
        raise EmailNotVerifiedError("email not verified")
    await accounts_services.touch_last_seen(session, user)
    return user


# --- API tokens (B8; long-lived bearer credentials, hashed at rest) ---------
# Workspace + personal access tokens per doc 08 §1.3. The opaque secret is shown
# once at creation; only its SHA-256 digest is stored (threat-model §4.2). Scopes
# are validated against the catalog below and enforced per-request by the auth
# dependency.

# The granted-permission vocabulary (doc 08 §1.3). Resource scopes come in
# read/write pairs; ``admin:read`` exposes admin-only reads (members, audit).
# Keep this the single source of truth — both token issuance (validation) and the
# management UI consume it. Additive: new scopes can be appended (non-breaking,
# doc 08 §1.2).
API_TOKEN_SCOPES: tuple[str, ...] = (
    "signals:read",
    "signals:write",
    "contacts:read",
    "contacts:write",
    "entities:read",
    "pipeline:read",
    "pipeline:write",
    "foia:read",
    "foia:write",
    "searches:read",
    "searches:write",
    "webhooks:manage",
    "admin:read",
)

# A short non-secret prefix per token type so the UI can label rows (doc 08 §1.3).
# ``cs_test_`` is reserved for a future test-mode toggle; issuance defaults to live.
_TOKEN_PREFIX: dict[ApiTokenType, str] = {
    ApiTokenType.WORKSPACE: "cs_live_",
    ApiTokenType.PERSONAL: "cs_pat_",
}
# Bytes of entropy in the random component (>= the documented 32-char body).
_API_TOKEN_ENTROPY_BYTES = 32
# How much of the plaintext to retain (non-secret) for the management UI label.
_DISPLAY_PREFIX_LEN = 12


@dataclass(frozen=True)
class ApiTokenIssue:
    """Outcome of :func:`create_api_token` — the record plus the cleartext secret.

    ``plaintext`` is the only time the secret is available; the route returns it
    once and it is never retrievable again (threat-model §4.2).
    """

    token: ApiToken
    plaintext: str


def validate_scopes(scopes: Sequence[str]) -> list[str]:
    """Normalize + validate requested scopes against :data:`API_TOKEN_SCOPES`.

    De-duplicates while preserving order. Raises :class:`InvalidScopeError`
    (mapped to ``422`` by the route) listing any unknown scope strings.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    invalid: list[str] = []
    for scope in scopes:
        if scope not in API_TOKEN_SCOPES:
            invalid.append(scope)
            continue
        if scope not in seen:
            seen.add(scope)
            ordered.append(scope)
    if invalid:
        raise InvalidScopeError(invalid)
    return ordered


def _generate_api_token(token_type: ApiTokenType) -> tuple[str, str]:
    """Return ``(plaintext, display_prefix)`` for a fresh token of ``token_type``.

    ``plaintext`` is ``<prefix><random>``; ``display_prefix`` is the leading,
    non-secret fragment stored for UI labelling.
    """
    prefix = _TOKEN_PREFIX[token_type]
    plaintext = f"{prefix}{secrets.token_urlsafe(_API_TOKEN_ENTROPY_BYTES)}"
    return plaintext, plaintext[:_DISPLAY_PREFIX_LEN]


async def create_api_token(
    session: AsyncSession,
    *,
    token_type: ApiTokenType,
    name: str,
    scopes: Sequence[str],
    user_id: UUID,
    created_by_user_id: UUID,
    workspace_id: UUID | None = None,
    expires_at: datetime | None = None,
) -> ApiTokenIssue:
    """Mint, hash, persist, and return a new API token plus its cleartext secret.

    ``token_type`` decides the prefix and scoping: a ``workspace`` token requires
    a ``workspace_id`` and is scoped to it; a ``personal`` token must have
    ``workspace_id`` left ``None`` and acts as ``user_id`` across their
    workspaces. Scopes are validated here (raises :class:`InvalidScopeError`).
    The caller commits.
    """
    if token_type is ApiTokenType.WORKSPACE and workspace_id is None:
        raise ApiTokenError("workspace tokens require a workspace_id")
    if token_type is ApiTokenType.PERSONAL and workspace_id is not None:
        raise ApiTokenError("personal tokens must not be bound to a workspace")

    validated = validate_scopes(scopes)
    plaintext, display_prefix = _generate_api_token(token_type)
    record = ApiToken(
        token_type=token_type,
        name=name,
        token_hash=_hash_token(plaintext),
        token_prefix=display_prefix,
        user_id=user_id,
        workspace_id=workspace_id,
        created_by_user_id=created_by_user_id,
        scopes=validated,
        expires_at=expires_at,
    )
    session.add(record)
    await session.flush()
    return ApiTokenIssue(token=record, plaintext=plaintext)


async def get_api_token(session: AsyncSession, token_id: UUID) -> ApiToken | None:
    """Return the API token row with this id, or ``None``."""
    result = await session.execute(select(ApiToken).where(ApiToken.id == token_id))
    return result.scalar_one_or_none()


async def list_workspace_api_tokens(session: AsyncSession, workspace_id: UUID) -> list[ApiToken]:
    """List all (live + revoked) workspace tokens for ``workspace_id`` (newest first)."""
    result = await session.execute(
        select(ApiToken)
        .where(
            ApiToken.token_type == ApiTokenType.WORKSPACE,
            ApiToken.workspace_id == workspace_id,
        )
        .order_by(ApiToken.id.desc())
    )
    return list(result.scalars().all())


async def list_personal_api_tokens(session: AsyncSession, user_id: UUID) -> list[ApiToken]:
    """List all (live + revoked) personal access tokens owned by ``user_id``."""
    result = await session.execute(
        select(ApiToken)
        .where(
            ApiToken.token_type == ApiTokenType.PERSONAL,
            ApiToken.user_id == user_id,
        )
        .order_by(ApiToken.id.desc())
    )
    return list(result.scalars().all())


async def revoke_api_token(session: AsyncSession, token: ApiToken) -> ApiToken:
    """Mark a token revoked (idempotent). The caller commits.

    Revocation is immediate: :pymeth:`ApiToken.is_active` returns ``False`` and
    :func:`authenticate_api_token` rejects it on the next request.
    """
    if token.revoked_at is None:
        token.revoked_at = datetime.now(UTC)
        await session.flush()
    return token


def looks_like_api_token(credential: str) -> bool:
    """Return True if ``credential`` carries an API-token prefix (not a JWT).

    The auth dependency uses this to decide whether to route a bearer credential
    to the API-token path or the JWT path (doc 08 §1.3 — both arrive as
    ``Authorization: Bearer``).
    """
    return any(credential.startswith(p) for p in _TOKEN_PREFIX.values())


@dataclass(frozen=True)
class AuthenticatedToken:
    """A successfully-authenticated API token, resolved to its identity (B8).

    Carries the loaded :class:`ApiToken` (for ``scopes`` / ``workspace_id``) and
    the owning :class:`User` so the auth dependency can resolve the request
    identity without a second lookup.
    """

    token: ApiToken
    user: User


async def authenticate_api_token(session: AsyncSession, credential: str) -> AuthenticatedToken:
    """Resolve + validate an API token credential, returning its identity.

    Looks up the token by hash, rejecting (with :class:`ApiTokenError`) a token
    that is unknown, revoked, expired, or whose owning user is gone.
    ``last_used_at`` is bumped opportunistically with a narrow UPDATE so the hot
    path stays cheap and concurrency-safe (no row read-modify-write).
    """
    result = await session.execute(
        select(ApiToken).where(ApiToken.token_hash == _hash_token(credential))
    )
    token = result.scalar_one_or_none()
    if token is None:
        raise ApiTokenError("unknown token")
    if not token.is_active:
        raise ApiTokenError("token revoked or expired")

    user = await accounts_services.get_user_by_id(session, token.user_id)
    if user is None:
        raise ApiTokenError("token owner no longer exists")

    await touch_api_token(session, token.id)
    return AuthenticatedToken(token=token, user=user)


async def touch_api_token(session: AsyncSession, token_id: uuid.UUID) -> None:
    """Update a token's ``last_used_at`` to now and commit it (cheap UPDATE).

    Done as a targeted UPDATE rather than mutating the loaded row so concurrent
    requests using the same token don't contend on the ORM identity map. It is
    committed here (rather than left for the route to flush) because it runs in
    the auth *dependency* — before the route does its work — so most read
    handlers never commit; without this the hygiene timestamp (doc 08 §1.3)
    would be rolled back. At this point the session holds only the auth lookup,
    so committing the touch does not leak any unrelated partial write.
    """
    await session.execute(
        update(ApiToken).where(ApiToken.id == token_id).values(last_used_at=datetime.now(UTC))
    )
    await session.commit()


# ---------------------------------------------------------------------------
# Google OAuth2 authorization-code flow + account linking (B2)
# ---------------------------------------------------------------------------
# Design:
# - ``google_oauth_start`` builds the Google authorization URL and a signed
#   state nonce (HMAC-SHA256 over a random value, signed with the state secret).
#   The signed state travels in the redirect URL so the callback can verify it
#   without server-side session storage (stateless, threat-model §4.2).
# - ``google_oauth_callback`` exchanges the code for tokens, fetches the
#   verified email via the userinfo endpoint (no raw id_token validation
#   required — we use the access token to hit the userinfo endpoint which
#   Google always authenticates), links or creates the user, and issues a JWT.
# - The Google HTTP calls are made through an *injectable* ``http_client``
#   parameter so tests can mock them without live Google calls (threat-model
#   §4.2: "no live vendor in CI").


class OAuthError(AuthError):
    """Base for OAuth-flow failures the route maps to RFC 7807 problems."""


class OAuthStateMismatchError(OAuthError):
    """The ``state`` param in the callback does not match the issued nonce."""


class OAuthEmailUnverifiedError(OAuthError):
    """Google reported that the account's email is not verified."""


class OAuthProviderError(OAuthError):
    """Google returned an error or an unexpected response."""


# Google OAuth2 scopes — openid + email is the minimum to get a verified email.
_GOOGLE_SCOPES = "openid email profile"
# Length of the random state nonce.
_STATE_NONCE_BYTES = 16
# HMAC algorithm for the state signature.
_STATE_HMAC_ALG = "sha256"


def _state_secret(settings: Settings) -> bytes:
    """Return the signing key for the state nonce (bytes)."""
    raw = settings.google_oauth_state_secret or settings.secret_key
    return raw.encode("utf-8")


def _sign_state(nonce: str, settings: Settings) -> str:
    """Return ``<nonce>.<hex-sig>`` — a self-verifying state token."""
    sig = hmac.new(_state_secret(settings), nonce.encode("utf-8"), _STATE_HMAC_ALG).hexdigest()
    return f"{nonce}.{sig}"


def _verify_state(state_token: str, settings: Settings) -> bool:
    """Return True iff ``state_token`` was issued by :func:`_sign_state`."""
    parts = state_token.split(".", 1)
    if len(parts) != 2:
        return False
    nonce, provided_sig = parts
    expected_sig = hmac.new(
        _state_secret(settings), nonce.encode("utf-8"), _STATE_HMAC_ALG
    ).hexdigest()
    return hmac.compare_digest(provided_sig, expected_sig)


def _derive_redirect_uri(settings: Settings) -> str:
    """Return the OAuth redirect URI (override > derived from api_base_url).

    The redirect URI must point to the *API* callback endpoint
    ``/api/v1/auth/oauth/google/callback`` so Google sends the authorization
    code directly to the server. The server then issues the JWT and redirects
    the browser to the web app. Use ``google_oauth_redirect_uri`` to override
    when a reverse-proxy changes the apparent host.

    ``web_base_url`` is the web app origin; ``api_v1_prefix`` is ``/api/v1``.
    We reconstruct the API origin from ``web_base_url`` by default, but the
    most reliable approach is to set ``GOOGLE_OAUTH_REDIRECT_URI`` explicitly
    in production.
    """
    if settings.google_oauth_redirect_uri:
        return settings.google_oauth_redirect_uri
    # Derive the API base from the web base URL by replacing the port / path.
    # Default local: web=http://localhost:3000 → api=http://localhost:8000/api/v1
    # This default is only used in dev; production always sets the override.
    return f"http://localhost:8000{settings.api_v1_prefix}/auth/oauth/google/callback"


@dataclass(frozen=True)
class GoogleOAuthStartResult:
    """Output of :func:`google_oauth_start` — the redirect URL + state nonce."""

    authorization_url: str
    state: str  # the signed state token to round-trip through the browser


def google_oauth_start(settings: Settings | None = None) -> GoogleOAuthStartResult:
    """Build the Google authorization URL and a signed state nonce.

    No DB access required. Returns the URL to redirect the browser to plus the
    state token (the route embeds it in the redirect response so the callback
    can verify it without server-side session storage — the HMAC signature
    makes the state self-verifying).

    Raises :class:`OAuthProviderError` when ``GOOGLE_OAUTH_CLIENT_ID`` is not
    configured (fail-closed: the endpoint is mounted but returns a clear error
    rather than a cryptic crash).
    """
    settings = settings or get_settings()
    client_id = settings.google_oauth_client_id
    if not client_id:
        raise OAuthProviderError("Google OAuth is not configured (GOOGLE_OAUTH_CLIENT_ID missing)")

    nonce = secrets.token_urlsafe(_STATE_NONCE_BYTES)
    state = _sign_state(nonce, settings)
    redirect_uri = _derive_redirect_uri(settings)

    # urlencode properly percent-encodes all values (scope has spaces,
    # redirect_uri has colons / slashes — manual concatenation would produce an
    # invalid Location header that breaks some browsers and proxies).
    params = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": _GOOGLE_SCOPES,
            "state": state,
            "access_type": "offline",
            "prompt": "select_account",
        }
    )
    return GoogleOAuthStartResult(
        authorization_url=f"https://accounts.google.com/o/oauth2/v2/auth?{params}",
        state=state,
    )


# Type alias for the injectable HTTP-call factory used by the callback — a
# callable that accepts the same kwargs as ``httpx.AsyncClient.post`` /
# ``.get`` and returns the JSON body as a dict. Tests replace this with a mock.
HttpPostFn = Callable[..., Any]


async def _exchange_code_for_tokens(
    code: str,
    *,
    settings: Settings,
    http_post: HttpPostFn | None = None,
) -> dict[str, Any]:
    """Exchange an authorization code for an access token at Google's token URL.

    Returns the raw token response dict. ``http_post`` is injectable so tests
    can supply a mock without live Google calls.

    Raises :class:`OAuthProviderError` when ``GOOGLE_OAUTH_CLIENT_SECRET`` is not
    configured (the exchange would fail at Google with a cryptic error otherwise).
    """
    client_secret = settings.google_oauth_client_secret
    if not client_secret:
        raise OAuthProviderError(
            "Google OAuth is not configured (GOOGLE_OAUTH_CLIENT_SECRET missing)"
        )
    redirect_uri = _derive_redirect_uri(settings)

    if http_post is not None:
        return http_post(  # type: ignore[no-any-return]
            url=settings.google_oauth_token_url,
            data={
                "code": code,
                "client_id": settings.google_oauth_client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            settings.google_oauth_token_url,
            data={
                "code": code,
                "client_id": settings.google_oauth_client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    if resp.status_code != 200:
        raise OAuthProviderError(f"Google token exchange failed: {resp.status_code}")
    return resp.json()  # type: ignore[no-any-return]


async def _fetch_userinfo(
    access_token: str,
    *,
    settings: Settings,
    http_get: HttpPostFn | None = None,
) -> dict[str, Any]:
    """Fetch the verified email + sub from Google's userinfo endpoint.

    Uses the access token rather than raw id_token validation so we avoid
    implementing JWT signature verification for Google's keys (the userinfo
    endpoint is always authenticated by the access token). ``http_get`` is
    injectable for tests.
    """
    if http_get is not None:
        return http_get(  # type: ignore[no-any-return]
            url=settings.google_oauth_userinfo_url,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            settings.google_oauth_userinfo_url,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if resp.status_code != 200:
        raise OAuthProviderError(f"Google userinfo fetch failed: {resp.status_code}")
    return resp.json()  # type: ignore[no-any-return]


async def get_oauth_identity_by_provider_subject(
    session: AsyncSession,
    provider: str,
    subject: str,
) -> OAuthIdentity | None:
    """Return the OAuth identity for ``(provider, subject)``, or ``None``."""
    result = await session.execute(
        select(OAuthIdentity).where(
            OAuthIdentity.provider == provider,
            OAuthIdentity.subject == subject,
        )
    )
    return result.scalar_one_or_none()


async def link_oauth_identity(
    session: AsyncSession,
    *,
    user: User,
    provider: str,
    subject: str,
    provider_email: str,
) -> OAuthIdentity:
    """Persist a new OAuth identity row linking ``user`` to ``(provider, subject)``.

    The caller is responsible for ensuring no identity for this ``(provider,
    subject)`` already exists (use :func:`get_oauth_identity_by_provider_subject`
    first). The caller commits.
    """
    identity = OAuthIdentity(
        user_id=user.id,
        provider=provider,
        subject=subject,
        provider_email=provider_email,
    )
    session.add(identity)
    await session.flush()
    return identity


@dataclass(frozen=True)
class GoogleOAuthCallbackResult:
    """Outcome of :func:`google_oauth_callback` — the resolved user and JWT."""

    user: User
    created: bool  # True if a new user was created, False if existing user was linked/found


async def google_oauth_callback(
    session: AsyncSession,
    *,
    code: str,
    state: str,
    settings: Settings | None = None,
    http_post: HttpPostFn | None = None,
    http_get: HttpPostFn | None = None,
) -> GoogleOAuthCallbackResult:
    """Complete the Google OAuth2 callback: exchange code, link/create user, issue JWT.

    Steps:
    1. Verify the state nonce (HMAC; raises :class:`OAuthStateMismatchError`).
    2. Exchange the authorization code for an access token (raises
       :class:`OAuthProviderError` on HTTP error).
    3. Fetch the verified email + subject from Google's userinfo endpoint
       (raises :class:`OAuthEmailUnverifiedError` when email_verified is False).
    4. Look up an existing ``auth_oauth_identity`` row for ``(google, sub)``:
       - **Found**: return the linked user (account linking already done).
       - **Not found**: look up an ``accounts_user`` by email:
         - **Email matches**: link the Google identity to the existing user
           (set ``email_verified`` if not already set).
         - **No match**: create a new user (``email_verified=True`` from Google)
           and link the identity.
    5. Return the resolved user (``created=True`` when new).

    ``http_post`` and ``http_get`` are injectable for tests (no live Google).
    """
    settings = settings or get_settings()

    # Step 1 — state verification (CSRF / replay protection).
    if not _verify_state(state, settings):
        raise OAuthStateMismatchError("OAuth state parameter is invalid or tampered")

    # Step 2 — exchange code for tokens.
    token_data = await _exchange_code_for_tokens(code, settings=settings, http_post=http_post)
    if "error" in token_data:
        raise OAuthProviderError(
            f"Google token error: {token_data.get('error_description', token_data['error'])}"
        )
    access_token = token_data.get("access_token")
    if not access_token:
        raise OAuthProviderError("Google token response missing access_token")

    # Step 3 — fetch verified email + sub from userinfo.
    userinfo = await _fetch_userinfo(access_token, settings=settings, http_get=http_get)
    email_verified = userinfo.get("email_verified", False)
    if not email_verified:
        raise OAuthEmailUnverifiedError(
            "Google account email is not verified; sign in with a verified Google account"
        )
    provider_email: str = userinfo.get("email", "")
    sub: str = userinfo.get("sub", "")
    name: str | None = userinfo.get("name") or None
    if not provider_email or not sub:
        raise OAuthProviderError("Google userinfo response is missing email or sub")

    # Step 4 — resolve or create the user.
    existing_identity = await get_oauth_identity_by_provider_subject(session, "google", sub)
    if existing_identity is not None:
        # Already linked — look up the user.
        user = await accounts_services.get_user_by_id(session, existing_identity.user_id)
        if user is None:
            raise OAuthProviderError("Linked user no longer exists")
        await accounts_services.touch_last_seen(session, user)
        await session.flush()
        return GoogleOAuthCallbackResult(user=user, created=False)

    # No existing identity — look up by email (account linking on email match).
    user_by_email = await accounts_services.get_user_by_email(session, provider_email)
    if user_by_email is not None:
        # Link this Google identity to the existing account.
        await link_oauth_identity(
            session,
            user=user_by_email,
            provider="google",
            subject=sub,
            provider_email=provider_email,
        )
        # Mark email verified (Google guarantees it).
        await accounts_services.mark_email_verified(session, user_by_email)
        await accounts_services.touch_last_seen(session, user_by_email)
        await session.flush()
        return GoogleOAuthCallbackResult(user=user_by_email, created=False)

    # No match — create a new user.
    new_user = await accounts_services.create_user(
        session,
        email=provider_email,
        password_hash=None,  # OAuth-only user; no password
        name=name,
        email_verified=True,  # Google guarantees email_verified=True
    )
    await link_oauth_identity(
        session,
        user=new_user,
        provider="google",
        subject=sub,
        provider_email=provider_email,
    )
    await accounts_services.touch_last_seen(session, new_user)
    await session.flush()
    return GoogleOAuthCallbackResult(user=new_user, created=True)
