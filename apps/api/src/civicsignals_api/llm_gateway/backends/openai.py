"""OpenAI backend (doc 06 §7).

Wraps the ``openai`` async SDK from the ``extraction`` optional extra. Import is
lazy; first use without the SDK raises
:class:`~civicsignals_api.llm_gateway.types.BackendNotAvailableError`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..types import (
    BackendNotAvailableError,
    LLMResult,
    TransientLLMError,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from openai import AsyncOpenAI


class OpenAIBackend:
    """OpenAI Chat Completions backend."""

    provider = "openai"

    def __init__(self, *, api_key: str | None, base_url: str | None = None) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI:
        if self._client is not None:
            return self._client
        try:
            from openai import AsyncOpenAI
        except ModuleNotFoundError as exc:  # pragma: no cover - exercised via guard test
            raise BackendNotAvailableError(
                "OpenAIBackend requires the 'openai' package (install the 'extraction' extra)."
            ) from exc
        if not self._api_key:
            raise BackendNotAvailableError("OpenAIBackend requires OPENAI_API_KEY.")
        kwargs: dict[str, Any] = {"api_key": self._api_key}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        self._client = AsyncOpenAI(**kwargs)
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
        messages: list[dict[str, str]] = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        try:
            response = await client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=messages,
            )
        except Exception as exc:
            # Normalize SDK errors to the gateway's transient/permanent taxonomy.
            raise _normalize_error(exc) from exc

        text = response.choices[0].message.content or ""
        usage = response.usage
        return LLMResult(
            text=text,
            model=model,
            provider=self.provider,
            input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
        )


def _normalize_error(exc: Exception) -> Exception:
    """Map openai SDK errors to the gateway taxonomy (see anthropic backend)."""
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    transient_names = {
        "APITimeoutError",
        "APIConnectionError",
        "RateLimitError",
        "InternalServerError",
        "APIStatusError",
    }
    if name in transient_names or (isinstance(status, int) and status >= 500) or status == 429:
        return TransientLLMError(f"openai transient error: {name}: {exc}")
    return exc
