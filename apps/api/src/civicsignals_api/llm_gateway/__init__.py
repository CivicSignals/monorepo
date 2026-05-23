"""LLM gateway package (doc 06 §7, doc 18 §6.6).

No module reaches a vendor SDK directly — everything goes through the gateway,
which owns model selection per task, per-workspace token accounting, retry with
backoff, prompt versioning, and cost reporting. Pluggable backends: Anthropic,
OpenAI, Ollama (plus a deterministic FakeBackend for tests).

Public import path is unchanged: ``from civicsignals_api.llm_gateway import LLMGateway``.
"""

from __future__ import annotations

from functools import lru_cache

from ..config import Settings, get_settings
from .accounting import (
    InMemoryTokenAccountant,
    TaskUsage,
    TokenAccountant,
    UsageCounters,
    estimate_cost_usd,
)
from .backends import (
    AnthropicBackend,
    FakeBackend,
    FakeEmbeddingBackend,
    FixtureBackend,
    OllamaBackend,
    OpenAIBackend,
)
from .gateway import LLMGateway
from .policy import DEFAULT_TASK_MODELS, KNOWN_PROVIDERS, ModelChoice, TaskModelPolicy
from .types import (
    TASK_CLASSIFY,
    TASK_EMBED,
    TASK_EXTRACTION,
    TASK_SMART_SEARCH_REWRITE,
    TASK_SMART_SEARCH_SUMMARY,
    TASK_SUMMARY,
    TASK_TRANSLATE,
    BackendNotAvailableError,
    EmbeddingResult,
    LLMBackend,
    LLMEmbeddingBackend,
    LLMError,
    LLMResult,
    PermanentLLMError,
    TransientLLMError,
)

__all__ = [
    "DEFAULT_TASK_MODELS",
    "KNOWN_PROVIDERS",
    "TASK_CLASSIFY",
    "TASK_EMBED",
    "TASK_EXTRACTION",
    "TASK_SMART_SEARCH_REWRITE",
    "TASK_SMART_SEARCH_SUMMARY",
    "TASK_SUMMARY",
    "TASK_TRANSLATE",
    "AnthropicBackend",
    "BackendNotAvailableError",
    "EmbeddingResult",
    "FakeBackend",
    "FakeEmbeddingBackend",
    "FixtureBackend",
    "InMemoryTokenAccountant",
    "LLMBackend",
    "LLMEmbeddingBackend",
    "LLMError",
    "LLMGateway",
    "LLMResult",
    "ModelChoice",
    "OllamaBackend",
    "OpenAIBackend",
    "PermanentLLMError",
    "TaskModelPolicy",
    "TaskUsage",
    "TokenAccountant",
    "TransientLLMError",
    "UsageCounters",
    "build_gateway",
    "estimate_cost_usd",
    "get_gateway",
]


def _policy_from_settings(settings: Settings) -> TaskModelPolicy:
    """Build the task→model policy from settings overrides."""
    overrides: dict[str, ModelChoice] = {}
    default_provider = settings.llm_default_provider
    for task, raw in settings.llm_task_models.items():
        # Each override is "provider:model" or just "model" (uses default
        # provider). Only treat a colon prefix as a provider when it actually
        # names a known one — otherwise it's part of the model id (e.g. the
        # Ollama tag "llama3:latest"), so keep the whole string as the model.
        prefix, sep, rest = raw.partition(":")
        if sep and prefix in KNOWN_PROVIDERS:
            provider, model = prefix, rest
        else:
            provider, model = default_provider, raw
        overrides[task] = ModelChoice(provider=provider, model=model)
    # default_choice=None lets TaskModelPolicy derive its fallback from the
    # *merged* models, so an overridden `classify` (or a changed default
    # provider) also governs unmapped tasks.
    return TaskModelPolicy(overrides=overrides)


def _embedding_choice_from_settings(settings: Settings) -> ModelChoice:
    """Resolve the deployment's embeddings (provider, model) from settings (I1).

    ``embedding_model`` is "provider:model" (a known provider prefix splits it) or
    just "model" (uses ``embedding_provider``). Mirrors ``_policy_from_settings``'s
    colon handling so an Ollama tag like ``nomic-embed-text:latest`` keeps its colon
    rather than being mistaken for a provider prefix.
    """
    raw = settings.embedding_model
    prefix, sep, rest = raw.partition(":")
    if sep and prefix in KNOWN_PROVIDERS:
        return ModelChoice(provider=prefix, model=rest)
    return ModelChoice(provider=settings.embedding_provider, model=raw)


_FAKE_PROVIDER = "fake"


def _load_fake_script(path: str) -> dict[str, dict[str, object]]:
    """Load the merged scenario→responses script for the fixture backend.

    The file is the e2e manifest's per-scenario ``llm-responses.json`` files merged
    into one mapping (``seed_e2e`` writes it / passes the path). A missing or
    unreadable file degrades to an empty script — the :class:`FixtureBackend` then
    falls back to its safe defaults rather than crashing process boot.
    """
    import json

    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    # Keep only the well-formed ``marker -> {kind: response}`` entries.
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def build_fake_gateway(settings: Settings) -> LLMGateway:
    """Construct a deterministic, network-free gateway (``LLM_BACKEND=fake``).

    Every completion task routes to a single deterministic backend registered under
    the ``fake`` provider — a :class:`FixtureBackend` replaying the scripted
    per-scenario / per-task responses at ``settings.llm_fake_fixtures`` when set, or a
    bare :class:`FakeBackend` (prompt echo) otherwise. The policy maps **all** known
    completion tasks to ``(fake, <task's default model id>)`` so a caller's
    task→model expectations (and the recorded ``result.model``) stay realistic while
    the actual generation is local. Embeddings use the deterministic
    :class:`FakeEmbeddingBackend` so the I1 embed step needs no embeddings API key.

    This is what lets a running api/worker — and ``seed_e2e`` — boot with no API key
    and produce reproducible signals (doc 06 §7: all LLM access goes through here).
    """
    completion_backend: LLMBackend
    if settings.llm_fake_fixtures:
        completion_backend = FixtureBackend(
            script=_load_fake_script(settings.llm_fake_fixtures), provider=_FAKE_PROVIDER
        )
    else:
        completion_backend = FakeBackend(provider=_FAKE_PROVIDER)

    # Route every task to the fake provider, preserving each task's default model id
    # so the per-task model the policy reports is still meaningful in logs/tests.
    overrides = {
        task: ModelChoice(provider=_FAKE_PROVIDER, model=choice.model)
        for task, choice in DEFAULT_TASK_MODELS.items()
    }
    policy = TaskModelPolicy(
        overrides=overrides,
        default_choice=ModelChoice(provider=_FAKE_PROVIDER, model="fake-model"),
    )
    fake_embed = FakeEmbeddingBackend(provider=_FAKE_PROVIDER, dim=settings.embedding_dim)
    return LLMGateway(
        {_FAKE_PROVIDER: completion_backend},
        policy=policy,
        accountant=InMemoryTokenAccountant(),
        max_attempts=settings.llm_max_attempts,
        embedding_backends={_FAKE_PROVIDER: fake_embed},
        embedding_choice=ModelChoice(provider=_FAKE_PROVIDER, model="fake-embed-model"),
    )


def build_gateway(settings: Settings | None = None) -> LLMGateway:
    """Construct an :class:`LLMGateway` wired from settings.

    When ``settings.llm_backend == "fake"`` a deterministic, network-free gateway is
    built (:func:`build_fake_gateway`) so the e2e stack / seed boots without API keys.
    Otherwise (the default) the real provider backends are registered; their vendor
    SDKs are imported lazily on first use, so registering an Anthropic/OpenAI backend
    in the api image (which lacks the ``extraction`` extra) is harmless until it is
    actually called.
    """
    settings = settings or get_settings()
    if settings.llm_backend == "fake":
        return build_fake_gateway(settings)
    openai_backend = OpenAIBackend(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
    )
    ollama_backend = OllamaBackend(base_url=settings.ollama_base_url)
    backends: dict[str, LLMBackend] = {
        "anthropic": AnthropicBackend(
            api_key=settings.anthropic_api_key,
            base_url=settings.anthropic_base_url,
        ),
        "openai": openai_backend,
        "ollama": ollama_backend,
    }
    # Embeddings (I1): OpenAI + Ollama serve embeddings (Anthropic has no
    # first-party embeddings endpoint). The same backend instances are shared with
    # the completion registry so their vendor clients (and ``aclose``) are reused.
    embedding_backends: dict[str, LLMEmbeddingBackend] = {
        "openai": openai_backend,
        "ollama": ollama_backend,
    }
    return LLMGateway(
        backends,
        policy=_policy_from_settings(settings),
        accountant=InMemoryTokenAccountant(),
        max_attempts=settings.llm_max_attempts,
        embedding_backends=embedding_backends,
        embedding_choice=_embedding_choice_from_settings(settings),
    )


@lru_cache
def get_gateway() -> LLMGateway:
    """Process-wide singleton gateway (shares the in-process accountant)."""
    return build_gateway()
