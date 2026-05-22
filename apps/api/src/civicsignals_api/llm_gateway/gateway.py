"""The LLM gateway (doc 06 §7, doc 18 §6.6).

No module reaches a vendor SDK directly — everything goes through this gateway,
which owns:

- model/provider selection per logical task (:class:`TaskModelPolicy`),
- per-workspace token + cost accounting (:class:`TokenAccountant`),
- retry with backoff on transient errors (``tenacity``),
- prompt versioning via the injected :class:`PromptRegistry` (TODO E3) — the
  ``prompt_name``/``prompt_version`` arguments resolve + render an on-disk,
  version-pinned prompt instead of (or in addition to) raw text.

The cache of identical ``(prompt_version, raw_document_hash)`` extractions
(doc 18 §6.6) and persistent usage metering (TODO N3) attach behind this class
without changing the call sites.
"""

from __future__ import annotations

import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# The gateway depends on the registry, not the other way round — no import cycle.
from ..prompt_registry import PromptRegistry
from .accounting import (
    InMemoryTokenAccountant,
    TokenAccountant,
    UsageCounters,
    estimate_cost_usd,
)
from .policy import TaskModelPolicy
from .types import (
    TASK_CLASSIFY,
    LLMBackend,
    LLMError,
    LLMResult,
    TransientLLMError,
)

log = structlog.get_logger(__name__)

DEFAULT_MAX_ATTEMPTS = 3


class LLMGateway:
    """Routes a logical task to a backend + model, with accounting and retries."""

    def __init__(
        self,
        backends: dict[str, LLMBackend],
        *,
        policy: TaskModelPolicy | None = None,
        accountant: TokenAccountant | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        prompt_registry: PromptRegistry | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")
        self._backends = backends
        self._policy = policy or TaskModelPolicy()
        self._accountant: TokenAccountant = accountant or InMemoryTokenAccountant()
        self._max_attempts = max_attempts
        # Injected so it is overridable in tests; lazily resolved to the
        # process-wide singleton on first prompt-registry use (avoids loading the
        # prompts dir for raw ``prompt=`` callers).
        self._prompt_registry = prompt_registry

    def _registry(self) -> PromptRegistry:
        if self._prompt_registry is None:
            from ..prompt_registry import get_prompt_registry

            self._prompt_registry = get_prompt_registry()
        return self._prompt_registry

    async def complete(
        self,
        *,
        prompt: str | None = None,
        task: str = TASK_CLASSIFY,
        provider: str | None = None,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        system: str | None = None,
        workspace_id: str | None = None,
        # Prompt registry (TODO E3): pass ``prompt_name`` (+ optional
        # ``prompt_version``, default "latest") to resolve and render a
        # version-pinned on-disk prompt instead of raw ``prompt`` text.
        # ``prompt_vars`` fills the template's ``{variable}`` placeholders.
        prompt_name: str | None = None,
        prompt_version: str | None = None,
        prompt_vars: dict[str, str] | None = None,
    ) -> LLMResult:
        """Run a completion, routing ``task`` to a backend + model via the policy.

        Pass raw ``prompt`` text (backward compatible) and/or a ``prompt_name``:

        - ``prompt`` set: used verbatim; ``prompt_name``/``prompt_version`` are
          recorded as metadata only (the E2 seam — no registry lookup).
        - ``prompt`` unset + ``prompt_name`` set: the registry resolves and
          renders the version-pinned prompt (``prompt_vars`` fills its
          ``{variable}`` placeholders), and its declared ``task`` and ``system``
          are applied. The prompt's ``model_hint`` stays advisory (read it off
          the resolved :class:`Prompt`) and never bypasses the policy.

        Explicit ``provider``/``model`` override the policy (e.g. confidence-driven
        Haiku→Sonnet escalation, doc 19 §5.1). Transient errors are retried with
        exponential backoff. Usage is recorded against ``workspace_id``. The
        resolved prompt identity (with the concrete ``vN``) is recorded on the
        result.
        """
        prompt, system, task, provider, model, prompt_version = self._resolve_prompt(
            prompt=prompt,
            system=system,
            task=task,
            provider=provider,
            model=model,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            prompt_vars=prompt_vars,
        )

        choice = self._policy.resolve(task, provider=provider, model=model)
        backend = self._backends.get(choice.provider)
        if backend is None:
            raise LLMError(
                f"No backend registered for provider {choice.provider!r} "
                f"(task={task!r}). Registered: {sorted(self._backends)}."
            )

        result = await self._complete_with_retry(
            backend=backend,
            prompt=prompt,
            model=choice.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
        )

        result = LLMResult(
            text=result.text,
            model=result.model,
            provider=result.provider,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            task=task,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
        )

        if workspace_id is not None:
            cost = estimate_cost_usd(result.model, result.input_tokens, result.output_tokens)
            self._accountant.record(
                workspace_id=workspace_id,
                task=task,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cost_usd=cost,
            )
        return result

    def _resolve_prompt(
        self,
        *,
        prompt: str | None,
        system: str | None,
        task: str,
        provider: str | None,
        model: str | None,
        prompt_name: str | None,
        prompt_version: str | None,
        prompt_vars: dict[str, str] | None,
    ) -> tuple[str, str | None, str, str | None, str | None, str | None]:
        """Resolve raw vs. registry prompt into the final call parameters.

        Returns ``(prompt, system, task, provider, model, prompt_version)``.

        - Raw ``prompt`` given: used verbatim; any ``prompt_name`` /
          ``prompt_version`` are recorded as metadata only (the E2 seam — no
          registry lookup, no rendering). This keeps E2 callers working.
        - No ``prompt`` but a ``prompt_name``: resolve + render from the registry.
          The rendered body becomes ``prompt``, the prompt's system text fills
          ``system`` (unless the caller passed one), and the resolved concrete
          version is reported back so the result records ``vN`` even when the
          caller asked for "latest".
        """
        if prompt is not None:
            # Raw-text path (backward compatible): prompt_name/version are
            # pass-through metadata only.
            return prompt, system, task, provider, model, prompt_version
        if prompt_name is None:
            raise LLMError("complete() requires either 'prompt' or 'prompt_name'")

        resolved = self._registry().get(prompt_name, prompt_version)
        rendered = resolved.render(prompt_vars)
        rendered_system = system if system is not None else resolved.render_system(prompt_vars)

        # The prompt's declared task drives model routing only when the caller
        # left task at the default and gave no explicit provider/model — an
        # explicit caller choice always wins (doc 19 §5.1 escalation). Routing
        # then flows through TaskModelPolicy, so a self-hoster's settings
        # overrides still govern which provider/model actually runs. The prompt's
        # ``model_hint`` stays advisory (exposed on the Prompt) and never bypasses
        # the policy — applying it here would override a self-host Ollama config.
        if task == TASK_CLASSIFY and resolved.task is not None:
            task = resolved.task

        return rendered, rendered_system, task, provider, model, resolved.version

    async def _complete_with_retry(
        self,
        *,
        backend: LLMBackend,
        prompt: str,
        model: str,
        max_tokens: int,
        temperature: float,
        system: str | None,
    ) -> LLMResult:
        retrying = AsyncRetrying(
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
            retry=retry_if_exception_type(TransientLLMError),
            reraise=True,
        )
        async for attempt in retrying:
            with attempt:
                if attempt.retry_state.attempt_number > 1:
                    log.warning(
                        "llm_gateway.retry",
                        provider=backend.provider,
                        model=model,
                        attempt=attempt.retry_state.attempt_number,
                    )
                return await backend.complete(
                    prompt=prompt,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system,
                )
        # AsyncRetrying with reraise=True either returns or raises; unreachable.
        raise AssertionError("unreachable")  # pragma: no cover

    # -- token accounting -------------------------------------------------

    def usage(self, workspace_id: str) -> UsageCounters:
        """Read accumulated usage for a workspace (TODO N3 persists this)."""
        return self._accountant.usage(workspace_id)

    def reset_usage(self, workspace_id: str | None = None) -> None:
        """Reset accumulated usage for one workspace, or all if ``None``."""
        self._accountant.reset(workspace_id)

    # -- lifecycle --------------------------------------------------------

    async def aclose(self) -> None:
        """Close any backends holding resources (e.g. the Ollama http client).

        Call on process/app shutdown. Backends without an ``aclose`` are skipped.
        """
        for backend in self._backends.values():
            closer = getattr(backend, "aclose", None)
            if closer is not None:
                await closer()
