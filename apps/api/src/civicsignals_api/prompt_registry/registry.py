"""Versioned, on-disk prompt registry (doc 06 §7, doc 18 §3.5, doc 19 §5.3).

The LLM gateway never embeds prompt text in code. Prompts live as files under a
prompts directory and are referenced by ``(name, version)``; a bad prompt is
rolled back by changing the version pointer, not by a code deploy. Because a
prompt change is a reproducible-state change (doc 19 §5.3), prompts are
content-addressable by their commit — the registry just loads what is on disk.

Layout
------
::

    prompts/
      <name>/
        v1.md
        v2.md

``<name>`` is the prompt name (it may contain ``/`` for grouping, e.g.
``signal_type/rfp_posted``). Each file is **Markdown with a YAML frontmatter
block**::

    ---
    description: Cheap relevance gate (doc 19 §3).
    task: classify          # logical gateway task -> model routing
    model_hint: anthropic:claude-3-5-haiku-latest   # optional advisory model
    variables: [cleaned_text]                       # declared template vars
    ---
    You are a classifier ... {cleaned_text} ...

The body is the template; ``{variable}`` placeholders are filled by
:meth:`PromptRegistry.render`. A ``system:`` key in the frontmatter (or a
separate ``system`` block) provides the system prompt. Everything is validated
at load time so malformed prompts fail fast.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import (
    PromptLoadError,
    PromptNotFoundError,
    PromptRenderError,
)

# Default prompts directory: ``apps/api/prompts`` relative to the package root.
# ``__file__`` is ``.../src/civicsignals_api/prompt_registry/registry.py``; the
# prompts dir sits beside ``src`` at the api project root.
_DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"

# A version file is ``v<N>.md`` (N a positive integer). The integer drives the
# "latest" resolver, so v10 sorts after v2 (unlike a lexical sort of the names).
_VERSION_FILE_RE = re.compile(r"^v(?P<num>\d+)\.md$")

# Frontmatter delimiter: a line of exactly ``---``.
_FRONTMATTER_RE = re.compile(r"^---\s*\n(?P<meta>.*?)\n---\s*\n(?P<body>.*)\Z", re.DOTALL)

# Recognised frontmatter keys; anything else fails validation so typos in
# ``descripton`` or ``modelhint`` are caught at load time rather than ignored.
_ALLOWED_META_KEYS = frozenset({"description", "task", "model_hint", "system", "variables"})


class _StrictFormatter(string.Formatter):
    """A ``str.format`` variant that only allows simple named ``{var}`` fields.

    Rejects positional fields (``{}``/``{0}``), attribute/index access
    (``{a.b}``/``{a[0]}``) and conversions/format-specs, so a prompt template
    can never reach into a passed object or silently drop a value. Missing keys
    raise (surfaced as :class:`PromptRenderError`).
    """

    def get_field(self, field_name: str, args: Any, kwargs: Any) -> tuple[Any, str]:
        if not field_name or not field_name.isidentifier():
            raise PromptRenderError(
                f"unsupported template field {field_name!r}: only simple "
                "{name} placeholders are allowed"
            )
        result: tuple[Any, str] = super().get_field(field_name, args, kwargs)
        return result


_FORMATTER = _StrictFormatter()


@dataclass(frozen=True)
class Prompt:
    """A single resolved prompt version, loaded from one file.

    ``template`` is the body text (with ``{variable}`` placeholders);
    ``render`` substitutes them. ``model_hint`` / ``task`` advise the gateway's
    model routing but never override an explicit caller choice.
    """

    name: str
    version: str  # e.g. "v1"
    template: str
    description: str = ""
    task: str | None = None
    model_hint: str | None = None
    system: str | None = None
    # Declared variables (from frontmatter ``variables:``) plus any discovered
    # in the template/system text. Used to validate render() inputs.
    variables: frozenset[str] = field(default_factory=frozenset)
    path: Path | None = None

    @property
    def version_number(self) -> int:
        """The integer version (``v3`` -> ``3``)."""
        return int(self.version[1:])

    def render(self, variables: dict[str, str] | None = None) -> str:
        """Render the template body, substituting ``{name}`` placeholders.

        Every placeholder in the template must be supplied; an unknown key
        raises :class:`PromptRenderError`. Extra keys are ignored (a caller may
        share one variable dict across prompts).
        """
        return self._render(self.template, variables, what="template")

    def render_system(self, variables: dict[str, str] | None = None) -> str | None:
        """Render the system prompt (if any), substituting placeholders."""
        if self.system is None:
            return None
        return self._render(self.system, variables, what="system")

    def _render(self, text: str, variables: dict[str, str] | None, *, what: str) -> str:
        values = variables or {}
        try:
            return _FORMATTER.vformat(text, (), values)
        except PromptRenderError:
            raise
        except KeyError as exc:
            missing = exc.args[0] if exc.args else "?"
            raise PromptRenderError(
                f"prompt {self.name}/{self.version} {what} is missing variable "
                f"{missing!r}; supplied: {sorted(values)}"
            ) from exc
        except (IndexError, ValueError) as exc:
            raise PromptRenderError(
                f"prompt {self.name}/{self.version} {what} failed to render: {exc}"
            ) from exc


def _extract_placeholders(text: str) -> set[str]:
    """Collect the ``{name}`` placeholder names used in ``text``.

    Reuses the format parser so escaped braces (``{{`` / ``}}``) are handled the
    same way rendering will handle them. Raises :class:`PromptLoadError` on a
    syntactically broken template (e.g. an unmatched brace) so it is caught at
    load time.
    """
    names: set[str] = set()
    try:
        for _literal, field_name, _spec, _conv in string.Formatter().parse(text):
            if field_name is None:
                continue
            if not field_name.isidentifier():
                raise PromptLoadError(
                    f"unsupported template field {field_name!r}: only simple "
                    "{name} placeholders are allowed"
                )
            names.add(field_name)
    except ValueError as exc:
        raise PromptLoadError(f"malformed template: {exc}") from exc
    return names


def _load_prompt_file(name: str, version: str, path: Path) -> Prompt:
    """Parse and validate a single ``v<N>.md`` prompt file. Fails fast."""
    raw = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(raw)
    if match is None:
        raise PromptLoadError(
            f"prompt {path} must start with a YAML frontmatter block delimited by "
            "'---' lines, followed by the template body"
        )

    try:
        meta_obj = yaml.safe_load(match.group("meta")) or {}
    except yaml.YAMLError as exc:
        raise PromptLoadError(f"prompt {path}: invalid YAML frontmatter: {exc}") from exc
    if not isinstance(meta_obj, dict):
        raise PromptLoadError(f"prompt {path}: frontmatter must be a YAML mapping")

    unknown = set(meta_obj) - _ALLOWED_META_KEYS
    if unknown:
        raise PromptLoadError(
            f"prompt {path}: unknown frontmatter keys {sorted(unknown)}; "
            f"allowed: {sorted(_ALLOWED_META_KEYS)}"
        )

    body = match.group("body").strip()
    if not body:
        raise PromptLoadError(f"prompt {path}: template body is empty")

    system = meta_obj.get("system")
    if system is not None and not isinstance(system, str):
        raise PromptLoadError(f"prompt {path}: 'system' must be a string")

    declared = meta_obj.get("variables")
    if declared is not None and (
        not isinstance(declared, list) or not all(isinstance(v, str) for v in declared)
    ):
        raise PromptLoadError(f"prompt {path}: 'variables' must be a list of strings")
    declared_set: set[str] = set(declared or [])

    description = meta_obj.get("description", "")
    if not isinstance(description, str):
        raise PromptLoadError(f"prompt {path}: 'description' must be a string")
    task = meta_obj.get("task")
    if task is not None and not isinstance(task, str):
        raise PromptLoadError(f"prompt {path}: 'task' must be a string")
    model_hint = meta_obj.get("model_hint")
    if model_hint is not None and not isinstance(model_hint, str):
        raise PromptLoadError(f"prompt {path}: 'model_hint' must be a string")

    used = _extract_placeholders(body) | _extract_placeholders(system or "")
    # If the author declared variables, the template must not reference any that
    # were not declared — catches drift between the declared contract and body.
    if declared_set and not used <= declared_set:
        raise PromptLoadError(
            f"prompt {path}: template uses undeclared variables "
            f"{sorted(used - declared_set)} (declared: {sorted(declared_set)})"
        )

    return Prompt(
        name=name,
        version=version,
        template=body,
        description=description,
        task=task,
        model_hint=model_hint,
        system=system,
        variables=frozenset(declared_set | used),
        path=path,
    )


class PromptRegistry:
    """Loads versioned prompts from disk and resolves them by ``(name, version)``.

    Construction eagerly scans ``base_dir`` and validates every prompt file, so a
    malformed prompt is surfaced immediately (fail fast) rather than at first use.
    The registry is read-only after construction.
    """

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self._base_dir = Path(base_dir) if base_dir is not None else _DEFAULT_PROMPTS_DIR
        # name -> {version_str -> Prompt}
        self._prompts: dict[str, dict[str, Prompt]] = {}
        self._load_all()

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def _load_all(self) -> None:
        if not self._base_dir.is_dir():
            raise PromptLoadError(f"prompts directory {self._base_dir} does not exist")
        # A prompt name is the directory path relative to base_dir; allow nested
        # grouping (e.g. ``signal_type/rfp_posted``). Only dirs that directly
        # contain ``v<N>.md`` files are treated as prompts.
        for version_path in sorted(self._base_dir.rglob("*.md")):
            file_match = _VERSION_FILE_RE.match(version_path.name)
            if file_match is None:
                continue  # README.md and other docs are ignored.
            name = version_path.parent.relative_to(self._base_dir).as_posix()
            if name == ".":
                raise PromptLoadError(
                    f"prompt file {version_path} must live in a <name>/ subdirectory"
                )
            version = f"v{int(file_match.group('num'))}"
            prompt = _load_prompt_file(name, version, version_path)
            versions = self._prompts.setdefault(name, {})
            if version in versions:
                raise PromptLoadError(
                    f"duplicate version {version} for prompt {name!r} "
                    f"({version_path} and {versions[version].path})"
                )
            versions[version] = prompt

    # -- lookup -----------------------------------------------------------

    def names(self) -> list[str]:
        """All known prompt names, sorted."""
        return sorted(self._prompts)

    def versions(self, name: str) -> list[str]:
        """Known versions for ``name``, ordered oldest -> newest (v1, v2, …)."""
        versions = self._prompts.get(name)
        if not versions:
            raise PromptNotFoundError(f"no prompt named {name!r}")
        return [p.version for p in sorted(versions.values(), key=lambda p: p.version_number)]

    def get(self, name: str, version: str | None = None) -> Prompt:
        """Resolve a prompt by name and version.

        ``version`` accepts ``"v3"`` or ``"3"`` or ``3``. When omitted (or
        ``"latest"``), the highest version number wins. Unknown name/version
        raises :class:`PromptNotFoundError`.
        """
        versions = self._prompts.get(name)
        if not versions:
            raise PromptNotFoundError(f"no prompt named {name!r}; known: {self.names()}")
        if version is None or version == "latest":
            return max(versions.values(), key=lambda p: p.version_number)
        key = self._normalize_version(name, version)
        try:
            return versions[key]
        except KeyError:
            raise PromptNotFoundError(
                f"prompt {name!r} has no version {key!r}; known: {self.versions(name)}"
            ) from None

    @staticmethod
    def _normalize_version(name: str, version: str) -> str:
        text = version.strip()
        digits = text[1:] if text[:1] in {"v", "V"} else text
        if not digits.isdigit() or int(digits) < 1:
            raise PromptNotFoundError(
                f"invalid version {version!r} for prompt {name!r}; "
                "expected 'latest', 'vN', or a positive integer"
            )
        return f"v{int(digits)}"

    # -- render -----------------------------------------------------------

    def render(
        self,
        name: str,
        version: str | None = None,
        variables: dict[str, str] | None = None,
    ) -> Prompt:
        """Resolve a prompt and validate that ``variables`` cover its placeholders.

        Returns the resolved :class:`Prompt`; call :meth:`Prompt.render` /
        :meth:`Prompt.render_system` on it to get the substituted text. Raising
        here (rather than only at render time) lets callers fail before any LLM
        call is made.
        """
        prompt = self.get(name, version)
        # Surface the missing-variable error eagerly.
        prompt.render(variables)
        prompt.render_system(variables)
        return prompt
