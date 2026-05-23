"""Tests for the env-selectable fake LLM backend (e2e foundation).

Cover:
- ``LLM_BACKEND`` selection in ``build_gateway`` (vendor default vs. fake), with no
  API keys needed in fake mode.
- :class:`FixtureBackend` call-kind detection (relevance / entity / signal) from the
  rendered prompt, and scripted per-scenario / per-task replies (incl. fallbacks for
  unknown scenarios / unscripted kinds).
All network-free.
"""

from __future__ import annotations

import json

from civicsignals_api.config import Settings
from civicsignals_api.llm_gateway import (
    TASK_CLASSIFY,
    TASK_EXTRACTION,
    FakeBackend,
    FixtureBackend,
    build_fake_gateway,
    build_gateway,
)
from civicsignals_api.llm_gateway.backends.fixture import (
    KIND_ENTITY,
    KIND_RELEVANCE,
    KIND_SIGNAL,
    detect_call_kind,
)

# Distinctive phrases from the rendered prompts the pipeline issues.
_RELEVANCE_PROMPT = "Does this document mention or discuss any of the following?"
_ENTITY_PROMPT = "Extract all named entities from the document below. Be inclusive."
_SIGNAL_PROMPT = "Read the document below and extract every buying signal it contains."


# --- backend selection ---------------------------------------------------


def test_default_backend_is_vendor() -> None:
    """The default ``LLM_BACKEND`` preserves today's vendor-backed gateway."""
    settings = Settings(llm_backend="vendor")
    gw = build_gateway(settings)
    # The vendor gateway registers the three real providers.
    assert "anthropic" in gw._backends
    assert "fake" not in gw._backends


def test_fake_backend_selected_without_api_keys() -> None:
    """``LLM_BACKEND=fake`` builds a deterministic gateway with no API key set."""
    settings = Settings(llm_backend="fake")  # no anthropic/openai keys
    gw = build_gateway(settings)
    assert list(gw._backends) == ["fake"]
    # Both the cheap (classify) and expensive (extraction) tasks route to fake.
    assert gw._policy.resolve(TASK_CLASSIFY).provider == "fake"
    assert gw._policy.resolve(TASK_EXTRACTION).provider == "fake"


async def test_fake_gateway_completes_deterministically() -> None:
    """A bare fake gateway (no fixtures) completes without network and echoes."""
    gw = build_fake_gateway(Settings(llm_backend="fake"))
    result = await gw.complete(prompt="hello world", task=TASK_CLASSIFY)
    assert result.provider == "fake"
    assert "hello world" in result.text  # bare FakeBackend echoes the prompt


async def test_fake_gateway_embeds_without_keys() -> None:
    """The fake gateway's embeddings use the deterministic embedding backend."""
    gw = build_fake_gateway(Settings(llm_backend="fake", embedding_dim=8))
    res = await gw.embed(["a", "b"])
    assert res.provider == "fake"
    assert len(res.vectors) == 2
    assert res.dim == 8


# --- call-kind detection --------------------------------------------------


def test_detect_call_kind_relevance() -> None:
    assert detect_call_kind(_RELEVANCE_PROMPT) == KIND_RELEVANCE


def test_detect_call_kind_entity() -> None:
    assert detect_call_kind(_ENTITY_PROMPT) == KIND_ENTITY


def test_detect_call_kind_signal() -> None:
    assert detect_call_kind(_SIGNAL_PROMPT) == KIND_SIGNAL


def test_detect_call_kind_unknown_defaults_to_signal() -> None:
    # An unrecognised completion returns an empty-candidates signal reply, never an echo.
    assert detect_call_kind("a summary please") == KIND_SIGNAL


# --- scripted replies -----------------------------------------------------


def _script() -> dict[str, dict[str, object]]:
    return {
        "MARK-A": {
            "relevance": {"relevant": True, "confidence": 0.9, "categories": []},
            "entity": {"organizations": [{"name": "Org A", "confidence": 0.9}]},
            "signal": {"candidates": [{"signal_type": "rfp_posted", "fields": {}}]},
        }
    }


async def _complete(backend: FixtureBackend, prompt: str) -> dict[str, object]:
    result = await backend.complete(prompt=prompt, model="fake-model", max_tokens=256)
    return json.loads(result.text)  # type: ignore[no-any-return]


async def test_fixture_routes_by_marker_and_kind() -> None:
    backend = FixtureBackend(script=_script())

    relevance = await _complete(backend, f"MARK-A {_RELEVANCE_PROMPT}")
    entity = await _complete(backend, f"MARK-A {_ENTITY_PROMPT}")
    signal = await _complete(backend, f"MARK-A {_SIGNAL_PROMPT}")

    assert relevance["relevant"] is True
    assert entity["organizations"][0]["name"] == "Org A"  # type: ignore[index]
    assert signal["candidates"][0]["signal_type"] == "rfp_posted"  # type: ignore[index]
    # Every call is recorded with its detected kind for routing assertions.
    assert [c["kind"] for c in backend.calls] == [
        KIND_RELEVANCE,
        KIND_ENTITY,
        KIND_SIGNAL,
    ]


async def test_fixture_unknown_scenario_falls_back_to_relevant() -> None:
    """An unknown scenario yields the safe fallbacks (relevant / empty / no candidates)."""
    backend = FixtureBackend(script=_script())

    relevance = await _complete(backend, f"UNKNOWN {_RELEVANCE_PROMPT}")
    signal = await _complete(backend, f"UNKNOWN {_SIGNAL_PROMPT}")

    assert relevance["relevant"] is True  # fail-open default
    assert signal["candidates"] == []  # no scripted signal → empty


async def test_fixture_string_response_passthrough() -> None:
    """An already-serialised JSON string reply is returned verbatim."""
    backend = FixtureBackend(
        script={"MARK-B": {"signal": '{"candidates": [{"signal_type": "news_mention"}]}'}}
    )
    signal = await _complete(backend, f"MARK-B {_SIGNAL_PROMPT}")
    assert signal["candidates"][0]["signal_type"] == "news_mention"  # type: ignore[index]


def test_bare_fake_backend_still_available() -> None:
    """The FIFO :class:`FakeBackend` is still exported (used by existing tests)."""
    be = FakeBackend(responses=["x"])
    assert be.provider == "fake"
