"""LLM gateway service interface (doc 06 §7, doc 18 §6.6).

No module reaches a vendor SDK directly — everything goes through the gateway,
which owns model selection per task, per-workspace token accounting, retry with
backoff, prompt versioning, and cost reporting. Pluggable backends: Anthropic,
OpenAI, Ollama. Concrete backends land in TODO E2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LLMResult:
    text: str
    model: str
    input_tokens: int
    output_tokens: int


class LLMBackend(Protocol):
    async def complete(self, *, prompt: str, model: str, max_tokens: int) -> LLMResult: ...


class LLMGateway:
    """Routes a task to a backend + model. Stub for the scaffold (TODO E2)."""

    def __init__(self, backends: dict[str, LLMBackend]) -> None:
        self._backends = backends

    async def complete(
        self,
        *,
        prompt: str,
        provider: str,
        model: str,
        max_tokens: int = 1024,
        workspace_id: str | None = None,
    ) -> LLMResult:
        backend = self._backends[provider]
        # TODO E2: token accounting per workspace, retry/backoff, cost reporting.
        return await backend.complete(prompt=prompt, model=model, max_tokens=max_tokens)
