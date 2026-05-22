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


class LLMError(Exception):
    """Base class for gateway-raised errors."""


class TransientLLMError(LLMError):
    """A retryable failure (timeout, 429, 5xx). The gateway retries with backoff."""


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
