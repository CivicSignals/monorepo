"""Tests for the recipe DSL + runner (TODO D1).

Covers: JSON-Schema validation (valid + invalid), robots.txt honoring, the
politeness window (delay + jitter), the full discover->fetch->extract->normalize
lifecycle, recipe_version pinning, and golden-fixture replay for the example
recipe (`wa-state-webs`).
"""

from __future__ import annotations

import pytest

from civicsignals_api.modules.recipes import services
from civicsignals_api.modules.recipes.runner import (
    RecipeValidationError,
    RequiredFieldMissingError,
    RobotsDisallowedError,
)
from civicsignals_api.modules.recipes.schemas import (
    ExtractedDocument,
    ExtractionMethod,
    Recipe,
    SelectorType,
)

# A minimal but complete, schema-valid recipe used across the tests.
VALID_RECIPE: dict[str, object] = {
    "recipe_id": "test-recipe",
    "connector": "http_static",
    "version": 3,
    "entity": {"name": "Test Agency", "state": "WA", "kind": "state_agency"},
    "fetch": {"respect_robots_txt": True, "politeness_seconds": 10, "jitter_seconds": 0},
    "fields": {
        "title": {"selectors": ["h1.title", ".fallback h2"], "required": True},
        "due_date": {"selectors": [".due time"], "attr": "datetime", "required": False},
    },
    "signal_types": ["rfp_posted"],
}

LISTING_HTML = """
<html><body><main>
  <h1 class="title">RFP 42 — Fiber Buildout</h1>
  <p class="due"><time datetime="2026-01-31">Jan 31, 2026</time></p>
</main></body></html>
"""


class FakeClock:
    """Deterministic clock: ``monotonic`` advances only when ``sleep`` is called,
    so a politeness wait is fully assertable without real time passing."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class RecordingFetcher:
    """In-memory fetcher: serves canned bodies + an optional robots.txt."""

    def __init__(self, body: str, *, robots: str | None = None) -> None:
        self.body = body
        self.robots = robots
        self.calls: list[str] = []
        self.robots_calls: list[str] = []

    def fetch(
        self, url: str, *, user_agent: str, max_redirects: int
    ) -> tuple[int, str, dict[str, str]]:
        self.calls.append(url)
        return 200, self.body, {"content-type": "text/html"}

    def robots_txt(self, url: str, *, user_agent: str) -> str | None:
        self.robots_calls.append(url)
        return self.robots


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_valid_recipe_parses() -> None:
    recipe = services.parse_recipe(VALID_RECIPE)
    assert recipe.recipe_id == "test-recipe"
    assert recipe.version == 3
    # FetchPolicy defaults applied for absent keys.
    assert recipe.fetch.user_agent.startswith("CivicSignalsBot/")
    # Selectors normalize to Selector objects (string shorthand -> CSS).
    assert recipe.fields["title"].primary_selector.selector == "h1.title"
    assert recipe.fields["title"].primary_selector.type is SelectorType.CSS


def test_invalid_recipe_missing_required_field_raises() -> None:
    bad = {k: v for k, v in VALID_RECIPE.items() if k != "entity"}
    with pytest.raises(RecipeValidationError) as exc:
        services.parse_recipe(bad, recipe_ref="bad.yml")
    assert any("entity" in m for m in exc.value.messages)


def test_invalid_recipe_unknown_property_rejected() -> None:
    bad = {**VALID_RECIPE, "totally_unknown_key": True}
    with pytest.raises(RecipeValidationError):
        services.parse_recipe(bad)


def test_invalid_recipe_bad_enum_rejected() -> None:
    bad = {**VALID_RECIPE, "prefilter": "nonsense"}
    with pytest.raises(RecipeValidationError):
        services.parse_recipe(bad)


def test_invalid_recipe_empty_selectors_rejected() -> None:
    bad = {
        **VALID_RECIPE,
        "fields": {"title": {"selectors": [], "required": True}},
    }
    with pytest.raises(RecipeValidationError):
        services.parse_recipe(bad)


# ---------------------------------------------------------------------------
# robots.txt honoring
# ---------------------------------------------------------------------------


def test_fetch_blocked_by_robots() -> None:
    recipe = services.parse_recipe(VALID_RECIPE)
    robots = "User-agent: *\nDisallow: /private/\n"
    fetcher = RecordingFetcher(LISTING_HTML, robots=robots)
    runner = services.make_runner(recipe, fetcher, clock=FakeClock())
    pointer = runner.discover(["https://example.gov/private/rfp-42"])[0]
    with pytest.raises(RobotsDisallowedError):
        runner.fetch(pointer)
    assert fetcher.calls == []  # never hit the network


def test_fetch_allowed_by_robots() -> None:
    recipe = services.parse_recipe(VALID_RECIPE)
    robots = "User-agent: *\nDisallow: /private/\n"
    fetcher = RecordingFetcher(LISTING_HTML, robots=robots)
    runner = services.make_runner(recipe, fetcher, clock=FakeClock())
    pointer = runner.discover(["https://example.gov/public/rfp-42"])[0]
    raw = runner.fetch(pointer)
    assert raw.status_code == 200
    assert fetcher.calls == ["https://example.gov/public/rfp-42"]


def test_missing_robots_is_permissive() -> None:
    recipe = services.parse_recipe(VALID_RECIPE)
    fetcher = RecordingFetcher(LISTING_HTML, robots=None)
    runner = services.make_runner(recipe, fetcher, clock=FakeClock())
    pointer = runner.discover(["https://example.gov/public/rfp"])[0]
    raw = runner.fetch(pointer)
    assert raw.status_code == 200


def test_robots_fetched_once_per_host() -> None:
    recipe = services.parse_recipe(VALID_RECIPE)
    fetcher = RecordingFetcher(LISTING_HTML, robots="User-agent: *\nDisallow: /private/\n")
    runner = services.make_runner(recipe, fetcher, clock=FakeClock())
    for path in ("a", "b", "c"):
        runner.fetch(runner.discover([f"https://example.gov/public/{path}"])[0])
    # robots.txt is host-wide -> consulted once, not once per URL.
    assert len(fetcher.robots_calls) == 1
    assert len(fetcher.calls) == 3


def test_robots_disabled_skips_check() -> None:
    data = {**VALID_RECIPE, "fetch": {"respect_robots_txt": False, "politeness_seconds": 0}}
    recipe = services.parse_recipe(data)

    class _NoRobots(RecordingFetcher):
        def robots_txt(self, url: str, *, user_agent: str) -> str | None:
            raise AssertionError("robots_txt must not be consulted when disabled")

    fetcher = _NoRobots(LISTING_HTML)
    runner = services.make_runner(recipe, fetcher, clock=FakeClock())
    pointer = runner.discover(["https://example.gov/private/rfp"])[0]
    raw = runner.fetch(pointer)
    assert raw.status_code == 200


# ---------------------------------------------------------------------------
# Politeness window
# ---------------------------------------------------------------------------


def test_politeness_delay_between_same_host_fetches() -> None:
    recipe = services.parse_recipe(VALID_RECIPE)  # politeness_seconds=10
    fetcher = RecordingFetcher(LISTING_HTML)
    clock = FakeClock()
    runner = services.make_runner(recipe, fetcher, clock=clock)
    pointers = runner.discover(
        ["https://example.gov/a", "https://example.gov/b", "https://example.gov/c"]
    )
    for pointer in pointers:
        runner.fetch(pointer)
    # First fetch: no wait. Each subsequent same-host fetch waits the full window.
    assert clock.slept == [10.0, 10.0]


def test_no_politeness_delay_across_different_hosts() -> None:
    recipe = services.parse_recipe(VALID_RECIPE)
    fetcher = RecordingFetcher(LISTING_HTML)
    clock = FakeClock()
    runner = services.make_runner(recipe, fetcher, clock=clock)
    runner.fetch(runner.discover(["https://a.gov/x"])[0])
    runner.fetch(runner.discover(["https://b.gov/y"])[0])
    assert clock.slept == []  # different hosts: no enforced delay


def test_jitter_adds_extra_delay() -> None:
    data = {
        **VALID_RECIPE,
        "fetch": {"politeness_seconds": 0, "jitter_seconds": 5, "respect_robots_txt": False},
    }
    recipe = services.parse_recipe(data)
    fetcher = RecordingFetcher(LISTING_HTML)
    clock = FakeClock()
    runner = services.make_runner(recipe, fetcher, clock=clock)
    runner.fetch(runner.discover(["https://example.gov/a"])[0])
    assert len(clock.slept) == 1
    assert 0.0 <= clock.slept[0] <= 5.0


# ---------------------------------------------------------------------------
# Lifecycle: extract / normalize / version pinning
# ---------------------------------------------------------------------------


def test_full_lifecycle_pins_version_and_normalizes() -> None:
    recipe = services.parse_recipe(VALID_RECIPE)
    fetcher = RecordingFetcher(LISTING_HTML)
    runner = services.make_runner(recipe, fetcher, clock=FakeClock())
    out = runner.run(["https://example.gov/rfp-42"])
    assert len(out) == 1
    record = out[0]
    assert record.record_type == "Signal"
    assert record.recipe_version == 3  # recipe_version pinned through the chain
    assert record.signal_types == ["rfp_posted"]
    assert record.fields["title"] == "RFP 42 — Fiber Buildout"
    assert record.fields["due_date"] == "2026-01-31"  # read via attr=datetime
    assert record.entity.state == "WA"


def test_all_declared_signal_types_carried_through() -> None:
    # The runner must not collapse the recipe's declared types to one.
    data = {**VALID_RECIPE, "signal_types": ["rfp_posted", "contract_award"]}
    recipe = services.parse_recipe(data)
    fetcher = RecordingFetcher(LISTING_HTML)
    runner = services.make_runner(recipe, fetcher, clock=FakeClock())
    out = runner.run(["https://example.gov/rfp-42"])
    assert out[0].signal_types == ["rfp_posted", "contract_award"]


def test_required_field_missing_raises() -> None:
    recipe = services.parse_recipe(VALID_RECIPE)
    fetcher = RecordingFetcher("<html><body><p>no title here</p></body></html>")
    runner = services.make_runner(recipe, fetcher, clock=FakeClock())
    with pytest.raises(RequiredFieldMissingError):
        runner.run(["https://example.gov/empty"])


def test_optional_field_missing_is_none() -> None:
    recipe = services.parse_recipe(VALID_RECIPE)
    html = '<html><body><h1 class="title">Only a title</h1></body></html>'
    fetcher = RecordingFetcher(html)
    runner = services.make_runner(recipe, fetcher, clock=FakeClock())
    out = runner.run(["https://example.gov/partial"])
    assert out[0].fields["title"] == "Only a title"
    assert out[0].fields["due_date"] is None


# ---------------------------------------------------------------------------
# Golden-fixture replay for the example recipe
# ---------------------------------------------------------------------------


def test_example_recipe_validates_against_schema() -> None:
    recipe = services.load_recipe("wa-state-webs")
    assert recipe.recipe_id == "wa-state-webs"
    assert recipe.connector == "http_static"
    assert recipe.version == 1


def test_example_recipe_passes_golden_fixture() -> None:
    results = services.replay_recipe("wa-state-webs")
    assert results, "expected at least one fixture"
    for result in results:
        assert result.passed, f"{result.fixture} drifted:\n{result.diff}"


def test_all_recipes_pass_golden_fixtures() -> None:
    # CI gate: every committed recipe extracts to its expected JSON.
    for recipe_id in services.list_recipe_ids():
        for result in services.replay_recipe(recipe_id):
            assert result.passed, f"{recipe_id}/{result.fixture} drifted:\n{result.diff}"


# ---------------------------------------------------------------------------
# D11: ordered fallback chain — primary → fallback → LLM-assisted → dead-letter
# ---------------------------------------------------------------------------


class FakeLLMExtractor:
    """Deterministic :class:`LLMFieldExtractor`: maps field name -> value.

    Records every call so a test can assert the LLM rung fired only after the
    selector chain missed and only for opted-in fields.
    """

    def __init__(self, values: dict[str, str | None]) -> None:
        self._values = values
        self.calls: list[str] = []

    def extract_field(
        self, *, field_name: str, text: str, recipe_id: str, prompt_name: str | None
    ) -> str | None:
        self.calls.append(field_name)
        return self._values.get(field_name)


def _recipe(fields: dict[str, object], **overrides: object) -> Recipe:
    data = {**VALID_RECIPE, "fields": fields, **overrides}
    return services.parse_recipe(data)


def _extract(recipe: Recipe, html: str) -> ExtractedDocument:
    runner = services.make_runner(recipe, RecordingFetcher(html), clock=FakeClock())
    return runner.extract_html(html)


def test_primary_selector_hit_is_not_degraded() -> None:
    recipe = _recipe({"title": {"selectors": ["h1.title", ".fallback h2"], "required": True}})
    html = '<html><body><h1 class="title">Primary wins</h1></body></html>'
    doc = _extract(recipe, html)
    assert doc.fields["title"] == "Primary wins"
    assert doc.degraded is False
    assert doc.degraded_fields == []
    assert doc.extraction_method == ExtractionMethod.PRIMARY
    assert doc.drift.selector_fallbacks == 0
    assert doc.drift.llm_fallbacks == 0
    assert doc.drift.dead_letters == 0
    assert doc.drift.fields_total == 1


def test_fallback_selector_hit_flags_degraded_and_ticks_drift() -> None:
    recipe = _recipe({"title": {"selectors": ["h1.title", ".fallback h2"], "required": True}})
    # Primary (h1.title) absent; fallback (.fallback h2) present.
    html = '<html><body><div class="fallback"><h2>Fallback wins</h2></div></body></html>'
    doc = _extract(recipe, html)
    assert doc.fields["title"] == "Fallback wins"
    assert doc.degraded is True
    assert doc.degraded_fields == ["title"]
    assert doc.extraction_method == ExtractionMethod.FALLBACK
    # Drift counters: one selector fallback, attributed to the field.
    assert doc.drift.selector_fallbacks == 1
    assert doc.drift.fallback_by_field == {"title": 1}
    assert doc.drift.llm_fallbacks == 0
    # Per-field provenance records which selector index matched.
    [fe] = doc.field_extractions
    assert fe.method == ExtractionMethod.FALLBACK
    assert fe.selector_index == 1
    assert fe.selector == ".fallback h2"


def test_first_non_empty_selector_wins_over_later_match() -> None:
    # Primary present and non-empty -> later selectors never consulted.
    recipe = _recipe({"title": {"selectors": ["h1.title", "h2.other"]}})
    html = '<html><body><h1 class="title">P</h1><h2 class="other">F</h2></body></html>'
    doc = _extract(recipe, html)
    assert doc.fields["title"] == "P"
    assert doc.extraction_method == ExtractionMethod.PRIMARY


def test_empty_primary_match_falls_through_to_fallback() -> None:
    # Primary element exists but is empty -> treated as a miss, fallback wins.
    recipe = _recipe({"title": {"selectors": ["h1.title", ".fallback h2"]}})
    html = '<html><body><h1 class="title"></h1><div class="fallback"><h2>F</h2></div></body></html>'
    doc = _extract(recipe, html)
    assert doc.fields["title"] == "F"
    assert doc.degraded is True


def test_xpath_fallback_selector() -> None:
    # CSS primary misses; an XPath fallback (object form) wins. Requires lxml
    # (ingestion extra); skip cleanly if not installed in this image.
    pytest.importorskip("lxml")
    recipe = _recipe(
        {
            "title": {
                "selectors": [
                    "h1.title",
                    {"selector": "//div[@id='wrap']/span", "type": "xpath"},
                ]
            }
        }
    )
    html = '<html><body><div id="wrap"><span>XPath wins</span></div></body></html>'
    doc = _extract(recipe, html)
    assert doc.fields["title"] == "XPath wins"
    assert doc.degraded is True
    assert doc.extraction_method == ExtractionMethod.FALLBACK


def test_multiple_xpath_fields_share_one_parse() -> None:
    # Two XPath-using fields in one document: the lxml tree is parsed once and
    # reused (ParsedDocument). Just assert both resolve correctly.
    pytest.importorskip("lxml")
    recipe = _recipe(
        {
            "title": {"selectors": [{"selector": "//h1", "type": "xpath"}]},
            "subtitle": {"selectors": [{"selector": "//h2", "type": "xpath"}]},
        }
    )
    html = "<html><body><h1>Title X</h1><h2>Sub Y</h2></body></html>"
    doc = _extract(recipe, html)
    assert doc.fields["title"] == "Title X"
    assert doc.fields["subtitle"] == "Sub Y"


def test_xpath_attr_selector() -> None:
    # XPath fallback reading an attribute (object form + attr).
    pytest.importorskip("lxml")
    recipe = _recipe(
        {"link": {"selectors": [{"selector": "//a", "type": "xpath"}], "attr": "href"}}
    )
    html = '<html><body><a href="https://x.gov/rfp">RFP</a></body></html>'
    doc = _extract(recipe, html)
    assert doc.fields["link"] == "https://x.gov/rfp"


def test_llm_assisted_hit_flags_degraded_and_ticks_llm_drift() -> None:
    recipe = _recipe({"title": {"selectors": ["h1.title", ".fallback h2"], "llm_assisted": True}})
    html = "<html><body><p>no selector matches here</p></body></html>"
    llm = FakeLLMExtractor({"title": "LLM pulled this"})
    runner = services.make_runner(
        recipe, RecordingFetcher(html), clock=FakeClock(), llm_extractor=llm
    )
    doc = runner.extract_html(html)
    assert doc.fields["title"] == "LLM pulled this"
    assert doc.degraded is True
    assert doc.degraded_fields == ["title"]
    assert doc.extraction_method == ExtractionMethod.LLM_ASSISTED
    assert doc.drift.llm_fallbacks == 1
    assert doc.drift.llm_by_field == {"title": 1}
    assert doc.drift.selector_fallbacks == 0
    assert doc.dead_letters == []
    # LLM consulted exactly once, only after every selector missed.
    assert llm.calls == ["title"]


def test_llm_via_gateway_fake_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    # Drive the real GatewayFieldExtractor through the gateway's FakeBackend.
    from civicsignals_api import llm_gateway
    from civicsignals_api.llm_gateway import FakeBackend, LLMGateway
    from civicsignals_api.modules.recipes.runner import GatewayFieldExtractor

    gateway = LLMGateway(
        {"anthropic": FakeBackend(provider="anthropic", responses=["Title via fake LLM"])}
    )
    monkeypatch.setattr(llm_gateway, "get_gateway", lambda: gateway)

    recipe = _recipe({"title": {"selectors": ["h1.title"], "llm_assisted": True}})
    html = "<html><body><p>nothing matches</p></body></html>"
    runner = services.make_runner(
        recipe, RecordingFetcher(html), clock=FakeClock(), llm_extractor=GatewayFieldExtractor()
    )
    doc = runner.extract_html(html)
    assert doc.fields["title"] == "Title via fake LLM"
    assert doc.extraction_method == ExtractionMethod.LLM_ASSISTED
    assert doc.degraded is True


def test_dead_letter_on_total_failure() -> None:
    # All selectors miss and LLM is disabled -> field dead-letters (optional, so
    # no exception). Drift + dead-letter sink record the loss.
    recipe = _recipe({"due_date": {"selectors": [".due time"], "attr": "datetime"}})
    html = "<html><body><p>no due date anywhere</p></body></html>"
    doc = _extract(recipe, html)
    assert doc.fields["due_date"] is None
    assert doc.extraction_method == ExtractionMethod.DEAD_LETTER
    assert doc.drift.dead_letters == 1
    assert doc.drift.dead_letter_by_field == {"due_date": 1}
    assert len(doc.dead_letters) == 1
    entry = doc.dead_letters[0]
    assert entry.field_name == "due_date"
    assert entry.recipe_id == "test-recipe"
    assert entry.recipe_version == 3
    assert "disabled" in entry.reason
    assert entry.tried_selectors == [".due time"]
    # A non-LLM dead-letter is not "degraded" (no value was produced at all).
    assert doc.degraded is False


def test_dead_letter_when_llm_returns_nothing() -> None:
    recipe = _recipe({"due_date": {"selectors": [".due time"], "llm_assisted": True}})
    html = "<html><body><p>nothing</p></body></html>"
    llm = FakeLLMExtractor({"due_date": None})  # LLM also whiffs
    runner = services.make_runner(
        recipe, RecordingFetcher(html), clock=FakeClock(), llm_extractor=llm
    )
    doc = runner.extract_html(html)
    assert doc.fields["due_date"] is None
    assert doc.drift.dead_letters == 1
    assert doc.drift.llm_fallbacks == 0
    assert len(doc.dead_letters) == 1
    assert "returned nothing" in doc.dead_letters[0].reason
    assert llm.calls == ["due_date"]


def test_required_field_still_raises_after_chain_exhausted() -> None:
    recipe = _recipe({"title": {"selectors": ["h1.title"], "required": True}})
    html = "<html><body><p>no title</p></body></html>"
    runner = services.make_runner(recipe, RecordingFetcher(html), clock=FakeClock())
    with pytest.raises(RequiredFieldMissingError):
        runner.extract_html(html)


def test_drift_counters_aggregate_across_fields() -> None:
    recipe = _recipe(
        {
            "primary_ok": {"selectors": ["h1.title"]},
            "needs_fallback": {"selectors": ["h2.gone", ".fallback h3"]},
            "needs_llm": {"selectors": [".missing"], "llm_assisted": True},
            "dead": {"selectors": [".also-missing"]},
        }
    )
    html = (
        '<html><body><h1 class="title">P</h1><div class="fallback"><h3>F</h3></div></body></html>'
    )
    llm = FakeLLMExtractor({"needs_llm": "L"})
    runner = services.make_runner(
        recipe, RecordingFetcher(html), clock=FakeClock(), llm_extractor=llm
    )
    doc = runner.extract_html(html)
    assert doc.drift.fields_total == 4
    assert doc.drift.selector_fallbacks == 1
    assert doc.drift.llm_fallbacks == 1
    assert doc.drift.dead_letters == 1
    assert set(doc.degraded_fields) == {"needs_fallback", "needs_llm"}
    # Document method is the most severe step any field needed.
    assert doc.extraction_method == ExtractionMethod.DEAD_LETTER


def test_normalize_threads_degraded_and_dead_letters_through() -> None:
    recipe = _recipe(
        {
            "title": {"selectors": ["h1.title", ".fallback h2"], "required": True},
            "due_date": {"selectors": [".due time"], "attr": "datetime"},
        }
    )
    html = '<html><body><div class="fallback"><h2>Fallback title</h2></div></body></html>'
    runner = services.make_runner(recipe, RecordingFetcher(html), clock=FakeClock())
    records = runner.run(["https://example.gov/x"])
    [record] = records
    assert record.degraded is True
    assert record.degraded_fields == ["title"]
    assert [d.field_name for d in record.dead_letters] == ["due_date"]


def test_llm_not_called_when_no_field_opts_in() -> None:
    # A selector-only recipe never constructs/invokes the LLM extractor even when
    # selectors miss — we don't pay for an LLM call on every field.
    recipe = _recipe({"title": {"selectors": ["h1.title"]}})
    html = "<html><body><p>nope</p></body></html>"

    class _Boom:
        def extract_field(self, **kw: object) -> str | None:
            raise AssertionError("LLM must not be consulted for a non-llm_assisted field")

    runner = services.make_runner(
        recipe, RecordingFetcher(html), clock=FakeClock(), llm_extractor=_Boom()
    )
    doc = runner.extract_html(html)
    assert doc.fields["title"] is None
    assert doc.drift.dead_letters == 1


async def test_persist_dead_letters_stages_rows() -> None:
    # services.persist_dead_letters stages a row per entry against a session;
    # use a stub session so the test stays DB-free.
    from civicsignals_api.modules.recipes.schemas import DeadLetterEntry

    class _StubSession:
        def __init__(self) -> None:
            self.added: list[object] = []

        def add(self, obj: object) -> None:
            self.added.append(obj)

    session = _StubSession()
    entries = [
        DeadLetterEntry(
            recipe_id="r",
            recipe_version=2,
            source_url="https://x/y",
            content_hash="abc",
            field_name="title",
            reason="all selectors missed; llm-assisted extraction disabled for this field",
            tried_selectors=["h1", "h2"],
        )
    ]
    n = await services.persist_dead_letters(session, entries)  # type: ignore[arg-type]
    assert n == 1
    assert len(session.added) == 1
    row = session.added[0]
    assert row.recipe_id == "r"  # type: ignore[attr-defined]
    assert row.tried_selectors == ["h1", "h2"]  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# D11: schema acceptance of the enriched selector forms
# ---------------------------------------------------------------------------


def test_object_form_selector_accepted_and_typed() -> None:
    recipe = _recipe({"title": {"selectors": [{"selector": "//h1", "type": "xpath"}]}})
    sel = recipe.fields["title"].primary_selector
    assert sel.selector == "//h1"
    assert sel.type is SelectorType.XPATH


def test_invalid_selector_type_rejected() -> None:
    bad = {
        **VALID_RECIPE,
        "fields": {"title": {"selectors": [{"selector": "//h1", "type": "regex"}]}},
    }
    with pytest.raises(RecipeValidationError):
        services.parse_recipe(bad)


def test_llm_assisted_flag_parses() -> None:
    recipe = _recipe(
        {"title": {"selectors": ["h1"], "llm_assisted": True, "prompt_name": "extract_title"}}
    )
    spec = recipe.fields["title"]
    assert spec.llm_assisted is True
    assert spec.prompt_name == "extract_title"
