"""Shared types and the backend protocol for the LLM gateway (doc 06 §7).

Kept dependency-free (no vendor SDKs) so every module and backend can import
these without pulling the heavy ``extraction`` extras into the api image.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# Logical task names the gateway routes on. The pipeline (doc 18 §6.6, doc 19)
# picks a task; the gateway maps task -> (provider, model) via TaskModelPolicy.
# Cheap Haiku-class for the relevance gate + classification, Sonnet for the
# higher-stakes extraction/summary passes.
TASK_CLASSIFY = "classify"
TASK_EXTRACTION = "extraction"
TASK_SUMMARY = "summary"
TASK_TRANSLATE = "translate"
# Smart-search NL->structured query rewrite (doc 08 §3.2/§3.3, TODO I2). A small,
# cheap, low-latency call on the user's search box, so it routes to a Haiku-class
# model just like classification.
TASK_SMART_SEARCH_REWRITE = "smart_search_rewrite"
# Signal embedding (I1, doc 19 §4/§7.4): each extracted signal is embedded into the
# ``signals_signal.vector_embedding`` pgvector column for fuzzy dedupe (E10) +
# smart-search / hybrid retrieval (I3). Routes to an embeddings model (OpenAI /
# Ollama), independent of the completion-task models.
TASK_EMBED = "embed"


@dataclass(frozen=True)
class LLMResult:
    """A completed generation plus the accounting metadata the gateway needs."""

    text: str
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    # The logical task this call was routed for (filled in by the gateway).
    task: str | None = None
    # Prompt identity once the registry (TODO E3) lands. Threaded through now so
    # callers can start passing it without a later signature change.
    prompt_name: str | None = None
    prompt_version: str | None = None


@dataclass(frozen=True)
class EmbeddingResult:
    """One batch of text embeddings plus the accounting metadata (I1, doc 19 §7.4).

    ``vectors[i]`` is the embedding for ``texts[i]`` (the gateway preserves order).
    ``dim`` is the per-vector dimension (every vector in the batch shares it) — the
    caller asserts it matches the ``signals_signal.vector_embedding`` column width
    (``signals.models.EMBEDDING_DIM``). ``input_tokens`` is the total tokens billed
    for the batch (embedding endpoints have no output tokens, so ``output_tokens``
    is always 0 — kept for symmetry with :class:`LLMResult` so the same
    :class:`~civicsignals_api.llm_gateway.accounting.TokenAccountant` records both).
    """

    vectors: list[list[float]]
    model: str
    provider: str
    dim: int
    input_tokens: int
    # The logical task this batch was routed for (filled in by the gateway).
    task: str | None = None


class LLMError(Exception):
    """Base class for gateway-raised errors."""


class TransientLLMError(LLMError):
    """A retryable failure (timeout, 429, 5xx). The gateway retries with backoff."""


class PermanentLLMError(LLMError):
    """A non-retryable provider failure (4xx auth/bad-request, invalid model, …).

    Backends wrap vendor SDK errors in this so the rest of the system never has
    to import or catch ``anthropic.*`` / ``openai.*`` exception types directly.
    """


class BackendNotAvailableError(LLMError):
    """A backend's vendor SDK is not installed or is misconfigured.

    Raised lazily when a backend is actually used so the api image — which does
    not install the ``extraction`` extra — can still import the gateway.
    """


@runtime_checkable
class LLMBackend(Protocol):
    """The common async contract every backend implements.

    ``provider`` identifies the backend in :class:`TaskModelPolicy` and in token
    accounting. ``complete`` must raise :class:`TransientLLMError` for failures
    the gateway should retry, and :class:`BackendNotAvailableError` when its
    dependency is missing.
    """

    provider: str

    async def complete(
        self,
        *,
        prompt: str,
        model: str,
        max_tokens: int,
        temperature: float = 0.0,
        system: str | None = None,
    ) -> LLMResult: ...


@runtime_checkable
class LLMEmbeddingBackend(Protocol):
    """The async contract for an embeddings backend (I1, doc 19 §7.4).

    Separate from :class:`LLMBackend` because not every completion provider also
    serves embeddings (Anthropic, e.g., has no first-party embeddings endpoint) and
    the gateway routes the two independently. ``embed`` must return one vector per
    input text, in input order, and raise :class:`TransientLLMError` for retryable
    failures / :class:`BackendNotAvailableError` when its dependency is missing —
    the same taxonomy :meth:`LLMBackend.complete` uses.
    """

    provider: str

    async def embed(
        self,
        *,
        texts: list[str],
        model: str,
    ) -> EmbeddingResult: ...
