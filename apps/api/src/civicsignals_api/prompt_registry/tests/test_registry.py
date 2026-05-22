"""Unit tests for the on-disk prompt registry (TODO E3).

Cover loading + fail-fast validation, version lookup + the latest resolver,
strict rendering (incl. the missing-variable error), and the bundled starter
prompts. No network, no LLM — pure file IO.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from civicsignals_api.prompt_registry import (
    PromptLoadError,
    PromptNotFoundError,
    PromptRegistry,
    PromptRenderError,
    get_prompt_registry,
)


def _write(dir_: Path, rel: str, content: str) -> Path:
    path = dir_ / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# A minimal valid prompt body factory.
def _prompt(*, body: str = "Hello {name}.", meta: str = "description: hi") -> str:
    return f"---\n{meta}\n---\n{body}\n"


# --- loading + validation ------------------------------------------------


def test_load_and_lookup(tmp_path: Path) -> None:
    _write(tmp_path, "greet/v1.md", _prompt(body="Hi {name}."))
    reg = PromptRegistry(tmp_path)
    assert reg.names() == ["greet"]
    assert reg.versions("greet") == ["v1"]
    prompt = reg.get("greet", "v1")
    assert prompt.name == "greet"
    assert prompt.version == "v1"
    assert prompt.version_number == 1
    assert prompt.render({"name": "Ada"}) == "Hi Ada."


def test_nested_prompt_name(tmp_path: Path) -> None:
    _write(tmp_path, "signal_type/rfp_posted/v1.md", _prompt(body="rfp {x}"))
    reg = PromptRegistry(tmp_path)
    assert reg.names() == ["signal_type/rfp_posted"]
    assert reg.get("signal_type/rfp_posted").render({"x": "y"}) == "rfp y"


def test_missing_dir_fails_fast(tmp_path: Path) -> None:
    with pytest.raises(PromptLoadError, match="does not exist"):
        PromptRegistry(tmp_path / "nope")


def test_missing_frontmatter_fails(tmp_path: Path) -> None:
    _write(tmp_path, "bad/v1.md", "just a body, no frontmatter")
    with pytest.raises(PromptLoadError, match="frontmatter"):
        PromptRegistry(tmp_path)


def test_invalid_yaml_frontmatter_fails(tmp_path: Path) -> None:
    _write(tmp_path, "bad/v1.md", "---\n: : not yaml :\n---\nbody {x}")
    with pytest.raises(PromptLoadError, match=r"invalid YAML|must be a YAML mapping"):
        PromptRegistry(tmp_path)


def test_unknown_frontmatter_key_fails(tmp_path: Path) -> None:
    _write(tmp_path, "bad/v1.md", _prompt(meta="descripton: typo"))
    with pytest.raises(PromptLoadError, match="unknown frontmatter keys"):
        PromptRegistry(tmp_path)


def test_empty_body_fails(tmp_path: Path) -> None:
    _write(tmp_path, "bad/v1.md", "---\ndescription: x\n---\n   \n")
    with pytest.raises(PromptLoadError, match="template body is empty"):
        PromptRegistry(tmp_path)


def test_undeclared_variable_fails(tmp_path: Path) -> None:
    _write(tmp_path, "bad/v1.md", _prompt(body="uses {extra}", meta="variables: [declared]"))
    with pytest.raises(PromptLoadError, match="undeclared variables"):
        PromptRegistry(tmp_path)


def test_declared_superset_is_allowed(tmp_path: Path) -> None:
    # Declaring more variables than the body uses is fine.
    _write(tmp_path, "ok/v1.md", _prompt(body="uses {a}", meta="variables: [a, b]"))
    reg = PromptRegistry(tmp_path)
    assert reg.get("ok").variables == frozenset({"a", "b"})


def test_empty_declared_variables_forbids_placeholders(tmp_path: Path) -> None:
    # `variables: []` means "no placeholders allowed" — gate on key presence.
    _write(tmp_path, "bad/v1.md", _prompt(body="uses {x}", meta="variables: []"))
    with pytest.raises(PromptLoadError, match="undeclared variables"):
        PromptRegistry(tmp_path)


def test_empty_declared_variables_with_static_body_ok(tmp_path: Path) -> None:
    _write(tmp_path, "ok/v1.md", _prompt(body="no placeholders here", meta="variables: []"))
    reg = PromptRegistry(tmp_path)
    assert reg.get("ok").render() == "no placeholders here"


def test_malformed_template_brace_fails(tmp_path: Path) -> None:
    _write(tmp_path, "bad/v1.md", _prompt(body="unmatched {"))
    with pytest.raises(PromptLoadError, match="malformed template"):
        PromptRegistry(tmp_path)


def test_positional_field_rejected_at_load(tmp_path: Path) -> None:
    _write(tmp_path, "bad/v1.md", _prompt(body="positional {0}"))
    with pytest.raises(PromptLoadError, match="only simple"):
        PromptRegistry(tmp_path)


def test_conversion_field_rejected_at_load(tmp_path: Path) -> None:
    # {x!r} is a conversion — rejected so rendering stays plain substitution.
    _write(tmp_path, "bad/v1.md", _prompt(body="convert {x!r}"))
    with pytest.raises(PromptLoadError, match="conversion"):
        PromptRegistry(tmp_path)


def test_format_spec_rejected_at_load(tmp_path: Path) -> None:
    # A nested format-spec variable ({x:{w}}) would otherwise smuggle in `w`.
    _write(tmp_path, "bad/v1.md", _prompt(body="spec {x:{w}}"))
    with pytest.raises(PromptLoadError, match="format spec"):
        PromptRegistry(tmp_path)


def test_file_outside_name_dir_fails(tmp_path: Path) -> None:
    # A v<N>.md sitting directly in the prompts root has no name.
    _write(tmp_path, "v1.md", _prompt())
    with pytest.raises(PromptLoadError, match="must live in a"):
        PromptRegistry(tmp_path)


def test_non_version_files_ignored(tmp_path: Path) -> None:
    _write(tmp_path, "greet/v1.md", _prompt(body="hi {name}"))
    _write(tmp_path, "README.md", "# docs")
    _write(tmp_path, "greet/notes.md", "scratch notes, not a version")
    reg = PromptRegistry(tmp_path)
    assert reg.names() == ["greet"]


# --- version resolution --------------------------------------------------


def test_latest_resolver_uses_highest_integer(tmp_path: Path) -> None:
    for n in (1, 2, 10):
        _write(tmp_path, f"p/v{n}.md", _prompt(body=f"version {n} {{x}}"))
    reg = PromptRegistry(tmp_path)
    # Ordered oldest -> newest; v10 sorts after v2 (integer, not lexical).
    assert reg.versions("p") == ["v1", "v2", "v10"]
    assert reg.get("p").version == "v10"
    assert reg.get("p", "latest").version == "v10"


def test_get_accepts_various_version_spellings(tmp_path: Path) -> None:
    _write(tmp_path, "p/v3.md", _prompt(body="three {x}"))
    reg = PromptRegistry(tmp_path)
    assert reg.get("p", "v3").version == "v3"
    assert reg.get("p", "3").version == "v3"
    # An int version is accepted too (matches the documented contract).
    assert reg.get("p", 3).version == "v3"


def test_unknown_name_raises(tmp_path: Path) -> None:
    _write(tmp_path, "p/v1.md", _prompt(body="{x}"))
    reg = PromptRegistry(tmp_path)
    with pytest.raises(PromptNotFoundError, match="no prompt named"):
        reg.get("missing")


def test_unknown_version_raises(tmp_path: Path) -> None:
    _write(tmp_path, "p/v1.md", _prompt(body="{x}"))
    reg = PromptRegistry(tmp_path)
    with pytest.raises(PromptNotFoundError, match="no version"):
        reg.get("p", "v2")


def test_invalid_version_spelling_raises(tmp_path: Path) -> None:
    _write(tmp_path, "p/v1.md", _prompt(body="{x}"))
    reg = PromptRegistry(tmp_path)
    with pytest.raises(PromptNotFoundError, match="invalid version"):
        reg.get("p", "draft")
    with pytest.raises(PromptNotFoundError, match="invalid version"):
        reg.get("p", "v0")


# --- rendering -----------------------------------------------------------


def test_render_missing_variable_errors(tmp_path: Path) -> None:
    _write(tmp_path, "p/v1.md", _prompt(body="needs {a} and {b}"))
    reg = PromptRegistry(tmp_path)
    prompt = reg.get("p")
    with pytest.raises(PromptRenderError, match="missing variable 'b'"):
        prompt.render({"a": "1"})


def test_render_extra_variables_ignored(tmp_path: Path) -> None:
    _write(tmp_path, "p/v1.md", _prompt(body="just {a}"))
    reg = PromptRegistry(tmp_path)
    assert reg.get("p").render({"a": "x", "unused": "y"}) == "just x"


def test_render_escaped_braces(tmp_path: Path) -> None:
    _write(tmp_path, "p/v1.md", _prompt(body='json {{"k": {v}}}'))
    reg = PromptRegistry(tmp_path)
    assert reg.get("p").render({"v": "1"}) == 'json {"k": 1}'


def test_render_system_prompt(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "p/v1.md",
        "---\ndescription: x\nsystem: 'sys for {who}'\n---\nbody {who}\n",
    )
    reg = PromptRegistry(tmp_path)
    prompt = reg.get("p")
    assert prompt.render({"who": "ada"}) == "body ada"
    assert prompt.render_system({"who": "ada"}) == "sys for ada"


def test_render_system_none_when_absent(tmp_path: Path) -> None:
    _write(tmp_path, "p/v1.md", _prompt(body="b {x}"))
    assert PromptRegistry(tmp_path).get("p").render_system({"x": "1"}) is None


def test_registry_render_validates_eagerly(tmp_path: Path) -> None:
    _write(tmp_path, "p/v1.md", _prompt(body="needs {a}"))
    reg = PromptRegistry(tmp_path)
    # render() resolves AND checks variables, returning the Prompt.
    prompt = reg.render("p", "v1", {"a": "x"})
    assert prompt.name == "p"
    with pytest.raises(PromptRenderError):
        reg.render("p", "v1", {})


# --- bundled starter prompts ---------------------------------------------


def test_default_registry_loads_bundled_prompts() -> None:
    reg = get_prompt_registry()
    names = set(reg.names())
    assert {"relevance_classifier", "entity_extraction", "smart_search_rewrite"} <= names


def test_relevance_classifier_renders() -> None:
    reg = get_prompt_registry()
    prompt = reg.get("relevance_classifier", "v1")
    assert prompt.task == "classify"
    assert prompt.model_hint == "anthropic:claude-3-5-haiku-latest"
    rendered = prompt.render({"cleaned_text": "Austin ISD posts RFP for SIS."})
    assert "Austin ISD posts RFP for SIS." in rendered
    # Escaped JSON braces survive rendering as single braces.
    assert '{"relevant"' in rendered


def test_entity_extraction_hints_sonnet() -> None:
    prompt = get_prompt_registry().get("entity_extraction", "v1")
    assert prompt.task == "extraction"
    assert prompt.model_hint == "anthropic:claude-3-5-sonnet-latest"
    rendered = prompt.render({"document_text": "doc", "partial_entities": "{}"})
    assert "doc" in rendered


def test_smart_search_rewrite_renders() -> None:
    prompt = get_prompt_registry().get("smart_search_rewrite", "v1")
    assert prompt.task == "classify"
    assert "RFPs in Texas" in prompt.render({"query": "RFPs in Texas"})
