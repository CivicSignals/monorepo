"""Ollama backend (doc 06 §7).

Talks to a local Ollama server over HTTP via ``httpx`` (a core dependency, so no
optional extra). Self-host friendly: signal extraction can run on a local
Llama/Qwen model with no per-token cost.
"""

from __future__ import annotations

from typing import Any

import httpx

from ..types import LLMResult, PermanentLLMError, TransientLLMError


class OllamaBackend:
    """Backend for a local Ollama server's ``/api/generate`` endpoint."""

    provider = "ollama"

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float = 120.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        # A long-lived client enables connection pooling / keep-alives across
        # calls. Lazily created so constructing the backend needs no event loop;
        # an injected client (e.g. for tests) is used as-is and not owned.
        self._client = client
        self._owns_client = client is None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        """Close the owned client (call on shutdown). No-op for injected ones."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def complete(
        self,
        *,
        prompt: str,
        model: str,
        max_tokens: int,
        temperature: float = 0.0,
        system: str | None = None,
    ) -> LLMResult:
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if system is not None:
            payload["system"] = system
        try:
            response = await self._get_client().post(
                f"{self._base_url}/api/generate", json=payload
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status >= 500 or status == 429:
                raise TransientLLMError(f"ollama transient error: HTTP {status}") from exc
            # 4xx (bad request, model not found) is permanent; wrap so the raw
            # httpx error never crosses the gateway boundary.
            raise PermanentLLMError(f"ollama error: HTTP {status}") from exc
        except httpx.TransportError as exc:
            # TransportError covers timeouts, connection, and protocol errors.
            raise TransientLLMError(f"ollama transport error: {exc}") from exc

        # A misbehaving server may return non-JSON or a wrong shape; wrap that as
        # transient so the raw ValueError/TypeError never crosses the boundary.
        try:
            data = response.json()
            text = str(data.get("response", ""))
            input_tokens = int(data.get("prompt_eval_count", 0) or 0)
            output_tokens = int(data.get("eval_count", 0) or 0)
        except (ValueError, TypeError, AttributeError) as exc:
            raise TransientLLMError(f"ollama returned an unexpected body: {exc}") from exc

        # Ollama prefixes model ids with "ollama/" in accounting so cost is $0.
        return LLMResult(
            text=text,
            model=f"ollama/{model}" if not model.startswith("ollama") else model,
            provider=self.provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
