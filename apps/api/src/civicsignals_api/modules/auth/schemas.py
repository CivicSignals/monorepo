"""Pydantic request/response shapes for the auth module (doc 06 §3, doc 08 §3.1)."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field

from civicsignals_api.modules.accounts.schemas import UserOut

# bcrypt operates on the first 72 bytes; cap length to keep behaviour predictable
# and avoid wasting work on absurd inputs.
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
