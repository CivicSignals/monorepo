"""Anthropic backend (doc 06 §7).

Wraps the ``anthropic`` async SDK, which lives in the ``extraction`` optional
extra. The import is lazy: constructing the backend without the SDK installed is
fine, but the first ``complete`` call raises
:class:`~civicsignals_api.llm_gateway.types.BackendNotAvailableError`.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from ..types import (
    BackendNotAvailableError,
    LLMError,
    LLMResult,
    PermanentLLMError,
    TransientLLMError,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from anthropic import AsyncAnthropic


class AnthropicBackend:
    """Anthropic Messages API backend."""

    provider = "anthropic"

    def __init__(self, *, api_key: str | None, base_url: str | None = None) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._client: AsyncAnthropic | None = None

    def _get_client(self) -> AsyncAnthropic:
        if self._client is not None:
            return self._client
        try:
            from anthropic import AsyncAnthropic
        except ModuleNotFoundError as exc:  # pragma: no cover - exercised via guard test
            raise BackendNotAvailableError(
                "AnthropicBackend requires the 'anthropic' package "
                "(install the 'extraction' extra)."
            ) from exc
        if not self._api_key:
            raise BackendNotAvailableError("AnthropicBackend requires ANTHROPIC_API_KEY.")
        kwargs: dict[str, Any] = {"api_key": self._api_key}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        self._client = AsyncAnthropic(**kwargs)
        return self._client

    async def complete(
        self,
        *,
        prompt: str,
        model: str,
        max_tokens: int,
        temperature: float = 0.0,
        system: str | None = None,
    ) -> LLMResult:
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system is not None:
            kwargs["system"] = system
        try:
            message = await client.messages.create(**kwargs)
        except asyncio.CancelledError:
            # Never swallow cancellation — let it propagate.
            raise
        except Exception as exc:
            # Normalize SDK errors to the gateway's transient/permanent taxonomy.
            raise _normalize_error(exc) from exc

        text = "".join(
            block.text for block in message.content if getattr(block, "type", None) == "text"
        )
        usage = message.usage
        return LLMResult(
            text=text,
            model=model,
            provider=self.provider,
            input_tokens=getattr(usage, "input_tokens", 0),
            output_tokens=getattr(usage, "output_tokens", 0),
        )


def _normalize_error(exc: Exception) -> LLMError:
    """Map anthropic SDK errors to the gateway taxonomy.

    Rate limits, timeouts, connection errors, and 5xx become
    :class:`TransientLLMError` (retried); everything else becomes
    :class:`PermanentLLMError`. Either way the vendor exception type never
    crosses the gateway boundary. Inspected by class name to avoid importing the
    SDK's error types eagerly.
    """
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    # These error classes are always transient. APIStatusError is NOT here: it is
    # the base for 4xx (e.g. 400/401/404) which must not be retried — those are
    # decided by status code below (429 / 5xx only).
    transient_names = {
        "APITimeoutError",
        "APIConnectionError",
        "RateLimitError",
        "InternalServerError",
    }
    if name in transient_names or (isinstance(status, int) and (status == 429 or status >= 500)):
        return TransientLLMError(f"anthropic transient error: {name}: {exc}")
    return PermanentLLMError(f"anthropic error: {name}: {exc}")
