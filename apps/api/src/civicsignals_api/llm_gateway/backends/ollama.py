"""Ollama backend (doc 06 §7).

Talks to a local Ollama server over HTTP via ``httpx`` (a core dependency, so no
optional extra). Self-host friendly: signal extraction can run on a local
Llama/Qwen model with no per-token cost.
"""

from __future__ import annotations

from typing import Any

import httpx

from ..types import LLMResult, TransientLLMError


class OllamaBackend:
    """Backend for a local Ollama server's ``/api/generate`` endpoint."""

    provider = "ollama"

    def __init__(self, *, base_url: str, timeout: float = 120.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

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
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(f"{self._base_url}/api/generate", json=payload)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status >= 500 or status == 429:
                raise TransientLLMError(f"ollama transient error: HTTP {status}") from exc
            raise
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise TransientLLMError(f"ollama transport error: {exc}") from exc

        data = response.json()
        # Ollama prefixes model ids with "ollama/" in accounting so cost is $0.
        return LLMResult(
            text=data.get("response", ""),
            model=f"ollama/{model}" if not model.startswith("ollama") else model,
            provider=self.provider,
            input_tokens=int(data.get("prompt_eval_count", 0)),
            output_tokens=int(data.get("eval_count", 0)),
        )
