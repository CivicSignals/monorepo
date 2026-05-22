"""Tests for the LLM gateway (TODO E2).

Cover task routing, token accounting accumulation, retry/backoff behaviour, and
the lazy-import guard — all without network or API keys via the FakeBackend.
"""

from __future__ import annotations

import httpx
import pytest

from civicsignals_api.config import Settings
from civicsignals_api.llm_gateway import (
    TASK_CLASSIFY,
    TASK_EXTRACTION,
    AnthropicBackend,
    BackendNotAvailableError,
    FakeBackend,
    InMemoryTokenAccountant,
    LLMError,
    LLMGateway,
    ModelChoice,
    OllamaBackend,
    OpenAIBackend,
    TaskModelPolicy,
    build_gateway,
    estimate_cost_usd,
)
from civicsignals_api.llm_gateway.types import PermanentLLMError, TransientLLMError


def _gateway(backend: FakeBackend, **kwargs: object) -> LLMGateway:
    policy = TaskModelPolicy(
        overrides={
            TASK_CLASSIFY: ModelChoice("fake", "haiku"),
            TASK_EXTRACTION: ModelChoice("fake", "sonnet"),
        }
    )
    return LLMGateway({"fake": backend}, policy=policy, **kwargs)  # type: ignore[arg-type]


# --- task routing --------------------------------------------------------


async def test_task_routing_selects_policy_model() -> None:
    backend = FakeBackend(responses=["c", "e"])
    gw = _gateway(backend)

    classify = await gw.complete(prompt="p1", task=TASK_CLASSIFY)
    extract = await gw.complete(prompt="p2", task=TASK_EXTRACTION)

    assert classify.model == "haiku"
    assert classify.task == TASK_CLASSIFY
    assert extract.model == "sonnet"
    assert extract.task == TASK_EXTRACTION
    assert [c["model"] for c in backend.calls] == ["haiku", "sonnet"]


async def test_explicit_provider_and_model_override_policy() -> None:
    # provider+model together force a specific backend+model (doc 19 escalation).
    backend = FakeBackend()
    gw = _gateway(backend)
    result = await gw.complete(
        prompt="p", task=TASK_CLASSIFY, provider="fake", model="sonnet-forced"
    )
    assert result.model == "sonnet-forced"
    assert result.provider == "fake"


async def test_default_task_falls_back_to_default_choice() -> None:
    backend = FakeBackend()
    gw = _gateway(backend)
    # An unknown task name resolves to the default choice (classify -> "haiku").
    result = await gw.complete(prompt="p", task="unmapped-task")
    assert result.model == "haiku"
    assert result.task == "unmapped-task"


async def test_provider_only_override_rejected() -> None:
    # Overriding provider without model would pair an OpenAI provider with the
    # base task's Anthropic model — reject rather than route an invalid combo.
    backend = FakeBackend()
    gw = _gateway(backend)
    with pytest.raises(ValueError, match="provider and model overrides"):
        await gw.complete(prompt="p", task=TASK_CLASSIFY, provider="openai")


def test_unmapped_task_follows_overridden_classify() -> None:
    # An override of `classify` (or the default provider) should also govern
    # unmapped tasks via build_gateway's policy wiring.
    settings = Settings(
        llm_default_provider="ollama",
        llm_task_models={"classify": "ollama:llama3"},
    )
    gw = build_gateway(settings)
    assert gw._policy.resolve("totally-unknown") == ModelChoice("ollama", "llama3")  # type: ignore[attr-defined]


def test_max_attempts_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_attempts must be >= 1"):
        LLMGateway({"fake": FakeBackend()}, max_attempts=0)


async def test_unregistered_provider_raises() -> None:
    backend = FakeBackend()
    policy = TaskModelPolicy(overrides={TASK_CLASSIFY: ModelChoice("nope", "m")})
    gw = LLMGateway({"fake": backend}, policy=policy)
    with pytest.raises(LLMError, match="No backend registered"):
        await gw.complete(prompt="p", task=TASK_CLASSIFY)


def test_prompt_seam_threads_through() -> None:
    # The prompt_name/prompt_version (TODO E3 seam) are recorded on the result.
    backend = FakeBackend(responses=["ok"])
    gw = _gateway(backend)
    import asyncio

    result = asyncio.run(
        gw.complete(
            prompt="p",
            task=TASK_CLASSIFY,
            prompt_name="signal_type:rfp_posted",
            prompt_version="v3",
        )
    )
    assert result.prompt_name == "signal_type:rfp_posted"
    assert result.prompt_version == "v3"


# --- token accounting ----------------------------------------------------


async def test_token_accounting_accumulates_per_workspace() -> None:
    backend = FakeBackend(responses=["aaaaaaaa", "bbbbbbbb", "cccc"])
    gw = _gateway(backend)

    await gw.complete(prompt="x" * 40, task=TASK_CLASSIFY, workspace_id="ws-1")
    await gw.complete(prompt="y" * 40, task=TASK_EXTRACTION, workspace_id="ws-1")
    await gw.complete(prompt="z" * 8, task=TASK_CLASSIFY, workspace_id="ws-2")

    ws1 = gw.usage("ws-1")
    ws2 = gw.usage("ws-2")

    assert ws1.calls == 2
    assert ws1.input_tokens > 0
    assert ws1.output_tokens > 0
    assert ws1.total_tokens == ws1.input_tokens + ws1.output_tokens
    assert ws1.cost_usd > 0
    # Per-task breakdown is tracked (int counts, float cost).
    assert set(ws1.by_task) == {TASK_CLASSIFY, TASK_EXTRACTION}
    assert ws1.by_task[TASK_CLASSIFY].calls == 1
    assert isinstance(ws1.by_task[TASK_CLASSIFY].input_tokens, int)
    assert isinstance(ws1.by_task[TASK_CLASSIFY].cost_usd, float)

    assert ws2.calls == 1
    # Workspaces are isolated.
    assert ws2.input_tokens != ws1.input_tokens


async def test_no_workspace_id_skips_accounting() -> None:
    backend = FakeBackend()
    gw = _gateway(backend)
    await gw.complete(prompt="p", task=TASK_CLASSIFY)
    assert gw.usage("anything").calls == 0


async def test_reset_usage() -> None:
    backend = FakeBackend(responses=["a", "b"])
    gw = _gateway(backend)
    await gw.complete(prompt="p", task=TASK_CLASSIFY, workspace_id="ws-1")
    await gw.complete(prompt="p", task=TASK_CLASSIFY, workspace_id="ws-2")

    gw.reset_usage("ws-1")
    assert gw.usage("ws-1").calls == 0
    assert gw.usage("ws-2").calls == 1

    gw.reset_usage()
    assert gw.usage("ws-2").calls == 0


def test_estimate_cost_haiku_cheaper_than_sonnet() -> None:
    haiku = estimate_cost_usd("claude-3-5-haiku-latest", 1000, 1000)
    sonnet = estimate_cost_usd("claude-3-5-sonnet-latest", 1000, 1000)
    assert 0 < haiku < sonnet


def test_estimate_cost_local_models_free() -> None:
    assert estimate_cost_usd("ollama/llama3", 1000, 1000) == 0.0
    assert estimate_cost_usd("qwen/qwen2", 1000, 1000) == 0.0


def test_in_memory_accountant_returns_independent_copy() -> None:
    acc = InMemoryTokenAccountant()
    acc.record(workspace_id="w", task="classify", input_tokens=10, output_tokens=5, cost_usd=0.1)
    snapshot = acc.usage("w")
    snapshot.input_tokens = 999  # mutate the copy
    assert acc.usage("w").input_tokens == 10


# --- retry / backoff -----------------------------------------------------


async def test_retry_recovers_after_transient_failures() -> None:
    backend = FakeBackend(responses=["recovered"], fail_times=2)
    # max_attempts=3 -> 2 failures + 1 success.
    gw = _gateway(backend, max_attempts=3)
    result = await gw.complete(prompt="p", task=TASK_CLASSIFY)
    assert result.text == "recovered"
    assert len(backend.calls) == 3


async def test_retry_exhausts_and_reraises() -> None:
    backend = FakeBackend(fail_times=5)
    gw = _gateway(backend, max_attempts=3)
    with pytest.raises(TransientLLMError):
        await gw.complete(prompt="p", task=TASK_CLASSIFY)
    assert len(backend.calls) == 3


async def test_failed_call_not_accounted() -> None:
    backend = FakeBackend(fail_times=5)
    gw = _gateway(backend, max_attempts=2)
    with pytest.raises(TransientLLMError):
        await gw.complete(prompt="p", task=TASK_CLASSIFY, workspace_id="ws")
    assert gw.usage("ws").calls == 0


# --- lazy-import guard ---------------------------------------------------


async def test_anthropic_backend_lazy_guard_missing_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "anthropic":
            raise ModuleNotFoundError("No module named 'anthropic'")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", fake_import)
    backend = AnthropicBackend(api_key="sk-test")
    with pytest.raises(BackendNotAvailableError, match="anthropic"):
        await backend.complete(prompt="p", model="claude", max_tokens=10)


async def test_openai_backend_lazy_guard_missing_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "openai":
            raise ModuleNotFoundError("No module named 'openai'")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", fake_import)
    backend = OpenAIBackend(api_key="sk-test")
    with pytest.raises(BackendNotAvailableError, match="openai"):
        await backend.complete(prompt="p", model="gpt", max_tokens=10)


async def test_anthropic_backend_requires_api_key() -> None:
    # The lazy guard also fires when the key is unset (only if the SDK imports).
    pytest.importorskip("anthropic")
    backend = AnthropicBackend(api_key=None)
    with pytest.raises(BackendNotAvailableError, match="ANTHROPIC_API_KEY"):
        await backend.complete(prompt="p", model="claude", max_tokens=10)


# --- error normalization (no vendor exceptions cross the boundary) -------


class _FakeStatusError(Exception):
    """Mimics an SDK error carrying an HTTP status_code attribute."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


@pytest.mark.parametrize("module_name", ["anthropic", "openai"])
def test_normalize_error_taxonomy(module_name: str) -> None:
    from importlib import import_module

    normalize = import_module(
        f"civicsignals_api.llm_gateway.backends.{module_name}"
    )._normalize_error

    # 5xx and 429 are transient and retryable.
    assert isinstance(normalize(_FakeStatusError(503)), TransientLLMError)
    assert isinstance(normalize(_FakeStatusError(429)), TransientLLMError)
    # 4xx is permanent — and crucially NOT transient (must not be retried).
    permanent = normalize(_FakeStatusError(400))
    assert isinstance(permanent, PermanentLLMError)
    assert not isinstance(permanent, TransientLLMError)
    assert isinstance(normalize(_FakeStatusError(401)), PermanentLLMError)
    # An arbitrary non-status error is wrapped as permanent, not leaked raw.
    assert isinstance(normalize(ValueError("boom")), PermanentLLMError)


# --- settings-driven factory --------------------------------------------


def test_build_gateway_registers_all_providers() -> None:
    gw = build_gateway(Settings())
    # All three real providers are registered (lazily importing their SDKs).
    assert set(gw._backends) == {"anthropic", "openai", "ollama"}  # type: ignore[attr-defined]


def test_build_gateway_applies_task_model_overrides() -> None:
    settings = Settings(
        llm_default_provider="ollama",
        llm_task_models={"classify": "llama3", "extraction": "openai:gpt-4o"},
    )
    gw = build_gateway(settings)
    choice_classify = gw._policy.resolve("classify")  # type: ignore[attr-defined]
    choice_extract = gw._policy.resolve("extraction")  # type: ignore[attr-defined]
    assert choice_classify == ModelChoice("ollama", "llama3")
    assert choice_extract == ModelChoice("openai", "gpt-4o")


def test_override_with_colon_in_model_id_is_not_split_as_provider() -> None:
    # "llama3:latest" is an Ollama tag, not a provider:model — the whole string
    # must stay the model id and use the default provider.
    settings = Settings(
        llm_default_provider="ollama",
        llm_task_models={"classify": "llama3:latest"},
    )
    gw = build_gateway(settings)
    assert gw._policy.resolve("classify") == ModelChoice("ollama", "llama3:latest")  # type: ignore[attr-defined]


def test_override_with_known_provider_prefix_is_split() -> None:
    settings = Settings(llm_task_models={"classify": "anthropic:claude-3-5-haiku-latest"})
    gw = build_gateway(settings)
    assert gw._policy.resolve("classify") == ModelChoice(  # type: ignore[attr-defined]
        "anthropic", "claude-3-5-haiku-latest"
    )


# --- ollama backend client lifecycle ------------------------------------


async def test_ollama_reuses_long_lived_client() -> None:
    backend = OllamaBackend(base_url="http://localhost:11434")
    # No client until first use; the same client is reused across calls.
    assert backend._client is None  # type: ignore[attr-defined]
    c1 = backend._get_client()  # type: ignore[attr-defined]
    c2 = backend._get_client()  # type: ignore[attr-defined]
    assert c1 is c2
    await backend.aclose()
    assert backend._client is None  # type: ignore[attr-defined]


async def test_ollama_does_not_close_injected_client() -> None:
    injected = httpx.AsyncClient()
    backend = OllamaBackend(base_url="http://localhost:11434", client=injected)
    assert backend._get_client() is injected  # type: ignore[attr-defined]
    await backend.aclose()  # no-op for injected clients
    assert not injected.is_closed
    await injected.aclose()
