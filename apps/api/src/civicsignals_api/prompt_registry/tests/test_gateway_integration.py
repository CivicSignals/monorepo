"""Gateway <-> prompt-registry integration tests (TODO E3).

Drive ``LLMGateway.complete(prompt_name=...)`` through the deterministic
FakeBackend the gateway (E2) ships, asserting the resolved prompt is rendered,
routed via its task/model hint, and recorded on the result — without touching a
real provider.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from civicsignals_api.llm_gateway import (
    TASK_CLASSIFY,
    TASK_EXTRACTION,
    FakeBackend,
    LLMBackend,
    LLMError,
    LLMGateway,
    ModelChoice,
    TaskModelPolicy,
)
from civicsignals_api.prompt_registry import PromptRegistry


def _write(dir_: Path, rel: str, content: str) -> None:
    path = dir_ / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def registry(tmp_path: Path) -> PromptRegistry:
    _write(
        tmp_path,
        "greet/v1.md",
        "---\ndescription: v1\ntask: classify\n---\nHello {who} v1.\n",
    )
    _write(
        tmp_path,
        "greet/v2.md",
        "---\ndescription: v2\ntask: classify\n---\nHello {who} v2.\n",
    )
    _write(
        tmp_path,
        "extract/v1.md",
        "---\ndescription: x\ntask: extraction\n"
        "model_hint: anthropic:claude-3-5-sonnet-latest\n"
        "system: 'sys {who}'\n---\nExtract {who}.\n",
    )
    return PromptRegistry(tmp_path)


def _gateway(backend: FakeBackend, registry: PromptRegistry) -> LLMGateway:
    policy = TaskModelPolicy(
        overrides={
            TASK_CLASSIFY: ModelChoice("fake", "haiku"),
            TASK_EXTRACTION: ModelChoice("fake", "sonnet"),
        }
    )
    backends: dict[str, LLMBackend] = {"fake": backend}
    return LLMGateway(backends, policy=policy, prompt_registry=registry)


async def test_prompt_name_resolves_and_renders(registry: PromptRegistry) -> None:
    backend = FakeBackend(responses=["ok"])
    gw = _gateway(backend, registry)
    result = await gw.complete(prompt_name="greet", prompt_vars={"who": "Ada"})
    # The rendered template (latest = v2) was sent to the backend.
    assert backend.calls[0]["prompt"] == "Hello Ada v2."
    # The resolved concrete version is recorded on the result.
    assert result.prompt_name == "greet"
    assert result.prompt_version == "v2"


async def test_explicit_version_pins(registry: PromptRegistry) -> None:
    backend = FakeBackend(responses=["ok"])
    gw = _gateway(backend, registry)
    result = await gw.complete(prompt_name="greet", prompt_version="v1", prompt_vars={"who": "Bo"})
    assert backend.calls[0]["prompt"] == "Hello Bo v1."
    assert result.prompt_version == "v1"


async def test_prompt_task_routes_via_policy(registry: PromptRegistry) -> None:
    backend = FakeBackend(responses=["ok"])
    gw = _gateway(backend, registry)
    # extract/v1 declares task=extraction (+ a system + an advisory model_hint).
    # Routing flows through the policy: extraction -> ("fake", "sonnet").
    result = await gw.complete(prompt_name="extract", prompt_vars={"who": "X"})
    assert result.task == TASK_EXTRACTION
    assert result.model == "sonnet"
    assert backend.calls[0]["system"] == "sys X"
    assert backend.calls[0]["prompt"] == "Extract X."


async def test_model_hint_is_advisory_not_applied(registry: PromptRegistry) -> None:
    # The prompt's model_hint (anthropic:...) is exposed on the Prompt but must
    # NOT bypass the policy — otherwise a self-host Ollama override would be lost.
    prompt = registry.get("extract", "v1")
    assert prompt.model_hint == "anthropic:claude-3-5-sonnet-latest"
    backend = FakeBackend(responses=["ok"])
    gw = _gateway(backend, registry)
    result = await gw.complete(prompt_name="extract", prompt_vars={"who": "X"})
    # Routed by the policy (fake/sonnet), not the hint's anthropic provider.
    assert result.provider == "fake"


async def test_explicit_model_overrides_task_routing(registry: PromptRegistry) -> None:
    backend = FakeBackend(responses=["ok"])
    gw = _gateway(backend, registry)
    result = await gw.complete(
        prompt_name="extract",
        prompt_vars={"who": "X"},
        provider="fake",
        model="forced",
    )
    assert result.model == "forced"


async def test_caller_system_overrides_prompt_system(registry: PromptRegistry) -> None:
    backend = FakeBackend(responses=["ok"])
    gw = _gateway(backend, registry)
    await gw.complete(prompt_name="extract", prompt_vars={"who": "X"}, system="caller system")
    assert backend.calls[0]["system"] == "caller system"


async def test_missing_variable_errors_before_backend(registry: PromptRegistry) -> None:
    from civicsignals_api.prompt_registry import PromptRenderError

    backend = FakeBackend(responses=["ok"])
    gw = _gateway(backend, registry)
    with pytest.raises(PromptRenderError):
        await gw.complete(prompt_name="greet", prompt_vars={})
    # The backend was never called — we fail before spending an LLM call.
    assert backend.calls == []


async def test_raw_prompt_still_works(registry: PromptRegistry) -> None:
    # Backward compatibility: raw prompt= path is unchanged.
    backend = FakeBackend(responses=["ok"])
    gw = _gateway(backend, registry)
    result = await gw.complete(prompt="raw text", task=TASK_CLASSIFY)
    assert backend.calls[0]["prompt"] == "raw text"
    assert result.prompt_name is None


async def test_raw_prompt_with_name_treats_name_as_metadata(
    registry: PromptRegistry,
) -> None:
    # E2 seam compatibility: passing raw prompt + prompt_name uses the raw text
    # verbatim (no registry lookup, no rendering) and records the name/version as
    # metadata on the result.
    backend = FakeBackend(responses=["ok"])
    gw = _gateway(backend, registry)
    result = await gw.complete(
        prompt="raw", task=TASK_CLASSIFY, prompt_name="greet", prompt_version="v9"
    )
    assert backend.calls[0]["prompt"] == "raw"
    assert result.prompt_name == "greet"
    assert result.prompt_version == "v9"


async def test_neither_prompt_nor_name_rejected(registry: PromptRegistry) -> None:
    backend = FakeBackend()
    gw = _gateway(backend, registry)
    with pytest.raises(LLMError, match="requires either"):
        await gw.complete()
    assert backend.calls == []


async def test_gateway_lazily_loads_default_registry() -> None:
    # No registry injected -> the gateway resolves the process-wide singleton,
    # which loads the bundled prompts/ dir on first prompt-registry use.
    backend = FakeBackend(responses=["ok"])
    policy = TaskModelPolicy(overrides={TASK_CLASSIFY: ModelChoice("fake", "haiku")})
    backends: dict[str, LLMBackend] = {"fake": backend}
    gw = LLMGateway(backends, policy=policy)
    result = await gw.complete(
        prompt_name="smart_search_rewrite", prompt_vars={"query": "RFPs in TX"}
    )
    assert "RFPs in TX" in str(backend.calls[0]["prompt"])
    assert result.prompt_name == "smart_search_rewrite"
    assert result.prompt_version == "v1"
