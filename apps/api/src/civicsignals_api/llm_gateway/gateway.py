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
from .policy import ModelChoice, TaskModelPolicy
from .types import (
    TASK_CLASSIFY,
    TASK_EMBED,
    EmbeddingResult,
    LLMBackend,
    LLMEmbeddingBackend,
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
        embedding_backends: dict[str, LLMEmbeddingBackend] | None = None,
        embedding_choice: ModelChoice | None = None,
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
        # Embeddings route through a separate backend registry + a single default
        # model choice (there is one embedding model per deployment, unlike the
        # per-task completion models). Empty/None until I1 wires them in
        # ``build_gateway``; ``embed`` raises a clear error if called without one.
        self._embedding_backends = embedding_backends or {}
        self._embedding_choice = embedding_choice

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

        # Adopt the prompt's declared task only when the caller left ``task`` at
        # its default — an explicit ``task=`` from the caller always wins. Task
        # then routes through TaskModelPolicy, so a self-hoster's settings
        # overrides still govern which provider/model actually runs; an explicit
        # ``provider``/``model`` (doc 19 §5.1 escalation) overrides that model
        # independently of the task. The prompt's ``model_hint`` stays advisory
        # (exposed on the Prompt) and never bypasses the policy — applying it here
        # would override a self-host Ollama config.
        if task == TASK_CLASSIFY and resolved.task is not None:
            task = resolved.task

        return rendered, rendered_system, task, provider, model, resolved.version

    # -- embeddings -------------------------------------------------------

    async def embed(
        self,
        texts: list[str],
        *,
        provider: str | None = None,
        model: str | None = None,
        workspace_id: str | None = None,
    ) -> EmbeddingResult:
        """Embed a batch of texts via the configured embeddings backend (I1).

        No module reaches a vendor embeddings SDK directly — like completions, all
        embedding traffic routes here for backend selection + per-workspace token
        accounting (doc 06 §7, doc 19 §7.4). Returns one vector per input text in
        input order. ``provider``/``model`` override the deployment default (must be
        supplied together, mirroring :meth:`complete`'s escalation override).
        Transient errors are retried with backoff; usage is recorded against
        ``workspace_id`` under the :data:`TASK_EMBED` task.

        An empty ``texts`` short-circuits to an empty result (no backend call, no
        accounting) so callers can pass through trivially-empty batches.
        """
        if (provider is None) != (model is None):
            raise ValueError(
                "provider and model overrides must be supplied together "
                f"(got provider={provider!r}, model={model!r})"
            )
        if not texts:
            chosen = self._resolve_embedding_choice(provider, model)
            return EmbeddingResult(
                vectors=[], model=chosen.model, provider=chosen.provider, dim=0, input_tokens=0
            )

        chosen = self._resolve_embedding_choice(provider, model)
        backend = self._embedding_backends.get(chosen.provider)
        if backend is None:
            raise LLMError(
                f"No embedding backend registered for provider {chosen.provider!r}. "
                f"Registered: {sorted(self._embedding_backends)}."
            )

        result = await self._embed_with_retry(backend=backend, texts=texts, model=chosen.model)
        result = EmbeddingResult(
            vectors=result.vectors,
            model=result.model,
            provider=result.provider,
            dim=result.dim,
            input_tokens=result.input_tokens,
            task=TASK_EMBED,
        )

        if workspace_id is not None:
            cost = estimate_cost_usd(result.model, result.input_tokens, 0)
            self._accountant.record(
                workspace_id=workspace_id,
                task=TASK_EMBED,
                input_tokens=result.input_tokens,
                output_tokens=0,
                cost_usd=cost,
            )
        return result

    def _resolve_embedding_choice(self, provider: str | None, model: str | None) -> ModelChoice:
        if provider is not None and model is not None:
            return ModelChoice(provider=provider, model=model)
        if self._embedding_choice is None:
            raise LLMError(
                "No embedding model configured. Set settings.embedding_model / "
                "embedding_provider, or pass provider+model to embed()."
            )
        return self._embedding_choice

    async def _embed_with_retry(
        self,
        *,
        backend: LLMEmbeddingBackend,
        texts: list[str],
        model: str,
    ) -> EmbeddingResult:
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
                        "llm_gateway.embed_retry",
                        provider=backend.provider,
                        model=model,
                        attempt=attempt.retry_state.attempt_number,
                    )
                return await backend.embed(texts=texts, model=model)
        raise AssertionError("unreachable")  # pragma: no cover

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
        A backend registered for both completions and embeddings (the OpenAI /
        Ollama instances are shared) is closed once — dedupe by identity.
        """
        seen: set[int] = set()
        for backend in (*self._backends.values(), *self._embedding_backends.values()):
            if id(backend) in seen:
                continue
            seen.add(id(backend))
            closer = getattr(backend, "aclose", None)
            if closer is not None:
                await closer()
