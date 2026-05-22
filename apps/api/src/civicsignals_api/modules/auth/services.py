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
- ``begin_mfa_enrollment`` / ``activate_mfa`` / ``verify_mfa_code`` — B4 TOTP
  MFA enrollment, activation (with backup-code generation), and login enforcement.
- ``issue_mfa_challenge_token`` / ``verify_mfa_challenge_token`` — B4 short-lived
  MFA-challenge JWT that bridges the first factor to the second-factor verify step.
- ``disable_mfa`` — B4 clears the TOTP secret + backup codes after re-auth.
- ``regenerate_backup_codes`` — B4 replaces the backup-code set; returns the new
  plaintext codes once (hashes only stored).

Cross-module rules: user identity is owned by ``accounts`` and reached only via
``accounts.services``; verification mail goes out via ``notifications.services``.
"""

from __future__ import annotations

import base64
import binascii
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
import pyotp
from cryptography.fernet import Fernet
from cryptography.fernet import InvalidToken as FernetInvalidToken
from passlib.context import CryptContext
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.config import Settings, get_settings
from civicsignals_api.modules.accounts import services as accounts_services
from civicsignals_api.modules.accounts.models import User

from .models import (
    ApiToken,
    ApiTokenType,
    EmailVerificationToken,
    MfaBackupCode,
    MfaCredential,
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


class MfaError(AuthError):
    """Base for MFA-layer failures the routes translate into RFC 7807 problems (B4)."""


class MfaRequiredError(MfaError):
    """Login succeeded but MFA is enabled; a second-factor challenge is required (B4)."""

    def __init__(self, challenge_token: str) -> None:
        self.challenge_token = challenge_token
        super().__init__("MFA verification required")


class MfaInvalidCodeError(MfaError):
    """The TOTP code or backup code submitted was wrong or already used (B4)."""


class MfaAlreadyActiveError(MfaError):
    """Enrollment attempted when MFA is already activated (B4)."""


class MfaNotActiveError(MfaError):
    """An MFA operation was requested but MFA is not active for this user (B4)."""


class MfaChallengeTokenError(MfaError):
    """The MFA-challenge JWT is missing, expired, or invalid (B4)."""


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
    """Return the OAuth redirect URI.

    The redirect URI must point to the *API* callback endpoint
    ``/api/v1/auth/oauth/google/callback`` so Google sends the authorization
    code directly to the server (not the web app). The server exchanges the
    code, issues a JWT, and then redirects the browser to the web callback page.

    **In all non-default deployments** (staging, production, custom dev ports,
    or behind a reverse-proxy) you **must** set ``GOOGLE_OAUTH_REDIRECT_URI``
    explicitly. The fallback is a convenience default for the standard local
    dev setup only (API on :8000, no override configured).
    """
    if settings.google_oauth_redirect_uri:
        return settings.google_oauth_redirect_uri
    # Fallback: standard local dev layout — api on port 8000.
    # This will NOT work in any other environment; set GOOGLE_OAUTH_REDIRECT_URI.
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
# **sync** callable that accepts keyword arguments and returns the JSON body as
# a dict. The injectable path is called without ``await``; only the real httpx
# branch is async. Tests supply a plain ``def`` that returns a canned dict.
HttpPostFn = Callable[..., Any]


async def _exchange_code_for_tokens(
    code: str,
    *,
    settings: Settings,
    http_post: HttpPostFn | None = None,
) -> dict[str, Any]:
    """Exchange an authorization code for an access token at Google's token URL.

    Returns the raw token response dict. ``http_post`` is injectable so tests
    can supply a **sync** mock without live Google calls. The callable is invoked
    without ``await`` — it must be a plain synchronous function (not a coroutine).
    When ``http_post`` is ``None`` (production), the async httpx branch is used.

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
    injectable for tests as a **sync** callable (called without ``await``);
    when ``None`` (production) the async httpx branch is used.
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


# ---------------------------------------------------------------------------
# B4: MFA / TOTP — enrollment, activation, login enforcement, disable
# ---------------------------------------------------------------------------
# Design:
# - The TOTP secret is Fernet-encrypted at rest. The same key-derivation
#   approach as integrations (K1) is used (raw 32-byte Fernet key verbatim;
#   otherwise SHA-256-derive from the passphrase). Falls back to ``secret_key``
#   in dev. Production MUST set MFA_TOTP_ENCRYPTION_KEY separately.
# - Backup codes are SHA-256 hashed (same as email-verification tokens) — they
#   are one-way; the plaintext is returned once at activation.
# - The MFA-challenge JWT is a short-lived "mfa_challenge" typed token issued
#   when login succeeds but MFA is enabled. The client presents it at
#   ``/auth/mfa/verify`` with the TOTP/backup code to complete auth.
# - TODO B2/OAuth: Google OAuth logins bypass MFA for now. Enforcing MFA on
#   OAuth logins requires a different UX (the callback is a redirect, not a
#   JSON exchange). A full solution is a follow-up; leave the comment at the
#   OAuth callback route.


def _mfa_cipher(settings: Settings) -> Fernet:
    """Return a Fernet instance keyed from MFA_TOTP_ENCRYPTION_KEY (or secret_key)."""
    key_material = settings.mfa_totp_encryption_key or settings.secret_key
    raw = key_material.encode("utf-8")
    try:
        decoded = base64.urlsafe_b64decode(raw)
        if len(decoded) == 32:
            fernet_key = raw
        else:
            import hashlib as _hashlib

            fernet_key = base64.urlsafe_b64encode(_hashlib.sha256(raw).digest())
    except (binascii.Error, ValueError):
        import hashlib as _hashlib

        fernet_key = base64.urlsafe_b64encode(_hashlib.sha256(raw).digest())
    return Fernet(fernet_key)


def _encrypt_totp_secret(secret: str, settings: Settings) -> str:
    """Return the Fernet-encrypted TOTP secret (ASCII ciphertext)."""
    return _mfa_cipher(settings).encrypt(secret.encode("utf-8")).decode("ascii")


def _decrypt_totp_secret(ciphertext: str, settings: Settings) -> str:
    """Decrypt a stored TOTP secret ciphertext; raises :class:`MfaError` on key mismatch."""
    try:
        return _mfa_cipher(settings).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except FernetInvalidToken as exc:
        raise MfaError("TOTP secret decryption failed (wrong key?)") from exc


def _hash_backup_code(raw: str) -> str:
    """SHA-256 hex digest of a plaintext backup code."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _generate_backup_codes(count: int) -> list[str]:
    """Generate ``count`` random backup codes (8 uppercase hex characters each)."""
    return [secrets.token_hex(4).upper() for _ in range(count)]


# --- MFA-challenge JWT --------------------------------------------------------
# A short-lived typed JWT (type="mfa_challenge") issued when email+password
# succeeds and MFA is active. It carries the user_id (sub) and is verified at
# /auth/mfa/verify before the full access+refresh pair is issued.


def issue_mfa_challenge_token(user_id: UUID, *, settings: Settings | None = None) -> str:
    """Mint a short-lived MFA-challenge JWT for ``user_id``."""
    settings = settings or get_settings()
    return _issue_token(
        user_id,
        token_type="mfa_challenge",  # type: ignore[arg-type]
        ttl_seconds=settings.mfa_challenge_ttl_seconds,
        settings=settings,
    )


def verify_mfa_challenge_token(token: str, *, settings: Settings | None = None) -> UUID:
    """Decode an MFA-challenge JWT and return the user_id.

    Raises :class:`MfaChallengeTokenError` on bad signature, expiry, or wrong type.
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
        raise MfaChallengeTokenError(str(exc)) from exc
    if claims.get("type") != "mfa_challenge":
        raise MfaChallengeTokenError("unexpected token type")
    try:
        return UUID(str(claims["sub"]))
    except (KeyError, ValueError) as exc:
        raise MfaChallengeTokenError("invalid subject") from exc


# --- MFA queries -------------------------------------------------------------


async def get_mfa_credential(session: AsyncSession, user_id: UUID) -> MfaCredential | None:
    """Return the MFA credential row for ``user_id``, or ``None``."""
    result = await session.execute(select(MfaCredential).where(MfaCredential.user_id == user_id))
    return result.scalar_one_or_none()


async def get_active_mfa_credential(session: AsyncSession, user_id: UUID) -> MfaCredential | None:
    """Return the **activated** MFA credential for ``user_id``, or ``None``."""
    result = await session.execute(
        select(MfaCredential).where(
            MfaCredential.user_id == user_id,
            MfaCredential.activated.is_(True),
        )
    )
    return result.scalar_one_or_none()


# --- Enrollment --------------------------------------------------------------


@dataclass(frozen=True)
class MfaEnrollResult:
    """Outcome of :func:`begin_mfa_enrollment`.

    ``totp_uri`` is the ``otpauth://`` provisioning URI (for QR rendering).
    ``secret`` is the raw base32 secret shown once to allow manual entry.
    Neither is ever stored in plaintext; the caller must not log them.
    """

    totp_uri: str
    secret: str  # base32; shown once, never stored


async def begin_mfa_enrollment(
    session: AsyncSession,
    user: User,
    *,
    settings: Settings | None = None,
) -> MfaEnrollResult:
    """Begin TOTP enrollment: generate a secret, persist it encrypted, and return the URI.

    If a **non-activated** credential already exists (e.g. the user started
    enrollment twice), it is replaced. Raises :class:`MfaAlreadyActiveError` if
    MFA is already activated.

    The secret is never returned after this call completes; the client must
    immediately display the QR / URI so the user can scan it.
    """
    settings = settings or get_settings()

    existing = await get_mfa_credential(session, user.id)
    if existing is not None and existing.activated:
        raise MfaAlreadyActiveError("MFA is already activated for this account")

    if existing is not None:
        # Replace the pending (non-activated) credential.
        await session.execute(delete(MfaCredential).where(MfaCredential.id == existing.id))
        await session.flush()

    secret = pyotp.random_base32()
    encrypted = _encrypt_totp_secret(secret, settings)
    credential = MfaCredential(
        user_id=user.id,
        totp_secret_encrypted=encrypted,
        activated=False,
    )
    session.add(credential)
    await session.flush()

    totp = pyotp.TOTP(secret)
    totp_uri = totp.provisioning_uri(name=user.email, issuer_name=settings.mfa_totp_issuer)
    # The secret is returned here ONLY so the client can render the QR / allow
    # manual entry. It is never logged and never stored in plaintext.
    return MfaEnrollResult(totp_uri=totp_uri, secret=secret)


# --- Activation (verify + generate backup codes) ----------------------------


@dataclass(frozen=True)
class MfaActivateResult:
    """Outcome of :func:`activate_mfa` — the plaintext backup codes (shown once)."""

    backup_codes: list[str]  # plaintext; return to client ONCE, then hashes only


async def activate_mfa(
    session: AsyncSession,
    user: User,
    *,
    totp_code: str,
    settings: Settings | None = None,
) -> MfaActivateResult:
    """Verify ``totp_code`` against the pending secret and activate MFA.

    On success:
    1. Marks the credential ``activated = True`` (with ``activated_at``).
    2. Generates ``mfa_backup_code_count`` backup codes and stores only their
       SHA-256 hashes; returns the plaintext codes once.

    Raises :class:`MfaNotActiveError` if no enrollment is pending.
    Raises :class:`MfaAlreadyActiveError` if already activated.
    Raises :class:`MfaInvalidCodeError` if the code is wrong.
    """
    settings = settings or get_settings()

    credential = await get_mfa_credential(session, user.id)
    if credential is None:
        raise MfaNotActiveError("No pending MFA enrollment found")
    if credential.activated:
        raise MfaAlreadyActiveError("MFA is already activated")

    secret = _decrypt_totp_secret(credential.totp_secret_encrypted, settings)
    totp = pyotp.TOTP(secret)
    # valid_window=1 allows ±30s drift (one adjacent window) which is typical for
    # authenticator apps with slight clock skew (NIST SP 800-63B §5.1.4.2).
    if not totp.verify(totp_code, valid_window=1):
        raise MfaInvalidCodeError("Invalid or expired TOTP code")

    now = datetime.now(UTC)
    credential.activated = True
    credential.activated_at = now

    # Generate backup codes; store only hashes.
    plaintext_codes = _generate_backup_codes(settings.mfa_backup_code_count)
    for code in plaintext_codes:
        backup = MfaBackupCode(
            mfa_credential_id=credential.id,
            user_id=user.id,
            code_hash=_hash_backup_code(code),
        )
        session.add(backup)

    await session.flush()
    return MfaActivateResult(backup_codes=plaintext_codes)


# --- Second-factor verification (login enforcement) --------------------------


async def verify_mfa_code(
    session: AsyncSession,
    user: User,
    *,
    code: str,
    settings: Settings | None = None,
) -> None:
    """Verify a TOTP code or backup code for an MFA-enabled user.

    Accepts either a 6-digit TOTP code (``code`` is all digits, len 6) or an
    uppercase hex backup code (consumed and marked used). Raises
    :class:`MfaNotActiveError` if MFA is not active for this user, and
    :class:`MfaInvalidCodeError` if the code is wrong or already used.
    """
    settings = settings or get_settings()

    credential = await get_active_mfa_credential(session, user.id)
    if credential is None:
        raise MfaNotActiveError("MFA is not active for this account")

    # Backup code path: non-digit or 8-char hex-style codes.
    if not code.isdigit() or len(code) != 6:
        return await _verify_backup_code(session, credential, code)

    # TOTP path.
    secret = _decrypt_totp_secret(credential.totp_secret_encrypted, settings)
    totp = pyotp.TOTP(secret)
    if not totp.verify(code, valid_window=1):
        # Also try backup code path in case the code happens to be numeric (8-char numeric backup).
        try:
            return await _verify_backup_code(session, credential, code)
        except MfaInvalidCodeError:
            pass
        raise MfaInvalidCodeError("Invalid or expired TOTP code")


async def _verify_backup_code(
    session: AsyncSession,
    credential: MfaCredential,
    code: str,
) -> None:
    """Consume a backup code (marks it used). Raises :class:`MfaInvalidCodeError` if invalid."""
    code_hash = _hash_backup_code(code)
    result = await session.execute(
        select(MfaBackupCode).where(
            MfaBackupCode.mfa_credential_id == credential.id,
            MfaBackupCode.code_hash == code_hash,
            MfaBackupCode.used_at.is_(None),
        )
    )
    backup = result.scalar_one_or_none()
    if backup is None:
        raise MfaInvalidCodeError("Invalid or already-used backup code")
    backup.used_at = datetime.now(UTC)
    await session.flush()


# --- Disable MFA -------------------------------------------------------------


async def disable_mfa(
    session: AsyncSession,
    user: User,
    *,
    code: str,
    settings: Settings | None = None,
) -> None:
    """Disable MFA after re-authentication with a TOTP/backup code.

    Clears the ``auth_mfa_credential`` row (cascade-deletes backup codes).
    Raises :class:`MfaNotActiveError` if MFA is not active.
    Raises :class:`MfaInvalidCodeError` if the code is wrong.
    """
    settings = settings or get_settings()
    # verify_mfa_code will raise MfaNotActiveError or MfaInvalidCodeError as needed.
    await verify_mfa_code(session, user, code=code, settings=settings)

    # Delete the credential (cascade deletes auth_mfa_backup_code rows).
    await session.execute(delete(MfaCredential).where(MfaCredential.user_id == user.id))
    await session.flush()


# --- Regenerate backup codes -------------------------------------------------


async def regenerate_backup_codes(
    session: AsyncSession,
    user: User,
    *,
    code: str,
    settings: Settings | None = None,
) -> list[str]:
    """Replace all backup codes after re-auth with a TOTP/backup code.

    Returns the new plaintext backup codes (shown once). Raises
    :class:`MfaNotActiveError` / :class:`MfaInvalidCodeError` as appropriate.
    """
    settings = settings or get_settings()

    credential = await get_active_mfa_credential(session, user.id)
    if credential is None:
        raise MfaNotActiveError("MFA is not active for this account")

    # Re-auth: verify the TOTP or backup code first.
    await verify_mfa_code(session, user, code=code, settings=settings)

    # Delete all existing backup codes for this credential.
    await session.execute(
        delete(MfaBackupCode).where(MfaBackupCode.mfa_credential_id == credential.id)
    )

    plaintext_codes = _generate_backup_codes(settings.mfa_backup_code_count)
    for c in plaintext_codes:
        session.add(
            MfaBackupCode(
                mfa_credential_id=credential.id,
                user_id=user.id,
                code_hash=_hash_backup_code(c),
            )
        )
    await session.flush()
    return plaintext_codes


# --- Login enforcement -------------------------------------------------------
# ``authenticate`` already verifies email+password and returns the user.
# When MFA is active, the route must NOT issue the full token pair — instead
# it issues a short-lived mfa_challenge JWT and returns an MfaRequiredError.
# The client presents the challenge token + TOTP at /auth/mfa/verify to obtain
# the real token pair. This function wraps ``authenticate`` for MFA-aware routes.


async def authenticate_with_mfa_check(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    settings: Settings | None = None,
) -> User:
    """Authenticate email+password and enforce MFA if active.

    Wraps :func:`authenticate`. If the user has MFA activated, raises
    :class:`MfaRequiredError` (with a short-lived challenge token) instead of
    returning the user directly. The caller MUST catch this and return the
    challenge token to the client rather than a full token pair.

    All other errors (:class:`InvalidCredentialsError`, :class:`EmailNotVerifiedError`)
    propagate unchanged.
    """
    settings = settings or get_settings()
    user = await authenticate(session, email=email, password=password, settings=settings)

    credential = await get_active_mfa_credential(session, user.id)
    if credential is not None:
        challenge = issue_mfa_challenge_token(user.id, settings=settings)
        raise MfaRequiredError(challenge)

    return user
