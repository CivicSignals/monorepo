"""Errors raised by the prompt registry (TODO E3).

Kept separate from the loader/registry so callers (and the LLM gateway) can
import and catch them without pulling in the YAML/IO machinery.
"""

from __future__ import annotations


class PromptRegistryError(Exception):
    """Base class for every prompt-registry error."""


class PromptLoadError(PromptRegistryError):
    """A prompt file on disk is malformed or its metadata is invalid.

    Raised at load time so a bad prompt fails fast (during registry construction)
    rather than at the first render in production.
    """


class PromptNotFoundError(PromptRegistryError):
    """No prompt matches the requested ``(name, version)`` (or name has no versions)."""


class PromptRenderError(PromptRegistryError):
    """Rendering failed — typically a template variable was not supplied."""
