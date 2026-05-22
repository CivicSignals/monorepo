"""Tests for the recipe authoring tooling (TODO D5).

Covers the CLI subcommands (validate / test / preview / scaffold), the preview
service (HTML + URL modes, required-field miss, robots/posture), and the
scaffold helper. The example recipe (``wa-state-webs``) is the live target.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from civicsignals_api.modules.recipes import preview as preview_mod
from civicsignals_api.modules.recipes import scaffold as scaffold_mod
from civicsignals_api.modules.recipes import services
from civicsignals_api.modules.recipes.cli import main as cli_main
from civicsignals_api.modules.recipes.runner import RecipeError, RobotsDisallowedError

VALID_RECIPE_YAML = """\
recipe_id: temp-recipe
connector: http_static
version: 2
entity:
  name: Temp Agency
  state: WA
  kind: state_agency
fields:
  title:
    selectors:
      - "h1.title"
    required: true
  due_date:
    attr: datetime
    selectors:
      - ".due time"
    required: false
signal_types:
  - rfp_posted
"""

LISTING_HTML = """
<html><body><main>
  <h1 class="title">RFP 7 — Bridge Repaint</h1>
  <p class="due"><time datetime="2026-03-01">Mar 1, 2026</time></p>
</main></body></html>
"""


class _StaticFetcher:
    """In-memory fetcher serving canned body + optional robots.txt."""

    def __init__(self, body: str, *, robots: str | None = None) -> None:
        self.body = body
        self.robots = robots

    def fetch(
        self, url: str, *, user_agent: str, max_redirects: int
    ) -> tuple[int, str, dict[str, str]]:
        return 200, self.body, {"content-type": "text/html"}

    def robots_txt(self, url: str, *, user_agent: str) -> str | None:
        return self.robots


# ---------------------------------------------------------------------------
# preview service — HTML mode
# ---------------------------------------------------------------------------


def test_preview_html_extracts_fields() -> None:
    recipe = services.parse_recipe_yaml(VALID_RECIPE_YAML)
    result = preview_mod.preview_html(recipe, LISTING_HTML)
    assert result.ok is True
    assert result.degraded is False
    assert result.recipe_id == "temp-recipe"
    assert result.recipe_version == 2
    assert result.signal_types == ["rfp_posted"]
    by_name = {f.name: f for f in result.fields}
    assert by_name["title"].value == "RFP 7 — Bridge Repaint"
    assert by_name["title"].matched is True
    assert by_name["title"].required is True
    assert by_name["due_date"].value == "2026-03-01"
    assert by_name["due_date"].attr == "datetime"
    # A canonical record is produced for a successful extraction.
    assert len(result.records) == 1
    assert result.records[0].fields["title"] == "RFP 7 — Bridge Repaint"


def test_preview_html_missing_required_is_captured_not_raised() -> None:
    recipe = services.parse_recipe_yaml(VALID_RECIPE_YAML)
    result = preview_mod.preview_html(recipe, "<html><body><p>nope</p></body></html>")
    assert result.ok is False
    assert result.error is not None
    assert "title" in result.error
    by_name = {f.name: f for f in result.fields}
    assert by_name["title"].matched is False
    assert by_name["title"].missing_required is True
    assert result.records == []  # no record on a failed extraction


def test_preview_html_optional_field_missing() -> None:
    recipe = services.parse_recipe_yaml(VALID_RECIPE_YAML)
    result = preview_mod.preview_html(
        recipe, '<html><body><h1 class="title">Only a title</h1></body></html>'
    )
    assert result.ok is True
    by_name = {f.name: f for f in result.fields}
    assert by_name["title"].value == "Only a title"
    assert by_name["due_date"].matched is False
    assert by_name["due_date"].missing_required is False


# ---------------------------------------------------------------------------
# preview service — URL mode (robots + politeness via the runner)
# ---------------------------------------------------------------------------


def test_preview_url_fetches_then_extracts() -> None:
    recipe = services.parse_recipe_yaml(VALID_RECIPE_YAML)
    fetcher = _StaticFetcher(LISTING_HTML)
    result = preview_mod.preview_url(recipe, "https://example.gov/rfp", fetcher)
    assert result.ok is True
    assert result.source == "https://example.gov/rfp"
    by_name = {f.name: f for f in result.fields}
    assert by_name["title"].value == "RFP 7 — Bridge Repaint"


def test_preview_url_honors_robots() -> None:
    recipe = services.parse_recipe_yaml(VALID_RECIPE_YAML)
    fetcher = _StaticFetcher(LISTING_HTML, robots="User-agent: *\nDisallow: /private/\n")
    with pytest.raises(RobotsDisallowedError):
        preview_mod.preview_url(recipe, "https://example.gov/private/rfp", fetcher)


# ---------------------------------------------------------------------------
# preview service — request resolution (id XOR yaml, html XOR url)
# ---------------------------------------------------------------------------


def test_preview_recipe_inline_yaml_html() -> None:
    request = services.PreviewRequest(recipe_yaml=VALID_RECIPE_YAML, html=LISTING_HTML)
    result = services.preview_recipe(request)
    assert result.ok is True


def test_preview_recipe_by_id_html() -> None:
    request = services.PreviewRequest(recipe_id="wa-state-webs", html=_example_fixture_html())
    result = services.preview_recipe(request)
    assert result.ok is True
    by_name = {f.name: f for f in result.fields}
    assert by_name["title"].value == "RFP 2025-014 — District-Wide Network Refresh"


def test_preview_recipe_url_uses_injected_fetcher() -> None:
    request = services.PreviewRequest(recipe_yaml=VALID_RECIPE_YAML, url="https://example.gov/x")
    result = services.preview_recipe(request, fetcher=_StaticFetcher(LISTING_HTML))
    assert result.ok is True


def test_preview_recipe_requires_one_recipe_source() -> None:
    with pytest.raises(RecipeError):
        services.preview_recipe(services.PreviewRequest(html=LISTING_HTML))
    with pytest.raises(RecipeError):
        services.preview_recipe(
            services.PreviewRequest(recipe_id="x", recipe_yaml=VALID_RECIPE_YAML, html=LISTING_HTML)
        )


def test_preview_recipe_requires_one_input() -> None:
    with pytest.raises(RecipeError):
        services.preview_recipe(services.PreviewRequest(recipe_yaml=VALID_RECIPE_YAML))
    with pytest.raises(RecipeError):
        services.preview_recipe(
            services.PreviewRequest(
                recipe_yaml=VALID_RECIPE_YAML, html=LISTING_HTML, url="https://x"
            )
        )


def test_preview_recipe_bad_yaml_raises() -> None:
    with pytest.raises(RecipeError):
        services.preview_recipe(
            services.PreviewRequest(recipe_yaml="not: [valid", html=LISTING_HTML)
        )


# ---------------------------------------------------------------------------
# scaffold
# ---------------------------------------------------------------------------


def test_render_recipe_is_schema_valid() -> None:
    text = scaffold_mod.render_recipe("my-source")
    recipe = services.parse_recipe_yaml(text)
    assert recipe.recipe_id == "my-source"
    assert recipe.connector == "http_static"
    assert recipe.version == 1


def test_render_recipe_custom_connector() -> None:
    text = scaffold_mod.render_recipe("my-source", connector="rss")
    assert services.parse_recipe_yaml(text).connector == "rss"


def test_render_recipe_invalid_id_rejected() -> None:
    with pytest.raises(RecipeError):
        scaffold_mod.render_recipe("Not Valid Id!")


def test_scaffold_writes_recipe(tmp_path: Path) -> None:
    path = scaffold_mod.scaffold_recipe("temp-source", base_dir=tmp_path)
    assert path == tmp_path / "temp-source" / "recipe.yml"
    assert path.exists()
    assert (tmp_path / "temp-source" / "fixtures").is_dir()
    # The written recipe round-trips through validation.
    assert services.load_recipe_file(path).recipe_id == "temp-source"


def test_scaffold_refuses_overwrite(tmp_path: Path) -> None:
    scaffold_mod.scaffold_recipe("temp-source", base_dir=tmp_path)
    with pytest.raises(RecipeError):
        scaffold_mod.scaffold_recipe("temp-source", base_dir=tmp_path)
    # ...unless overwrite is explicit.
    scaffold_mod.scaffold_recipe("temp-source", base_dir=tmp_path, overwrite=True)


# ---------------------------------------------------------------------------
# CLI subcommands
# ---------------------------------------------------------------------------


def test_cli_validate_ok(capsys: pytest.CaptureFixture[str]) -> None:
    path = str(_example_recipe_path())
    assert cli_main(["validate", path]) == 0
    assert "OK" in capsys.readouterr().out


def test_cli_validate_invalid(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.yml"
    bad.write_text(
        "recipe_id: bad\nconnector: http_static\n", encoding="utf-8"
    )  # no version/entity
    assert cli_main(["validate", str(bad)]) == 1
    assert "INVALID" in capsys.readouterr().err


def test_cli_test_one_recipe(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["test", "wa-state-webs"]) == 0
    assert "PASS" in capsys.readouterr().out


def test_cli_test_backward_compat_no_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    # Bare invocation (D1 behavior) replays every recipe's fixtures.
    assert cli_main([]) == 0
    assert "all fixtures passed" in capsys.readouterr().out


def test_cli_preview_html_ok(capsys: pytest.CaptureFixture[str]) -> None:
    fixture = str(_example_fixture_path())
    rc = cli_main(["preview", "--recipe", "wa-state-webs", "--html", fixture])
    assert rc == 0
    out = capsys.readouterr().out
    assert "OK wa-state-webs" in out
    assert "title" in out


def test_cli_preview_html_json(capsys: pytest.CaptureFixture[str]) -> None:
    fixture = str(_example_fixture_path())
    rc = cli_main(["preview", "--recipe", "wa-state-webs", "--html", fixture, "--json"])
    assert rc == 0
    import json

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["recipe_id"] == "wa-state-webs"


def test_cli_preview_missing_required_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    html = tmp_path / "empty.html"
    html.write_text("<html><body><p>nothing</p></body></html>", encoding="utf-8")
    rc = cli_main(["preview", "--recipe", "wa-state-webs", "--html", str(html)])
    assert rc == 1
    assert "MISSING REQUIRED" in capsys.readouterr().out


def test_cli_preview_recipe_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    recipe = tmp_path / "r.yml"
    recipe.write_text(VALID_RECIPE_YAML, encoding="utf-8")
    html = tmp_path / "page.html"
    html.write_text(LISTING_HTML, encoding="utf-8")
    rc = cli_main(["preview", "--recipe-file", str(recipe), "--html", str(html)])
    assert rc == 0


def test_cli_scaffold_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["scaffold", "cli-scaffold-demo", "--stdout"]) == 0
    assert "recipe_id: cli-scaffold-demo" in capsys.readouterr().out


def test_cli_scaffold_writes_via_settings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from civicsignals_api.config import get_settings

    monkeypatch.setenv("RECIPES_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        assert cli_main(["scaffold", "written-recipe"]) == 0
        assert (tmp_path / "written-recipe" / "recipe.yml").exists()
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _example_recipe_path() -> Path:
    return services.recipes_dir() / "wa-state-webs" / "recipe.yml"


def _example_fixture_path() -> Path:
    return services.recipes_dir() / "wa-state-webs" / "fixtures" / "listing-001.html"


def _example_fixture_html() -> str:
    return _example_fixture_path().read_text(encoding="utf-8")
