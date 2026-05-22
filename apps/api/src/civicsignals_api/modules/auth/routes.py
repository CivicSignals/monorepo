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
from urllib.parse import urlencode
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api import events
from civicsignals_api.config import Settings, get_settings
from civicsignals_api.db import get_session
from civicsignals_api.events import (
    AUTH_LOGIN,
    AUTH_PASSWORD_RESET_COMPLETED,
    AUTH_PASSWORD_RESET_REQUESTED,
)
from civicsignals_api.modules.accounts import services as accounts_services
from civicsignals_api.modules.accounts.schemas import UserOut
from civicsignals_api.modules.notifications import services as notifications_services
from civicsignals_api.problems import ProblemException

from . import services as auth_services
from .dependencies import CurrentUser
from .models import ApiToken, ApiTokenType
from .schemas import (
    ApiTokenCreate,
    ApiTokenCreated,
    ApiTokenList,
    ApiTokenOut,
    ApiTokenScopesOut,
    AuthResponse,
    LoginRequest,
    MessageResponse,
    MfaActivateResponse,
    MfaDisableBody,
    MfaEnrollBeginResponse,
    MfaLoginVerifyBody,
    MfaRegenerateCodesBody,
    MfaRegenerateCodesResponse,
    MfaRequiredResponse,
    MfaStatusResponse,
    MfaVerifyBody,
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


@router.post("/login", response_model=AuthResponse | MfaRequiredResponse)
async def login(
    body: LoginRequest, session: SessionDep, settings: SettingsDep
) -> AuthResponse | MfaRequiredResponse:
    """Email+password login. When MFA is active returns an ``mfa_required`` challenge.

    If the account has MFA enabled, the response is a :class:`MfaRequiredResponse`
    (HTTP 200 with ``mfa_required: true``). The client must present the
    ``challenge_token`` to ``POST /auth/mfa/verify`` with a TOTP/backup code.
    A full :class:`AuthResponse` is only returned when MFA is not active.
    """
    try:
        user = await auth_services.authenticate_with_mfa_check(
            session,
            email=str(body.email),
            password=body.password,
            settings=settings,
        )
        await session.commit()
    except auth_services.MfaRequiredError as exc:
        # First factor succeeded; MFA is active — return the challenge token.
        # Commit the touch_last_seen from authenticate() before returning.
        await session.commit()
        return MfaRequiredResponse(challenge_token=exc.challenge_token)
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

    # B9: emit login audit event (best-effort; never fails the response).
    try:
        await events.publish(AUTH_LOGIN, {"user_id": str(user.id), "email": user.email})
    except Exception:
        logger.warning("auth_login_event_failed", user_id=str(user.id))

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
        # B9: the admin audit listener persists this event as an audit row.
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

    # B9: the admin audit listener persists this event as an audit row.
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


# --- B8: Personal access tokens (PATs) --------------------------------------
# Managed by the owning user (no admin gate — a PAT acts *as* that user across
# their workspaces, doc 08 §1.3). The secret is revealed once on create and never
# again (threat-model §4.2). Workspace API tokens live under
# ``/workspaces/{id}/api-tokens`` (admin-gated, in the accounts module).

# Nested under the module's ``/auth`` prefix → ``/api/v1/auth/tokens`` (the
# ``router`` below already carries ``/auth``, so this adds only ``/tokens``).
tokens_router = APIRouter(prefix="/tokens", tags=["api-tokens"])


def _bad_scopes(invalid: list[str]) -> ProblemException:
    return ProblemException(
        status=422,
        code="invalid_scope",
        title="Unknown token scope",
        detail=f"Unknown scope(s): {', '.join(invalid)}.",
        errors=[{"field": "scopes", "code": "unknown_scope", "message": s} for s in invalid],
    )


@tokens_router.get(
    "/scopes",
    response_model=ApiTokenScopesOut,
    summary="List the grantable API-token scopes",
)
async def list_token_scopes() -> ApiTokenScopesOut:
    """Return the catalog of scopes a token may be granted (doc 08 §1.3)."""
    return ApiTokenScopesOut(scopes=list(auth_services.API_TOKEN_SCOPES))


@tokens_router.post(
    "",
    response_model=ApiTokenCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create a personal access token (revealed once)",
)
async def create_personal_token(
    body: ApiTokenCreate,
    current_user: CurrentUser,
    session: SessionDep,
    response: Response,
) -> ApiTokenCreated:
    """Mint a PAT for the current user; the plaintext is returned **once**.

    A PAT acts as the owning user across all their workspaces (doc 08 §1.3), so
    any authenticated user may create one for themselves. The secret in the
    ``token`` field is never retrievable again.
    """
    try:
        issued = await auth_services.create_api_token(
            session,
            token_type=ApiTokenType.PERSONAL,
            name=body.name,
            scopes=body.scopes,
            user_id=current_user.id,
            created_by_user_id=current_user.id,
        )
        await session.commit()
    except auth_services.InvalidScopeError as exc:
        await session.rollback()
        raise _bad_scopes(exc.invalid) from exc

    response.headers["Location"] = f"/api/v1/auth/tokens/{issued.token.id}"
    out = ApiTokenOut.model_validate(issued.token)
    return ApiTokenCreated(**out.model_dump(), token=issued.plaintext)


@tokens_router.get(
    "",
    response_model=ApiTokenList,
    summary="List my personal access tokens (no secrets)",
)
async def list_personal_tokens(current_user: CurrentUser, session: SessionDep) -> ApiTokenList:
    """List the caller's PATs as metadata only — the secret is never returned."""
    tokens = await auth_services.list_personal_api_tokens(session, current_user.id)
    return ApiTokenList(items=[ApiTokenOut.model_validate(t) for t in tokens])


def _ensure_personal_token_owner(token: ApiToken | None, user_id: UUID) -> ApiToken:
    """Resolve a PAT owned by ``user_id`` or raise ``404`` (existence not leaked)."""
    if token is None or token.token_type is not ApiTokenType.PERSONAL or token.user_id != user_id:
        raise ProblemException(
            status=status.HTTP_404_NOT_FOUND,
            code="not_found",
            title="Token not found",
            detail="No such personal access token.",
        )
    return token


@tokens_router.delete(
    "/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a personal access token",
)
async def revoke_personal_token(
    token_id: UUID, current_user: CurrentUser, session: SessionDep
) -> Response:
    """Revoke one of the caller's PATs. Idempotent; immediate (doc 08 §1.3)."""
    token = _ensure_personal_token_owner(
        await auth_services.get_api_token(session, token_id), current_user.id
    )
    await auth_services.revoke_api_token(session, token)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


router.include_router(tokens_router)


# ---------------------------------------------------------------------------
# B4: MFA / TOTP endpoints
# ---------------------------------------------------------------------------
# All MFA endpoints are mounted under the existing ``/auth`` prefix:
#   POST /api/v1/auth/mfa/enroll            — begin enrollment
#   POST /api/v1/auth/mfa/enroll/verify     — activate (confirm live code)
#   POST /api/v1/auth/mfa/verify            — second-factor login (challenge → tokens)
#   GET  /api/v1/auth/mfa/status            — MFA status for current user
#   POST /api/v1/auth/mfa/disable           — disable MFA (requires re-auth)
#   POST /api/v1/auth/mfa/backup-codes/regenerate  — replace backup codes (re-auth)

_mfa_router = APIRouter(prefix="/mfa", tags=["auth", "mfa"])


def _mfa_problem(code: str, title: str, detail: str) -> ProblemException:
    return ProblemException(
        status=status.HTTP_400_BAD_REQUEST,
        code=code,
        title=title,
        detail=detail,
    )


def _mfa_401(detail: str) -> ProblemException:
    return ProblemException(
        status=status.HTTP_401_UNAUTHORIZED,
        code="mfa_invalid_code",
        title="Invalid MFA code",
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


@_mfa_router.post(
    "/enroll",
    response_model=MfaEnrollBeginResponse,
    status_code=status.HTTP_200_OK,
    summary="Begin TOTP MFA enrollment — returns provisioning URI",
)
async def mfa_enroll_begin(
    current_user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> MfaEnrollBeginResponse:
    """Start TOTP enrollment for the current user.

    Generates a new TOTP secret, stores it encrypted (not yet activated), and
    returns the ``otpauth://`` provisioning URI and base32 secret. The secret is
    shown **once**; submit it to an authenticator app, then call
    ``POST /auth/mfa/enroll/verify`` with a live code to activate.

    Returns ``400`` if MFA is already activated.
    """
    try:
        result = await auth_services.begin_mfa_enrollment(session, current_user, settings=settings)
        await session.commit()
    except auth_services.MfaAlreadyActiveError as exc:
        await session.rollback()
        raise _mfa_problem(
            "mfa_already_active",
            "MFA already active",
            "MFA is already activated for this account. Disable it first to re-enroll.",
        ) from exc
    return MfaEnrollBeginResponse(totp_uri=result.totp_uri, secret=result.secret)


@_mfa_router.post(
    "/enroll/verify",
    response_model=MfaActivateResponse,
    status_code=status.HTTP_200_OK,
    summary="Activate MFA — confirm a live TOTP code, receive backup codes",
)
async def mfa_enroll_verify(
    body: MfaVerifyBody,
    current_user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> MfaActivateResponse:
    """Confirm a live TOTP code and activate MFA for the current user.

    On success: activates MFA and returns ``backup_codes`` — the one-time
    plaintext recovery codes. These are shown **once**; the server stores only
    their SHA-256 hashes.

    Returns ``400`` on bad/expired code or if enrollment was not started.
    """
    try:
        result = await auth_services.activate_mfa(
            session, current_user, totp_code=body.code, settings=settings
        )
        await session.commit()
    except auth_services.MfaNotActiveError as exc:
        await session.rollback()
        raise _mfa_problem(
            "mfa_not_enrolled",
            "MFA enrollment not started",
            "Call POST /auth/mfa/enroll first.",
        ) from exc
    except auth_services.MfaAlreadyActiveError as exc:
        await session.rollback()
        raise _mfa_problem(
            "mfa_already_active",
            "MFA already active",
            "MFA is already activated.",
        ) from exc
    except auth_services.MfaInvalidCodeError as exc:
        await session.rollback()
        raise _mfa_401("Invalid or expired TOTP code. Check your authenticator app.") from exc
    return MfaActivateResponse(backup_codes=result.backup_codes)


@_mfa_router.post(
    "/verify",
    response_model=AuthResponse,
    status_code=status.HTTP_200_OK,
    summary="Complete MFA login — exchange challenge token + TOTP/backup code for tokens",
)
async def mfa_login_verify(
    body: MfaLoginVerifyBody,
    session: SessionDep,
    settings: SettingsDep,
) -> AuthResponse:
    """Complete the second factor of MFA login.

    Accepts the short-lived ``challenge_token`` from the ``mfa_required`` login
    response plus a TOTP code or backup code. On success issues a full
    access+refresh token pair. Returns ``401`` on invalid/expired codes.
    """
    try:
        user_id = auth_services.verify_mfa_challenge_token(body.challenge_token, settings=settings)
    except auth_services.MfaChallengeTokenError as exc:
        raise ProblemException(
            status=status.HTTP_401_UNAUTHORIZED,
            code="invalid_mfa_challenge",
            title="Invalid or expired MFA challenge",
            detail="The MFA challenge token is invalid or expired. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    from civicsignals_api.modules.accounts import services as accounts_services

    user = await accounts_services.get_user_by_id(session, user_id)
    if user is None:
        raise ProblemException(
            status=status.HTTP_401_UNAUTHORIZED,
            code="user_not_found",
            title="User not found",
            detail="The user associated with this challenge no longer exists.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        await auth_services.verify_mfa_code(session, user, code=body.code, settings=settings)
        await session.commit()
    except auth_services.MfaNotActiveError as exc:
        await session.rollback()
        raise ProblemException(
            status=status.HTTP_400_BAD_REQUEST,
            code="mfa_not_active",
            title="MFA not active",
            detail="MFA is not active for this account.",
        ) from exc
    except auth_services.MfaInvalidCodeError as exc:
        await session.rollback()
        raise _mfa_401("Invalid or expired MFA code.") from exc

    # B9: emit login audit event (best-effort).
    try:
        await events.publish(AUTH_LOGIN, {"user_id": str(user.id), "email": user.email})
    except Exception:
        logger.warning("mfa_login_event_failed", user_id=str(user.id))

    return AuthResponse(
        user=UserOut.model_validate(user),
        tokens=_token_pair(user.id, settings),
    )


@_mfa_router.get(
    "/status",
    response_model=MfaStatusResponse,
    summary="MFA status for the current user",
)
async def mfa_status(
    current_user: CurrentUser,
    session: SessionDep,
) -> MfaStatusResponse:
    """Return whether MFA is active for the current user."""
    credential = await auth_services.get_active_mfa_credential(session, current_user.id)
    if credential is None:
        return MfaStatusResponse(mfa_enabled=False)
    return MfaStatusResponse(mfa_enabled=True, activated_at=credential.activated_at)


@_mfa_router.post(
    "/disable",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Disable MFA (requires a fresh TOTP or backup code)",
)
async def mfa_disable(
    body: MfaDisableBody,
    current_user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> MessageResponse:
    """Disable MFA for the current user.

    Requires a fresh TOTP or backup code as re-authentication. Clears the TOTP
    secret and all backup codes. Returns ``400`` if MFA is not active, ``401``
    on invalid code.
    """
    try:
        await auth_services.disable_mfa(session, current_user, code=body.code, settings=settings)
        await session.commit()
    except auth_services.MfaNotActiveError as exc:
        await session.rollback()
        raise _mfa_problem(
            "mfa_not_active",
            "MFA not active",
            "MFA is not currently active for this account.",
        ) from exc
    except auth_services.MfaInvalidCodeError as exc:
        await session.rollback()
        raise _mfa_401(
            "Invalid or already-used code. Provide a fresh TOTP or backup code."
        ) from exc
    return MessageResponse(message="MFA disabled")


@_mfa_router.post(
    "/backup-codes/regenerate",
    response_model=MfaRegenerateCodesResponse,
    status_code=status.HTTP_200_OK,
    summary="Regenerate backup codes (requires re-auth; old codes are invalidated)",
)
async def mfa_regenerate_backup_codes(
    body: MfaRegenerateCodesBody,
    current_user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> MfaRegenerateCodesResponse:
    """Regenerate backup codes for the current user.

    All existing backup codes are deleted; the new ones are returned once.
    Requires a fresh TOTP or backup code as re-authentication. Returns ``400``
    if MFA is not active, ``401`` on invalid code.
    """
    try:
        codes = await auth_services.regenerate_backup_codes(
            session, current_user, code=body.code, settings=settings
        )
        await session.commit()
    except auth_services.MfaNotActiveError as exc:
        await session.rollback()
        raise _mfa_problem(
            "mfa_not_active",
            "MFA not active",
            "MFA is not currently active for this account.",
        ) from exc
    except auth_services.MfaInvalidCodeError as exc:
        await session.rollback()
        raise _mfa_401(
            "Invalid or already-used code. Provide a fresh TOTP or backup code."
        ) from exc
    return MfaRegenerateCodesResponse(backup_codes=codes)


router.include_router(_mfa_router)


# ---------------------------------------------------------------------------
# B2: Google OAuth2 authorization-code flow
# ---------------------------------------------------------------------------
# Mounted under the existing ``/auth`` prefix → ``/api/v1/auth/oauth/google``.
# The start endpoint redirects the browser to Google; the callback endpoint
# receives the code + state, resolves/creates the user, and redirects the
# browser to the web app with the issued tokens in the URL fragment (same
# pattern as doc 08 §3.1 "Callback redirect — token in fragment").
#
# Error handling follows RFC 7807; a state mismatch / unverified email / Google
# error redirects to the web app's /login?error=... so the user gets a readable
# message instead of a raw 400 page in the browser.

_oauth_router = APIRouter(prefix="/oauth/google", tags=["auth", "oauth"])


@_oauth_router.get(
    "/start",
    status_code=status.HTTP_302_FOUND,
    summary="Start Google OAuth2 sign-in (redirect to Google)",
    response_description="302 redirect to Google's authorization page",
    include_in_schema=True,
)
async def google_oauth_start(settings: SettingsDep) -> RedirectResponse:
    """Redirect the browser to Google's OAuth2 authorization page.

    Returns ``501 Not Implemented`` when ``GOOGLE_OAUTH_CLIENT_ID`` is not
    configured so the error is clear in both development and production, rather
    than a cryptic crash.
    """
    try:
        result = auth_services.google_oauth_start(settings=settings)
    except auth_services.OAuthProviderError as exc:
        raise ProblemException(
            status=status.HTTP_501_NOT_IMPLEMENTED,
            code="oauth_not_configured",
            title="Google OAuth not configured",
            detail=str(exc),
        ) from exc
    return RedirectResponse(url=result.authorization_url, status_code=status.HTTP_302_FOUND)


@_oauth_router.get(
    "/callback",
    status_code=status.HTTP_302_FOUND,
    summary="Google OAuth2 callback — exchange code, link/create account, issue JWT",
    response_description="302 redirect to web app with access token",
    include_in_schema=True,
)
async def google_oauth_callback(
    session: SessionDep,
    settings: SettingsDep,
    code: str | None = Query(default=None, description="Authorization code from Google"),
    state: str | None = Query(default=None, description="State nonce returned by Google"),
    error: str | None = Query(default=None, description="Error code from Google (user cancelled)"),
) -> RedirectResponse:
    """Complete the Google OAuth2 flow: exchange code, link/create user, issue JWT.

    On success redirects to ``{web_base_url}/auth/callback/google#access_token=...``
    so the web app can store the token client-side without it appearing in the
    server logs (token in URL fragment, not query string).

    On error (including user-cancelled consent, where Google redirects back with
    ``?error=access_denied`` and no ``code``) redirects to
    ``{web_base_url}/login?error=<code>`` so the user sees a readable message
    in the browser (not a raw API 422 response).
    """

    def _error_redirect(code_str: str, detail: str) -> RedirectResponse:
        params = urlencode({"error": code_str, "detail": detail})
        url = f"{settings.web_base_url}/login?{params}"
        return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)

    # Handle the case where Google redirects back with an error (e.g. user
    # cancelled consent) — no ``code`` is present in that case.
    if error or not code or not state:
        detail = error or "missing_code"
        return _error_redirect(
            "oauth_denied", f"Google sign-in was denied or cancelled ({detail})."
        )

    try:
        result = await auth_services.google_oauth_callback(
            session,
            code=code,
            state=state,
            settings=settings,
        )
        await session.commit()
    except auth_services.OAuthStateMismatchError:
        await session.rollback()
        return _error_redirect(
            "state_mismatch", "Sign-in session expired or tampered. Please try again."
        )
    except auth_services.OAuthEmailUnverifiedError:
        await session.rollback()
        return _error_redirect(
            "email_unverified",
            "Your Google account's email is not verified. Please verify it with Google first.",
        )
    except auth_services.OAuthProviderError as exc:
        await session.rollback()
        logger.warning("google_oauth_provider_error", detail=str(exc))
        return _error_redirect("provider_error", "Google sign-in failed. Please try again.")
    except IntegrityError as exc:
        await session.rollback()
        exc_str = str(exc.orig) if exc.orig else str(exc)
        if "uq_auth_oauth_identity_provider_user" in exc_str:
            # (provider, user_id) constraint: this CivicSignals account already
            # has a different Google account linked.
            return _error_redirect(
                "identity_conflict",
                "Your CivicSignals account is already linked to a different Google account.",
            )
        if "uq_auth_oauth_identity_provider_subject" in exc_str:
            # (provider, subject) constraint: this Google account is linked to
            # a different CivicSignals account.
            return _error_redirect(
                "identity_conflict",
                "This Google account is already linked to another CivicSignals account.",
            )
        # Unrelated integrity error — log and surface as a generic server error.
        logger.error("google_oauth_unexpected_integrity_error", exc=str(exc))
        raise ProblemException(
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="internal_error",
            title="Internal server error",
            detail="An unexpected error occurred during sign-in. Please try again.",
        ) from exc

    # Emit login audit event (best-effort).
    try:
        await events.publish(
            AUTH_LOGIN,
            {"user_id": str(result.user.id), "email": result.user.email, "provider": "google"},
        )
    except Exception:
        logger.warning("google_oauth_login_event_failed", user_id=str(result.user.id))

    pair = _token_pair(result.user.id, settings)
    # Redirect to the web app callback page with the access token in the URL
    # fragment (not query string) so it doesn't appear in server logs or
    # Referer headers. urlencode ensures special characters are percent-encoded.
    # Refresh token is intentionally omitted from the fragment: the web client
    # does not yet have secure refresh-token storage (B3 follow-up), and
    # exposing a long-lived token in the fragment increases exposure risk.
    fragment = urlencode(
        {
            "access_token": pair.access_token,
            "expires_in": pair.expires_in,
            "token_type": "bearer",
        }
    )
    url = f"{settings.web_base_url}/auth/callback/google#{fragment}"
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


router.include_router(_oauth_router)
