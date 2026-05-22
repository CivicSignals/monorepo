"""Pydantic request/response shapes for the recipes module (doc 06 §3).

These model the recipe DSL (validated against the canonical draft-07 JSON Schema
in ``packages/recipe-schema``) and the typed outputs of the connector lifecycle
``discover -> fetch -> extract -> normalize`` that ``RecipeRunner`` drives (doc 18
§2, TODO D1/D11). The JSON Schema — not these models — is the source of truth for
*validation*; these models give the runner a typed, ergonomic view *after* a
recipe has passed schema validation.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Default fetch posture (doc 18 §2.2, doc 16 §18). Mirrors the JSON Schema
# defaults; kept here so the runner has them without re-reading the schema.
DEFAULT_USER_AGENT = "CivicSignalsBot/1.0 (+https://civicsignals.io/bot)"
DEFAULT_POLITENESS_SECONDS = 10.0


class SelectorType(StrEnum):
    """Dialect of one selector in a field's ordered fallback chain (doc 18 §3.4)."""

    CSS = "css"
    XPATH = "xpath"


class Selector(BaseModel):
    """One selector in the ordered fallback chain.

    Recipes may write a selector as a bare string (CSS shorthand) or as an
    object ``{selector, type}`` to declare an XPath fallback. The JSON Schema
    accepts both forms; :meth:`FieldSpec.coerce_selectors` normalizes the string
    shorthand into this object so the runner only ever sees one shape.
    """

    model_config = ConfigDict(extra="forbid")

    selector: str
    type: SelectorType = SelectorType.CSS


class ExtractionMethod(StrEnum):
    """How a field's value was obtained, in escalation order (doc 18 §3.4).

    ``PRIMARY`` is index-0 selector hit; ``FALLBACK`` is any later selector;
    ``LLM_ASSISTED`` is the LLM safety net; ``DEAD_LETTER`` means nothing
    produced a value and the field was routed to the dead-letter sink.
    """

    PRIMARY = "primary"
    FALLBACK = "fallback"
    LLM_ASSISTED = "llm_assisted"
    DEAD_LETTER = "dead_letter"


class FieldSpec(BaseModel):
    """One extraction field: an ordered selector chain + optional attribute.

    The runner (D11) tries each selector in order — primary (index 0) →
    fallback(s) → optional LLM-assisted pass → dead-letter — and flags the
    document ``degraded: true`` whenever a non-primary step produced the value
    (doc 18 §3.4).
    """

    model_config = ConfigDict(extra="forbid")

    selectors: list[Selector] = Field(min_length=1)
    attr: str | None = None
    required: bool = False
    # Opt-in LLM safety net (doc 18 §3.4): only fields that set this incur an LLM
    # call when every selector misses, so we never pay for fields fine to leave
    # empty. ``prompt_name`` is the E3 prompt-registry seam (default prompt used
    # until the registry lands).
    llm_assisted: bool = False
    prompt_name: str | None = None

    @model_validator(mode="before")
    @classmethod
    def coerce_selectors(cls, data: object) -> object:
        """Normalize the string-shorthand selector form into ``Selector`` objects.

        The JSON Schema lets an author write ``selectors: ["h1", {selector: …}]``;
        we widen every bare string to ``{selector: <str>, type: css}`` so the rest
        of the module only handles one shape.
        """
        if isinstance(data, dict):
            raw = data.get("selectors")
            if isinstance(raw, list):
                data = {
                    **data,
                    "selectors": [
                        {"selector": item} if isinstance(item, str) else item for item in raw
                    ],
                }
        return data

    @property
    def primary_selector(self) -> Selector:
        return self.selectors[0]


class FetchPolicy(BaseModel):
    """Fetch-time politeness + legal posture for a recipe (doc 18 §2.2)."""

    model_config = ConfigDict(extra="forbid")

    respect_robots_txt: bool = True
    politeness_seconds: float = DEFAULT_POLITENESS_SECONDS
    jitter_seconds: float = 0.0
    user_agent: str = DEFAULT_USER_AGENT
    max_redirects: int = 5


class EntityRef(BaseModel):
    """How ``normalize()`` resolves the producing entity (doc 18 §2.4)."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str | None = None
    name: str | None = None
    state: str | None = None
    kind: str | None = None


class Recipe(BaseModel):
    """A schema-valid recipe, parsed into a typed view for the runner.

    ``version`` is the pinned ``recipe_version`` (doc 18 §3.5): every run and
    every produced record carries it so a bad deploy is rolled back by changing
    the version pointer, not by a code deploy.
    """

    model_config = ConfigDict(extra="forbid")

    recipe_id: str
    connector: str
    version: int
    entity: EntityRef
    tenant: dict[str, object] = Field(default_factory=dict)
    schedule: dict[str, str] = Field(default_factory=dict)
    fetch: FetchPolicy = Field(default_factory=FetchPolicy)
    prefilter: str = "classifier"
    # Per-connector configuration block (doc 18 §1, §5 wave 1; TODO D6). Kept as a
    # raw mapping here so the recipes module stays connector-agnostic — the
    # ingestion ``connectors`` package owns the per-connector config models and
    # parses ``connector_config[connector]`` into them when it dispatches. The
    # canonical JSON Schema in ``packages/recipe-schema`` validates the shape.
    connector_config: dict[str, object] = Field(default_factory=dict)
    fields: dict[str, FieldSpec] = Field(default_factory=dict)
    signal_types: list[str] = Field(default_factory=list)


class SourcePointer(BaseModel):
    """A candidate URL/record discovered for a run (doc 18 §2.1)."""

    model_config = ConfigDict(extra="forbid")

    recipe_id: str
    connector: str
    url: str
    hint_metadata: dict[str, object] = Field(default_factory=dict)


class RawDocument(BaseModel):
    """Raw bytes + provenance from ``fetch()`` (doc 18 §2.2).

    ``fetch()`` never parses; it records the raw content, status, and the pinned
    ``recipe_version`` so extraction is replayable against the snapshot later.
    """

    model_config = ConfigDict(extra="forbid")

    recipe_id: str
    connector: str
    url: str
    status_code: int
    content: str
    content_hash: str
    recipe_version: int
    headers: dict[str, str] = Field(default_factory=dict)


class FieldExtraction(BaseModel):
    """Per-field provenance from the ordered fallback chain (doc 18 §3.4).

    Records *how* each field was obtained so the runner can surface which fields
    were degraded and which dead-lettered, and so drift counters can be ticked
    by escalation step. ``selector`` is the selector string that matched
    (``None`` for the LLM-assisted and dead-letter steps).
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    value: str | None = None
    method: ExtractionMethod
    # 0 for the primary, 1+ for fallbacks; None for LLM/dead-letter.
    selector_index: int | None = None
    selector: str | None = None

    @property
    def degraded(self) -> bool:
        """A field is degraded when a non-primary step produced its value."""
        return self.method in (ExtractionMethod.FALLBACK, ExtractionMethod.LLM_ASSISTED)


class DeadLetterEntry(BaseModel):
    """A field (or document) that nothing could extract (doc 18 §2.3, §3.4).

    Surfaced on the :class:`ExtractedDocument` and persisted to the
    ``recipes_dead_letter`` table by ``services.run_recipe`` so a failed
    extraction is never silently lost — it can be inspected and the recipe fixed,
    then replayed against the snapshot raw document (doc 18 §3.6).
    """

    model_config = ConfigDict(extra="forbid")

    recipe_id: str
    recipe_version: int
    source_url: str
    content_hash: str
    field_name: str
    # Why it dead-lettered: all selectors missed and LLM was disabled, the LLM
    # step itself failed, or the value failed validation.
    reason: str
    tried_selectors: list[str] = Field(default_factory=list)


class DriftCounters(BaseModel):
    """Per-recipe drift counters produced by one extract pass (doc 18 §3.2, §3.4).

    D11 *produces and exposes* these; the rolling-window drift detection that
    consumes them (auto-pause, alerting) is a separate task (TODO E7). Counts are
    per-field so E7 can attribute drift to the specific selector that broke.
    """

    model_config = ConfigDict(extra="forbid")

    # Fields whose value came from a fallback selector (not the primary).
    selector_fallbacks: int = 0
    # Fields whose value came from the LLM-assisted safety net.
    llm_fallbacks: int = 0
    # Fields nothing could extract (routed to the dead-letter sink).
    dead_letters: int = 0
    # Total fields attempted this pass — the denominator for fallback rates.
    fields_total: int = 0
    # Per-field tallies keyed by field name -> count, for E7 attribution.
    fallback_by_field: dict[str, int] = Field(default_factory=dict)
    llm_by_field: dict[str, int] = Field(default_factory=dict)
    dead_letter_by_field: dict[str, int] = Field(default_factory=dict)


class ExtractedDocument(BaseModel):
    """Structured fields produced by ``extract()`` (doc 18 §2.3, §3.1, §3.4).

    ``extraction_method`` is the *document-level* escalation high-water mark:
    ``primary`` when every field hit its primary selector, otherwise the most
    severe step any field needed (``fallback`` < ``llm_assisted`` <
    ``dead_letter``). ``degraded`` is true whenever a non-primary step produced a
    value (doc 18 §3.4). ``recipe_version`` is carried through for provenance.
    ``signal_types`` is the recipe's full declared set — the runner does not
    collapse it to one; assigning a concrete type per record is a
    connector/normalize concern (doc 16 §17.3).
    """

    model_config = ConfigDict(extra="forbid")

    recipe_id: str
    recipe_version: int
    signal_types: list[str] = Field(default_factory=list)
    extraction_method: ExtractionMethod = ExtractionMethod.PRIMARY
    degraded: bool = False
    fields: dict[str, str | None] = Field(default_factory=dict)
    # Per-field provenance + the names that were degraded / dead-lettered, so a
    # caller (and E7) can see *which* fields slipped, not just that some did.
    field_extractions: list[FieldExtraction] = Field(default_factory=list)
    degraded_fields: list[str] = Field(default_factory=list)
    dead_letters: list[DeadLetterEntry] = Field(default_factory=list)
    drift: DriftCounters = Field(default_factory=DriftCounters)


class CanonicalRecord(BaseModel):
    """A normalized record emitted by ``normalize()`` (doc 16 §17.3).

    D1 produces the skeleton (entity ref + extracted fields + provenance);
    entity resolution and signal scoring are downstream tasks (doc 18 §2.4). D11
    threads the degraded flag, the degraded field names, and any dead-letter
    entries through so a downstream consumer sees the extraction was partial.
    """

    model_config = ConfigDict(extra="forbid")

    record_type: str
    recipe_id: str
    recipe_version: int
    source_url: str
    entity: EntityRef
    signal_types: list[str] = Field(default_factory=list)
    degraded: bool = False
    degraded_fields: list[str] = Field(default_factory=list)
    dead_letters: list[DeadLetterEntry] = Field(default_factory=list)
    fields: dict[str, str | None] = Field(default_factory=dict)


class FixtureReplayResult(BaseModel):
    """Outcome of replaying one golden fixture (doc 18 §3.3, TODO D1/D5)."""

    model_config = ConfigDict(extra="forbid")

    recipe_id: str
    fixture: str
    passed: bool
    expected: dict[str, object]
    actual: dict[str, object]
    diff: str | None = None
