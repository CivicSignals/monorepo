"""Versioned prompt registry package (TODO E3, doc 06 §7, doc 19 §5.3).

Public import path: ``from civicsignals_api.prompt_registry import PromptRegistry``.

The registry loads on-disk, version-pinned prompts from ``apps/api/prompts/`` and
hands the resolved + rendered text to the LLM gateway. No module embeds prompt
text in code; rolling back a prompt is a version-pointer change, not a deploy.
"""

from __future__ import annotations

from functools import lru_cache

from .errors import (
    PromptLoadError,
    PromptNotFoundError,
    PromptRegistryError,
    PromptRenderError,
)
from .registry import Prompt, PromptRegistry

__all__ = [
    "Prompt",
    "PromptLoadError",
    "PromptNotFoundError",
    "PromptRegistry",
    "PromptRegistryError",
    "PromptRenderError",
    "get_prompt_registry",
]


@lru_cache
def get_prompt_registry() -> PromptRegistry:
    """Process-wide singleton registry rooted at the default ``prompts/`` dir.

    Loading is eager (validates every prompt at construction), so the first
    access also acts as a startup self-check.
    """
    return PromptRegistry()
