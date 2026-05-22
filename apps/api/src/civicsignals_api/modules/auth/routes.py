"""HTTP endpoints for the auth module, mounted under ``/api/v1/auth`` (doc 08 §3.1).

Email/password identity (B1): signup, login, logout, email verification, and the
current-user lookup. Password reset (B3): request + confirm.

Errors are RFC 7807 ``application/problem+json`` (doc 08 §1.7) raised as
:class:`ProblemException`. Bearer JWTs are issued by ``auth.services``; the
reusable ``get_current_user`` dependency guards ``/me``.

# TODO B1: per-IP / per-account rate limiting attaches to /signup, /login, and
#   /verify-email (5/15min/IP, 20/15min/account — threat-model §4.2). The
#   service layer (``authenticate``) is the single credential-check choke point.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api import events
from civicsignals_api.config import Settings, get_settings
from civicsignals_api.db import get_session
from civicsignals_api.events import AUTH_PASSWORD_RESET_COMPLETED, AUTH_PASSWORD_RESET_REQUESTED
from civicsignals_api.modules.accounts import services as accounts_services
from civicsignals_api.modules.accounts.schemas import UserOut
from civicsignals_api.modules.notifications import services as notifications_services
from civicsignals_api.problems import ProblemException

from . import services as auth_services
from .dependencies import CurrentUser
from .schemas import (
    AuthResponse,
    LoginRequest,
    MessageResponse,
    PasswordResetConfirmBody,
    PasswordResetRequestBody,
    SignupRequest,
    SignupResponse,
    TokenPair,
    VerifyEmailRequest,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _token_pair(user_id: UUID, settings: Settings) -> TokenPair:
    return TokenPair(
        access_token=auth_services.issue_access_token(user_id, settings=settings),
        refresh_token=auth_services.issue_refresh_token(user_id, settings=settings),
        expires_in=settings.access_token_ttl_seconds,
    )


def _send_verification_email(email: str, raw_token: str, settings: Settings) -> None:
    link = f"{settings.web_base_url}/verify-email?token={raw_token}"
    message = notifications_services.OutboundEmail(
        to=email,
        subject="Verify your CivicSignals email",
        text_body=(
            "Welcome to CivicSignals.\n\n"
            f"Confirm your email address by visiting:\n{link}\n\n"
            "If you did not create this account, you can ignore this message."
        ),
        html_body=(
            "<p>Welcome to CivicSignals.</p>"
            f'<p>Confirm your email address: <a href="{link}">verify my email</a></p>'
            "<p>If you did not create this account, you can ignore this message.</p>"
        ),
    )
    notifications_services.send_email(message)


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
async def signup(body: SignupRequest, session: SessionDep, settings: SettingsDep) -> SignupResponse:
    try:
        result = await auth_services.signup(
            session,
            email=str(body.email),
            password=body.password,
            name=body.name,
            settings=settings,
        )
        await session.commit()
    except auth_services.InvalidCredentialsError as exc:
        await session.rollback()
        raise ProblemException(
            status=status.HTTP_409_CONFLICT,
            code="email_taken",
            title="Email already registered",
            detail="An account with this email already exists.",
        ) from exc
    except IntegrityError as exc:  # unique-violation race
        await session.rollback()
        raise ProblemException(
            status=status.HTTP_409_CONFLICT,
            code="email_taken",
            title="Email already registered",
            detail="An account with this email already exists.",
        ) from exc

    _send_verification_email(result.user.email, result.verification_token, settings)
    return SignupResponse(
        user=UserOut.model_validate(result.user),
        tokens=_token_pair(result.user.id, settings),
        email_verification_required=settings.require_email_verification,
    )


@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest, session: SessionDep, settings: SettingsDep) -> AuthResponse:
    try:
        user = await auth_services.authenticate(
            session,
            email=str(body.email),
            password=body.password,
            settings=settings,
        )
        await session.commit()
    except auth_services.InvalidCredentialsError as exc:
        await session.rollback()
        raise ProblemException(
            status=status.HTTP_401_UNAUTHORIZED,
            code="invalid_credentials",
            title="Invalid credentials",
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except auth_services.EmailNotVerifiedError as exc:
        await session.rollback()
        raise ProblemException(
            status=status.HTTP_403_FORBIDDEN,
            code="email_not_verified",
            title="Email not verified",
            detail="Verify your email address before signing in.",
        ) from exc

    return AuthResponse(
        user=UserOut.model_validate(user),
        tokens=_token_pair(user.id, settings),
    )


@router.post("/logout", response_model=MessageResponse)
async def logout(response: Response) -> MessageResponse:
    """Log out the current session.

    Bearer JWTs are stateless, so logout is client-side (drop the token). The
    web app also clears its cookie. A server-side token denylist / opaque-session
    revocation is a B-series follow-up (threat-model §4.2: session invalidation
    on password change / MFA enable). We clear the session cookie defensively.
    """
    response.delete_cookie("cs_session", path="/")
    return MessageResponse(message="logged out")


@router.post("/verify-email", response_model=MessageResponse)
async def verify_email(body: VerifyEmailRequest, session: SessionDep) -> MessageResponse:
    try:
        await auth_services.consume_email_verification_token(session, body.token)
        await session.commit()
    except auth_services.VerificationTokenError as exc:
        await session.rollback()
        raise ProblemException(
            status=status.HTTP_400_BAD_REQUEST,
            code="invalid_verification_token",
            title="Invalid verification token",
            detail="This verification link is invalid, expired, or already used.",
        ) from exc
    return MessageResponse(message="email verified")


@router.get("/me", response_model=UserOut)
async def me(current_user: CurrentUser) -> UserOut:
    return UserOut.model_validate(current_user)


# --- B3: Password reset -------------------------------------------------------


def _send_password_reset_email(email: str, raw_token: str, settings: Settings) -> None:
    link = f"{settings.web_base_url}/reset-password?token={raw_token}"
    # Derive a human-readable TTL from settings rather than hard-coding "1 hour"
    # so the copy stays accurate if the operator changes password_reset_ttl_seconds.
    # Use the most natural unit that divides cleanly; fall back to minutes, then
    # seconds for unusual values — avoids misleading rounding (e.g. 90 min → "1 hour").
    ttl_secs = settings.password_reset_ttl_seconds
    if ttl_secs % 3600 == 0:
        n = ttl_secs // 3600
        ttl_str = f"{n} hour{'s' if n != 1 else ''}"
    elif ttl_secs % 60 == 0:
        n = ttl_secs // 60
        ttl_str = f"{n} minute{'s' if n != 1 else ''}"
    else:
        ttl_str = f"{ttl_secs} seconds"
    message = notifications_services.OutboundEmail(
        to=email,
        subject="Reset your CivicSignals password",
        text_body=(
            "You requested a password reset for your CivicSignals account.\n\n"
            f"Reset your password by visiting:\n{link}\n\n"
            f"This link expires in {ttl_str} and can only be used once.\n\n"
            "If you did not request a password reset, you can ignore this message."
        ),
        html_body=(
            "<p>You requested a password reset for your CivicSignals account.</p>"
            f'<p><a href="{link}">Reset my password</a></p>'
            f"<p>This link expires in {ttl_str} and can only be used once.</p>"
            "<p>If you did not request a password reset, you can ignore this message.</p>"
        ),
    )
    notifications_services.send_email(message)


@router.post(
    "/password-reset/request",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Request a password reset email",
    response_description="Always 204 — no enumeration",
)
async def password_reset_request(
    body: PasswordResetRequestBody, session: SessionDep, settings: SettingsDep
) -> Response:
    """Send a password-reset email if the address is registered.

    ALWAYS returns 204 regardless of whether the address is known — this prevents
    user enumeration (doc 08 §3.1, threat-model §4.2). The email is only sent
    when the user actually exists.

    Emits ``auth.password_reset.requested`` on the in-process event bus for audit
    (B9 will persist it).
    """
    email = str(body.email)
    user = await accounts_services.get_user_by_email(session, email)
    if user is not None:
        raw_token = await auth_services.create_password_reset_token(
            session, user, settings=settings
        )
        await session.commit()
        _send_password_reset_email(email, raw_token, settings)
        # TODO B9: persist audit entry — currently emitted on the in-process bus only.
        # Best-effort: a failing subscriber must not leak user existence via 500 vs 204.
        try:
            # Use user.email (normalized/canonical) rather than the raw request
            # value so downstream audit consumers receive consistent casing.
            await events.publish(
                AUTH_PASSWORD_RESET_REQUESTED,
                {"user_id": str(user.id), "email": user.email},
            )
        except Exception:
            logger.warning("password_reset_requested_event_failed")
    else:
        # No commit needed — nothing changed; rollback for cleanliness.
        await session.rollback()
        # Do NOT log the raw email address: this is an unauthenticated endpoint
        # and logging user-supplied addresses would retain PII and could enable
        # log-based user enumeration if logs are ever exposed (threat-model §4.2).
        logger.info("password_reset_request_no_match")

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/password-reset/confirm", response_model=MessageResponse)
async def password_reset_confirm(
    body: PasswordResetConfirmBody, session: SessionDep
) -> MessageResponse:
    """Validate a reset token, set a new password, and consume the token.

    Returns ``400`` if the token is unknown, expired, or already used.
    On success all other pending reset tokens for this user are also invalidated,
    preventing replay of old links.

    Emits ``auth.password_reset.completed`` on the in-process event bus for audit
    (B9 will persist it).
    """
    try:
        user = await auth_services.consume_password_reset_token(
            session, body.token, new_password=body.new_password
        )
        await session.commit()
    except auth_services.PasswordResetTokenError as exc:
        await session.rollback()
        raise ProblemException(
            status=status.HTTP_400_BAD_REQUEST,
            code="invalid_reset_token",
            title="Invalid password-reset token",
            detail="This reset link is invalid, expired, or already used.",
        ) from exc

    # TODO B9: persist audit entry — currently emitted on the in-process bus only.
    # Best-effort: the password is already committed; a failing subscriber must not
    # cause the client to see 500 and potentially retry with the now-consumed token.
    try:
        await events.publish(
            AUTH_PASSWORD_RESET_COMPLETED,
            {"user_id": str(user.id), "email": user.email},
        )
    except Exception:
        logger.warning("password_reset_completed_event_failed", user_id=str(user.id))
    return MessageResponse(message="password reset")
