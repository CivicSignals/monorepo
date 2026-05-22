"""Pluggable LLM backends (doc 06 §7).

Vendor SDKs (``anthropic``, ``openai``) live in the ``extraction`` optional
extra and are imported lazily inside each backend so the api image — which does
not install them — can still import the gateway. A backend used without its
dependency raises :class:`~civicsignals_api.llm_gateway.types.BackendNotAvailableError`.
"""

from __future__ import annotations

from .anthropic import AnthropicBackend
from .fake import FakeBackend, FakeEmbeddingBackend
from .ollama import OllamaBackend
from .openai import OpenAIBackend

__all__ = [
    "AnthropicBackend",
    "FakeBackend",
    "FakeEmbeddingBackend",
    "OllamaBackend",
    "OpenAIBackend",
]
