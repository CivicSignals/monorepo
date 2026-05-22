"""The LLM gateway (doc 06 §7, doc 18 §6.6).

No module reaches a vendor SDK directly — everything goes through this gateway,
which owns:

- model/provider selection per logical task (:class:`TaskModelPolicy`),
- per-workspace token + cost accounting (:class:`TokenAccountant`),
- retry with backoff on transient errors (``tenacity``),
- prompt versioning (the ``prompt_name``/``prompt_version`` seam; TODO E3).

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
    ) -> None:
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")
        self._backends = backends
        self._policy = policy or TaskModelPolicy()
        self._accountant: TokenAccountant = accountant or InMemoryTokenAccountant()
        self._max_attempts = max_attempts

    async def complete(
        self,
        *,
        prompt: str,
        task: str = TASK_CLASSIFY,
        provider: str | None = None,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        system: str | None = None,
        workspace_id: str | None = None,
        # Prompt-registry seam (TODO E3): the prompt identity is threaded through
        # and recorded on the result; the registry will resolve name+version to
        # the actual prompt/system text upstream of this call.
        prompt_name: str | None = None,
        prompt_version: str | None = None,
    ) -> LLMResult:
        """Run a completion, routing ``task`` to a backend + model via the policy.

        Explicit ``provider``/``model`` override the policy (e.g. confidence-driven
        Haiku→Sonnet escalation, doc 19 §5.1). Transient errors are retried with
        exponential backoff. Usage is recorded against ``workspace_id``.
        """
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
