"""Deterministic in-memory backend for tests (no network, no API keys).

Lets the gateway, token accounting, and retry logic be exercised without a real
provider. Supports scripted responses and scripted transient failures so tests
can drive the retry/backoff path.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

from ..types import LLMResult, TransientLLMError


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token), enough for accounting tests."""
    return max(1, len(text) // 4)


class FakeBackend:
    """A deterministic :class:`~civicsignals_api.llm_gateway.types.LLMBackend`.

    - ``responses`` (optional): scripted reply texts consumed FIFO; once
      exhausted it echoes the prompt.
    - ``fail_times``: raise :class:`TransientLLMError` this many times before
      succeeding, to exercise retry/backoff.
    """

    provider = "fake"

    def __init__(
        self,
        *,
        provider: str = "fake",
        responses: Iterable[str] | None = None,
        fail_times: int = 0,
    ) -> None:
        self.provider = provider
        self._responses: deque[str] = deque(responses or [])
        self._fail_times = fail_times
        self.calls: list[dict[str, object]] = []

    async def complete(
        self,
        *,
        prompt: str,
        model: str,
        max_tokens: int,
        temperature: float = 0.0,
        system: str | None = None,
    ) -> LLMResult:
        self.calls.append(
            {
                "prompt": prompt,
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system,
            }
        )
        if self._fail_times > 0:
            self._fail_times -= 1
            raise TransientLLMError("fake transient failure")

        text = self._responses.popleft() if self._responses else f"echo: {prompt}"
        return LLMResult(
            text=text,
            model=model,
            provider=self.provider,
            input_tokens=_estimate_tokens(prompt + (system or "")),
            output_tokens=_estimate_tokens(text),
        )
