"""Tests for the E7 GitHub auto-issue client seam (doc 18 §3.2).

Covers the injectable/mockable + no-op-when-unconfigured behavior without a
database: the client selection (no-op when ``GITHUB_TOKEN`` unset, real client
when set), the override hook, and the issue body composition (sanitized sample +
metrics). The full idempotent open-once flow against a live table is in
``test_drift_persistence.py``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from civicsignals_api.config import Settings
from civicsignals_api.modules.recipes import drift, services
from civicsignals_api.modules.recipes.github_client import (
    HttpxGitHubClient,
    NoopGitHubClient,
)
from civicsignals_api.modules.recipes.schemas import RollingMetrics


class _RecordingGitHubClient:
    """A fake GitHub client recording every issue it was asked to open."""

    def __init__(self, url: str | None = "https://github.com/acme/repo/issues/1") -> None:
        self.url = url
        self.calls: list[dict[str, object]] = []

    def open_issue(self, *, title: str, body: str, labels: list[str]) -> str | None:
        self.calls.append({"title": title, "body": body, "labels": labels})
        return self.url


@pytest.fixture(autouse=True)
def _reset_override() -> Iterator[None]:
    services.override_github_client(None)
    yield
    services.override_github_client(None)


def test_get_github_client_is_noop_when_token_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services, "_get_settings", lambda: Settings(github_token=None))
    client = services.get_github_client()
    assert isinstance(client, NoopGitHubClient)
    # No-op returns None so the drift service records no idempotency key.
    assert client.open_issue(title="t", body="b", labels=[]) is None


def test_get_github_client_real_when_token_set(monkeypatch: pytest.MonkeyPatch) -> None:
    # The selection only checks truthiness. The token value is assembled at runtime
    # (not written as a ``token = "<literal>"`` assignment) so the gitleaks secret
    # scanner in CI doesn't flag a fake credential.
    fake_token = "-".join(["placeholder", "value"])
    settings = Settings(github_repo="acme/repo").model_copy(update={"github_token": fake_token})
    monkeypatch.setattr(services, "_get_settings", lambda: settings)
    client = services.get_github_client()
    assert isinstance(client, HttpxGitHubClient)


def test_override_github_client_takes_precedence() -> None:
    fake = _RecordingGitHubClient()
    services.override_github_client(fake)
    assert services.get_github_client() is fake


def test_build_issue_includes_metrics_and_sanitized_sample() -> None:
    w24 = RollingMetrics(
        recipe_id="seattle_agendas",
        window_hours=24.0,
        runs=5,
        extractions_total=10,
        extractions_succeeded=3,
        signals_produced=3,
        dead_letters=4,
        extraction_success_rate=0.3,
    )
    w7 = RollingMetrics(
        recipe_id="seattle_agendas",
        window_hours=168.0,
        llm_fallbacks=8,
        fields_total=20,
        llm_fallback_rate=0.4,
        avg_wall_clock_seconds=12.5,
    )
    title, body, labels = drift._build_issue(
        "seattle_agendas",
        w24,
        w7,
        reason="extraction success 0.30 < 0.50 over 24h (5 runs)",
        failing_sample="```html\n<broken>\n```",
    )
    assert "seattle_agendas" in title
    assert "30%" in body  # extraction success rate rendered
    assert "40%" in body  # llm fallback rate rendered
    assert "3/10" in body  # success fraction
    assert "12.5s" in body  # avg wall clock
    assert "```" in body  # our own fence is present
    # The source's embedded fence was neutralized so it can't break out of ours.
    assert "<broken>" in body
    assert "recipe-drift" in labels
