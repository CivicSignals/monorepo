"""Unit tests for JWT issue/verify + password hashing (no DB needed)."""

from __future__ import annotations

import time
from uuid import uuid4

import jwt
import pytest

from civicsignals_api.config import Settings
from civicsignals_api.modules.auth import services as auth_services


def _settings(**overrides: object) -> Settings:
    base = {
        "jwt_secret": "test-secret",
        "jwt_algorithm": "HS256",
        "jwt_issuer": "civicsignals",
        "access_token_ttl_seconds": 3600,
        "refresh_token_ttl_seconds": 86400,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_password_hash_roundtrip() -> None:
    h = auth_services.hash_password("correct horse battery staple")
    assert h != "correct horse battery staple"
    assert auth_services.verify_password("correct horse battery staple", h)
    assert not auth_services.verify_password("wrong", h)
    assert not auth_services.verify_password("anything", None)


def test_access_token_roundtrip() -> None:
    settings = _settings()
    uid = uuid4()
    token = auth_services.issue_access_token(uid, settings=settings)
    assert auth_services.verify_token(token, expected_type="access", settings=settings) == uid


def test_refresh_token_type_is_enforced() -> None:
    settings = _settings()
    uid = uuid4()
    refresh = auth_services.issue_refresh_token(uid, settings=settings)
    # A refresh token must not be accepted where an access token is expected.
    with pytest.raises(auth_services.InvalidTokenError):
        auth_services.verify_token(refresh, expected_type="access", settings=settings)
    assert auth_services.verify_token(refresh, expected_type="refresh", settings=settings) == uid


def test_expired_token_is_rejected() -> None:
    settings = _settings(access_token_ttl_seconds=-1)
    token = auth_services.issue_access_token(uuid4(), settings=settings)
    with pytest.raises(auth_services.InvalidTokenError):
        auth_services.verify_token(token, settings=settings)


def test_wrong_secret_is_rejected() -> None:
    issued = auth_services.issue_access_token(uuid4(), settings=_settings(jwt_secret="a"))
    with pytest.raises(auth_services.InvalidTokenError):
        auth_services.verify_token(issued, settings=_settings(jwt_secret="b"))


def test_alg_none_token_is_rejected() -> None:
    """Algorithm-confusion / unsigned tokens are refused (threat-model §4.2)."""
    settings = _settings()
    forged = jwt.encode(
        {"sub": str(uuid4()), "type": "access", "iss": "civicsignals", "exp": time.time() + 999},
        key="",
        algorithm="none",
    )
    with pytest.raises(auth_services.InvalidTokenError):
        auth_services.verify_token(forged, settings=settings)


def test_wrong_issuer_is_rejected() -> None:
    issued = auth_services.issue_access_token(uuid4(), settings=_settings(jwt_issuer="evil"))
    with pytest.raises(auth_services.InvalidTokenError):
        auth_services.verify_token(issued, settings=_settings(jwt_issuer="civicsignals"))
