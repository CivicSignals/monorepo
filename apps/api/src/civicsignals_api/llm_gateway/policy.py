"""Per-task model/provider selection (doc 06 §7, doc 18 §6.6, doc 19 §11).

A :class:`TaskModelPolicy` maps a logical task name to a ``(provider, model)``
pair. Defaults follow the cost model: cheap Haiku-class for the relevance gate +
classification, Sonnet-class for the higher-stakes extraction/summary passes.
The policy is configurable from settings so self-hosters can point everything at
a local Ollama model.
"""

from __future__ import annotations

from dataclasses import dataclass

from .types import (
    TASK_CLASSIFY,
    TASK_EXTRACTION,
    TASK_SMART_SEARCH_REWRITE,
    TASK_SMART_SEARCH_SUMMARY,
    TASK_SUMMARY,
    TASK_TRANSLATE,
)


@dataclass(frozen=True)
class ModelChoice:
    provider: str
    model: str


# Providers the gateway knows how to route to. Used to disambiguate the
# "provider:model" override syntax from model ids that themselves contain a
# colon (e.g. Ollama tags like ``llama3:latest``).
KNOWN_PROVIDERS: frozenset[str] = frozenset({"anthropic", "openai", "ollama"})


# Sensible defaults per task. Haiku-class for cheap/high-volume work, Sonnet for
# extraction and summarization where quality matters more than per-call cost.
# smart_search_summary uses Haiku (not Sonnet) because it is latency-sensitive
# (user waits) and the synthesis is short — quality/cost balance favours Haiku.
DEFAULT_TASK_MODELS: dict[str, ModelChoice] = {
    # Current Claude 4.x model IDs (the 3.5 family these shipped with is retired —
    # Anthropic returns 404 for claude-3-5-*-latest). Cheap Haiku-class for the
    # relevance gate / rewrites; Sonnet-class for extraction + summarization.
    TASK_CLASSIFY: ModelChoice("anthropic", "claude-haiku-4-5-20251001"),
    TASK_EXTRACTION: ModelChoice("anthropic", "claude-sonnet-4-6"),
    TASK_SUMMARY: ModelChoice("anthropic", "claude-sonnet-4-6"),
    TASK_TRANSLATE: ModelChoice("anthropic", "claude-haiku-4-5-20251001"),
    TASK_SMART_SEARCH_REWRITE: ModelChoice("anthropic", "claude-haiku-4-5-20251001"),
    TASK_SMART_SEARCH_SUMMARY: ModelChoice("anthropic", "claude-haiku-4-5-20251001"),
}


class TaskModelPolicy:
    """Resolves a logical task to a backend provider + model.

    ``overrides`` (typically from settings) take precedence over the built-in
    defaults; ``default_choice`` is used for any task not otherwise mapped.
    """

    def __init__(
        self,
        *,
        overrides: dict[str, ModelChoice] | None = None,
        default_choice: ModelChoice | None = None,
    ) -> None:
        self._models: dict[str, ModelChoice] = dict(DEFAULT_TASK_MODELS)
        if overrides:
            self._models.update(overrides)
        self._default = default_choice or self._models[TASK_CLASSIFY]

    def resolve(
        self,
        task: str,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> ModelChoice:
        """Resolve ``task`` to a :class:`ModelChoice`.

        Explicit ``provider``/``model`` arguments win over the policy so callers
        can force a specific model (e.g. doc 19's confidence-driven escalation
        from Haiku to Sonnet) without editing the policy table. They must be
        supplied together: overriding only ``provider`` would pair it with the
        base task's model (typically from another vendor), so we reject that
        rather than route an invalid provider/model combination.
        """
        if (provider is None) != (model is None):
            raise ValueError(
                "provider and model overrides must be supplied together "
                f"(got provider={provider!r}, model={model!r})"
            )
        base = self._models.get(task, self._default)
        return ModelChoice(
            provider=provider or base.provider,
            model=model or base.model,
        )
