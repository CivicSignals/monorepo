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

Cross-module rules: user identity is owned by ``accounts`` and reached only via
``accounts.services``; verification mail goes out via ``notifications.services``.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import jwt
from passlib.context import CryptContext
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.config import Settings, get_settings
from civicsignals_api.modules.accounts import services as accounts_services
from civicsignals_api.modules.accounts.models import User

from .models import EmailVerificationToken, PasswordResetToken

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
    """
    # SELECT FOR UPDATE locks the token row so concurrent requests with the same
    # token serialize here rather than both seeing consumed_at IS NULL and both
    # proceeding to set a new password (TOCTOU race, threat-model §4.2).
    result = await session.execute(
        select(PasswordResetToken)
        .where(PasswordResetToken.token_hash == _hash_token(raw_token))
        .with_for_update()
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise PasswordResetTokenError("unknown token")
    if record.consumed_at is not None:
        raise PasswordResetTokenError("token already used")
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < datetime.now(UTC):
        raise PasswordResetTokenError("token expired")

    user = await accounts_services.get_user_by_id(session, record.user_id)
    if user is None:
        raise PasswordResetTokenError("user no longer exists")

    now = datetime.now(UTC)

    # Invalidate all other pending reset tokens for this user so old reset links
    # cannot be replayed after a successful password change.
    await session.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == record.user_id,
            PasswordResetToken.id != record.id,
            PasswordResetToken.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )

    # Consume this token.
    record.consumed_at = now

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
