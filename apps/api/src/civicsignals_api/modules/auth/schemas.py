"""Pydantic request/response shapes for the auth module (doc 06 §3, doc 08 §3.1)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from civicsignals_api.modules.accounts.schemas import UserOut

from .models import ApiTokenType

# bcrypt operates on the first 72 bytes; cap length to keep behaviour predictable
# and avoid wasting work on absurd inputs. Consistent with B1 signup policy.
PASSWORD_MIN_LEN = 8
PASSWORD_MAX_LEN = 128


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=PASSWORD_MIN_LEN, max_length=PASSWORD_MAX_LEN)
    name: str | None = Field(default=None, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LEN)
    # MFA arrives in B4; accepted now so the contract is forward-compatible
    # (doc 08 §3.1). Ignored until B4 wires TOTP verification.
    mfa_code: str | None = None


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=1)


class TokenPair(BaseModel):
    """Bearer access (+refresh) JWTs returned by signup/login (doc 08 §1.3)."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class AuthResponse(BaseModel):
    """Login/signup response: the user plus the issued token pair."""

    user: UserOut
    tokens: TokenPair


class SignupResponse(BaseModel):
    """Signup response — the new user plus tokens. ``email_verification_required``
    tells the client whether login is gated on verifying the email first."""

    user: UserOut
    tokens: TokenPair
    email_verification_required: bool


class MessageResponse(BaseModel):
    message: str


# --- B3: Password reset -------------------------------------------------------


class PasswordResetRequestBody(BaseModel):
    """Body for POST /auth/password-reset/request.

    Only the email is required; the response is ALWAYS 204 to prevent user
    enumeration (doc 08 §3.1, threat-model §4.2).
    """

    email: EmailStr


class PasswordResetConfirmBody(BaseModel):
    """Body for POST /auth/password-reset/confirm.

    ``token`` is the opaque URL-safe token from the reset email. ``new_password``
    is validated against the same policy as signup (B1) so users can't set a
    trivially weak password via the reset path.
    """

    token: str = Field(min_length=1)
    new_password: str = Field(min_length=PASSWORD_MIN_LEN, max_length=PASSWORD_MAX_LEN)


# --- B8: API tokens ----------------------------------------------------------

TOKEN_NAME_MAX_LEN = 120


class ApiTokenCreate(BaseModel):
    """Body for creating a workspace or personal API token (doc 08 §1.3).

    ``scopes`` is validated against the catalog in ``auth.services`` server-side;
    an empty list yields a token that can authenticate but pass no scope gate
    (useful for an identity-only token). ``expires_at`` is optional (doc 08
    §1.3). ``name`` is a human label like ``"Salesforce push"``.
    """

    name: str = Field(min_length=1, max_length=TOKEN_NAME_MAX_LEN)
    scopes: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None

    @field_validator("scopes")
    @classmethod
    def _strip_scopes(cls, scopes: list[str]) -> list[str]:
        return [scope.strip() for scope in scopes if scope.strip()]


class ApiTokenOut(BaseModel):
    """Public metadata for an API token — never includes the secret (doc 08 §1.3).

    Returned by list endpoints and the non-secret part of create. ``token_prefix``
    is a non-secret display fragment (``cs_pat_a1b2``). ``revoked`` /
    ``last_used_at`` / ``expires_at`` support the hygiene UI.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    token_type: ApiTokenType
    name: str
    token_prefix: str
    scopes: list[str]
    workspace_id: UUID | None = None
    user_id: UUID
    created_by_user_id: UUID | None = None
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None


class ApiTokenCreated(ApiTokenOut):
    """Create response — :class:`ApiTokenOut` plus the cleartext secret.

    The ``token`` field is the **only** time the plaintext secret is returned
    ("revealed once", doc 08 §3.6 webhook-secret pattern / §1.3); it is never
    retrievable again and is omitted from every list/get response.
    """

    token: str


class ApiTokenList(BaseModel):
    """A list of API token metadata (no secrets)."""

    items: list[ApiTokenOut]


class ApiTokenScopesOut(BaseModel):
    """The catalog of grantable scopes (for the management UI to render)."""

    scopes: list[str]


# --- B4: MFA / TOTP ----------------------------------------------------------


class MfaEnrollBeginResponse(BaseModel):
    """Response to POST /auth/mfa/enroll — provisioning URI + base32 secret.

    ``totp_uri`` is the ``otpauth://totp/...`` URI for QR code rendering.
    ``secret`` is the raw base32 secret for manual entry in an authenticator app.
    Neither field is stored in the DB after this response — they are shown once.
    """

    totp_uri: str
    secret: str


class MfaVerifyBody(BaseModel):
    """Body for POST /auth/mfa/enroll/verify (activate) and POST /auth/mfa/verify (login).

    ``code`` is either a 6-digit TOTP code or an 8-char uppercase backup code.
    """

    code: str = Field(min_length=1, max_length=64)


class MfaLoginVerifyBody(BaseModel):
    """Body for POST /auth/mfa/verify (second-factor login step).

    ``challenge_token`` is the short-lived token returned in the ``mfa_required``
    login response. ``code`` is a TOTP or backup code.
    """

    challenge_token: str = Field(min_length=1)
    code: str = Field(min_length=1, max_length=64)


class MfaRequiredResponse(BaseModel):
    """Response when MFA is required during login.

    ``mfa_required`` is always True; ``challenge_token`` is a short-lived JWT
    (TTL = ``mfa_challenge_ttl_seconds``) that the client presents to
    ``POST /auth/mfa/verify`` along with the TOTP/backup code to receive the
    full token pair.
    """

    mfa_required: bool = True
    challenge_token: str


class MfaActivateResponse(BaseModel):
    """Response to POST /auth/mfa/enroll/verify — the one-time backup codes.

    ``backup_codes`` is returned **once only**; the server stores only their
    SHA-256 hashes. The client must display them for the user to save.
    """

    backup_codes: list[str]


class MfaDisableBody(BaseModel):
    """Body for POST /auth/mfa/disable — requires a fresh TOTP or backup code."""

    code: str = Field(min_length=1, max_length=64)


class MfaRegenerateCodesBody(BaseModel):
    """Body for POST /auth/mfa/backup-codes/regenerate — requires re-auth."""

    code: str = Field(min_length=1, max_length=64)


class MfaRegenerateCodesResponse(BaseModel):
    """Response to the regenerate endpoint — new backup codes shown once."""

    backup_codes: list[str]


class MfaStatusResponse(BaseModel):
    """Response to GET /auth/mfa/status — whether MFA is active for the current user."""

    mfa_enabled: bool
    activated_at: datetime | None = None
