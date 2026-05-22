"""Deterministic in-memory backends for tests (no network, no API keys).

Lets the gateway, token accounting, and retry logic be exercised without a real
provider. :class:`FakeBackend` covers completions (scripted responses + scripted
transient failures to drive the retry/backoff path); :class:`FakeEmbeddingBackend`
covers embeddings (deterministic, repeatable vectors of a fixed dimension).
"""

from __future__ import annotations

import hashlib
import math
import struct
from collections import deque
from collections.abc import Iterable

from ..types import EmbeddingResult, LLMResult, TransientLLMError


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


def _deterministic_vector(text: str, dim: int) -> list[float]:
    """A repeatable unit-norm vector derived from ``text`` (no model, no network).

    Hashes the text and expands the digest into ``dim`` floats, then L2-normalises
    so cosine similarity behaves like a real embedding (identical text → identical
    vector → similarity 1.0; different text → a different direction). Deterministic
    across processes/runs so similarity assertions in tests are stable.
    """
    # Stretch the digest to cover ``dim`` 4-byte words by re-hashing with a counter.
    raw = bytearray()
    counter = 0
    while len(raw) < dim * 4:
        raw.extend(hashlib.sha256(f"{counter}:{text}".encode()).digest())
        counter += 1
    # Map each 4-byte word to a float in [-1, 1).
    words = struct.unpack_from(f"<{dim}I", bytes(raw), 0)
    vector = [(w / 0xFFFFFFFF) * 2.0 - 1.0 for w in words]
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:  # pragma: no cover - astronomically unlikely
        return [0.0] * dim
    return [v / norm for v in vector]


class FakeEmbeddingBackend:
    """A deterministic :class:`~civicsignals_api.llm_gateway.types.LLMEmbeddingBackend`.

    Produces repeatable, L2-normalised vectors of ``dim`` dimensions straight from a
    hash of each input text — no network, no API key — so embedding, persistence,
    fuzzy-dedupe, and backfill paths can be tested end-to-end. ``fail_times`` raises
    :class:`TransientLLMError` that many times first, to exercise the retry/backoff
    path and the pipeline's best-effort (failure is non-fatal) handling.
    """

    provider = "fake"

    def __init__(
        self,
        *,
        provider: str = "fake",
        dim: int = 1536,
        fail_times: int = 0,
    ) -> None:
        self.provider = provider
        self._dim = dim
        self._fail_times = fail_times
        self.calls: list[dict[str, object]] = []

    async def embed(self, *, texts: list[str], model: str) -> EmbeddingResult:
        self.calls.append({"texts": list(texts), "model": model})
        if self._fail_times > 0:
            self._fail_times -= 1
            raise TransientLLMError("fake transient embedding failure")
        vectors = [_deterministic_vector(t, self._dim) for t in texts]
        return EmbeddingResult(
            vectors=vectors,
            model=model,
            provider=self.provider,
            dim=self._dim,
            input_tokens=sum(_estimate_tokens(t) for t in texts),
        )
