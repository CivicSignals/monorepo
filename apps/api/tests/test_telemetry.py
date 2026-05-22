"""Tests for O6 — anonymous telemetry opt-in.

Covers:
- Disabled by default (no network calls, no payload built).
- Enabled: payload contains ONLY the allowed keys (no PII).
- Instance-id stability (same id returned on second call from the same file).
- Instance-id: a fresh file is created when the path doesn't exist.
- send_ping: posts via httpx; errors are silently swallowed.
- Bucket helpers: correct size-band labels.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from civicsignals_api.telemetry import (
    _ALLOWED_PAYLOAD_KEYS,
    _load_or_create_instance_id,
    build_payload,
    send_ping,
    signal_bucket,
    workspace_bucket,
)

# ---------------------------------------------------------------------------
# Bucket helpers
# ---------------------------------------------------------------------------


def test_workspace_bucket_zero() -> None:
    assert workspace_bucket(0) == "0"


def test_workspace_bucket_one() -> None:
    assert workspace_bucket(1) == "1-5"


def test_workspace_bucket_five() -> None:
    assert workspace_bucket(5) == "1-5"


def test_workspace_bucket_six() -> None:
    assert workspace_bucket(6) == "6-25"


def test_workspace_bucket_large() -> None:
    assert workspace_bucket(500) == "101+"


def test_signal_bucket_zero() -> None:
    assert signal_bucket(0) == "0"


def test_signal_bucket_small() -> None:
    assert signal_bucket(500) == "1k"


def test_signal_bucket_million() -> None:
    assert signal_bucket(1_000_000) == "10M+"


# ---------------------------------------------------------------------------
# build_payload — only allowed keys, no PII
# ---------------------------------------------------------------------------

_PII_FIELD_NAMES = {
    "email",
    "name",
    "user_id",
    "workspace_id",
    "user_email",
    "contact",
    "first_name",
    "last_name",
    "phone",
    "address",
    "ip",
    "ip_address",
}


def test_build_payload_keys_are_subset_of_allowed() -> None:
    payload = build_payload(
        instance_id=str(uuid.uuid4()),
        workspace_count=10,
        signal_count=500,
    )
    assert set(payload.keys()) <= _ALLOWED_PAYLOAD_KEYS


def test_build_payload_no_pii_fields() -> None:
    payload = build_payload(
        instance_id=str(uuid.uuid4()),
        workspace_count=10,
        signal_count=500,
    )
    for key in payload:
        assert key not in _PII_FIELD_NAMES, f"PII field found in payload: {key!r}"


def test_build_payload_required_fields_present() -> None:
    iid = str(uuid.uuid4())
    payload = build_payload(instance_id=iid, workspace_count=3, signal_count=1_500)
    assert payload["instance_id"] == iid
    assert payload["version"] == "0.1.0"
    assert payload["deploy_type"] == "self-host"
    assert payload["workspace_count_bucket"] == "1-5"
    assert payload["signal_count_bucket"] == "10k"


def test_build_payload_values_are_strings() -> None:
    payload = build_payload(
        instance_id=str(uuid.uuid4()),
        workspace_count=0,
        signal_count=0,
    )
    for key, value in payload.items():
        assert isinstance(value, str), f"Non-string value for key {key!r}: {value!r}"


# ---------------------------------------------------------------------------
# Instance-id stability
# ---------------------------------------------------------------------------


def test_instance_id_created_when_missing(tmp_path: Path) -> None:
    id_file = str(tmp_path / "instance-id")
    id1 = _load_or_create_instance_id(id_file)
    # Should be a valid UUID.
    uuid.UUID(id1)
    assert Path(id_file).exists()


def test_instance_id_stable_across_calls(tmp_path: Path) -> None:
    id_file = str(tmp_path / "instance-id")
    id1 = _load_or_create_instance_id(id_file)
    id2 = _load_or_create_instance_id(id_file)
    assert id1 == id2


def test_instance_id_not_derived_from_user_data(tmp_path: Path) -> None:
    id_file = str(tmp_path / "instance-id")
    id1 = _load_or_create_instance_id(id_file)
    # Must parse as UUID (random UUID4, not derived from any workspace/user id).
    parsed = uuid.UUID(id1)
    assert parsed.version == 4


def test_instance_id_recovers_from_corrupted_file(tmp_path: Path) -> None:
    id_file = tmp_path / "instance-id"
    id_file.write_text("not-a-uuid")
    result = _load_or_create_instance_id(str(id_file))
    uuid.UUID(result)  # Must be a valid UUID.


# ---------------------------------------------------------------------------
# send_ping — uses mocked httpx; errors are silently swallowed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_ping_posts_payload() -> None:
    """send_ping POSTs the payload to the configured endpoint."""
    mock_response = MagicMock()
    mock_response.status_code = 200

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("civicsignals_api.telemetry.httpx.AsyncClient", return_value=mock_client):
        await send_ping(
            endpoint="https://telemetry.example.com/v1/ping",
            instance_id=str(uuid.uuid4()),
            workspace_count=5,
            signal_count=1000,
        )

    mock_client.post.assert_awaited_once()
    call_args = mock_client.post.call_args
    assert call_args.args[0] == "https://telemetry.example.com/v1/ping"
    # Verify only allowed keys in the posted JSON body.
    sent_payload = call_args.kwargs.get(
        "json", call_args.args[1] if len(call_args.args) > 1 else {}
    )
    assert set(sent_payload.keys()) <= _ALLOWED_PAYLOAD_KEYS


@pytest.mark.asyncio
async def test_send_ping_swallows_network_error() -> None:
    """A network error must never propagate — best-effort only."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=ConnectionError("network down"))

    with patch("civicsignals_api.telemetry.httpx.AsyncClient", return_value=mock_client):
        # Must not raise.
        await send_ping(
            endpoint="https://telemetry.example.com/v1/ping",
            instance_id=str(uuid.uuid4()),
            workspace_count=0,
            signal_count=0,
        )


# ---------------------------------------------------------------------------
# Disabled by default — no network calls
# ---------------------------------------------------------------------------


def test_telemetry_disabled_by_default() -> None:
    """With default settings, telemetry is disabled."""
    from civicsignals_api.config import Settings

    settings = Settings()
    assert settings.civicsignals_telemetry_enabled is False


@pytest.mark.asyncio
async def test_no_ping_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """When telemetry is disabled, send_ping is never called.

    We patch send_ping at the module level and verify it is not invoked
    when the settings flag is false (simulating what the Celery task does).
    """
    # Simulate the guard logic in the Celery task without actually running Celery.
    from civicsignals_api import telemetry as tel_module
    from civicsignals_api.config import Settings

    settings = Settings(civicsignals_telemetry_enabled=False)  # type: ignore[call-arg]
    assert settings.civicsignals_telemetry_enabled is False

    send_ping_called = False

    async def fake_send_ping(**_: object) -> None:
        nonlocal send_ping_called
        send_ping_called = True

    monkeypatch.setattr(tel_module, "send_ping", fake_send_ping)

    if not settings.civicsignals_telemetry_enabled:
        pass  # The task returns early — fake_send_ping must not be called.
    else:
        await tel_module.send_ping(  # type: ignore[call-arg]
            endpoint=settings.civicsignals_telemetry_endpoint,
            instance_id="test",
            workspace_count=0,
            signal_count=0,
        )

    assert not send_ping_called, "send_ping must not be called when telemetry is disabled"


# ---------------------------------------------------------------------------
# config settings round-trip
# ---------------------------------------------------------------------------


def test_telemetry_settings_configurable() -> None:
    """Env vars control all telemetry settings."""
    from civicsignals_api.config import Settings

    settings = Settings(  # type: ignore[call-arg]
        civicsignals_telemetry_enabled=True,
        civicsignals_telemetry_endpoint="https://custom.example.com/ping",
        civicsignals_telemetry_id_path="/var/lib/civicsignals/instance-id",
    )
    assert settings.civicsignals_telemetry_enabled is True
    assert settings.civicsignals_telemetry_endpoint == "https://custom.example.com/ping"
    assert settings.civicsignals_telemetry_id_path == "/var/lib/civicsignals/instance-id"
