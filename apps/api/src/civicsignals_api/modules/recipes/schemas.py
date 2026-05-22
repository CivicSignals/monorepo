"""Pydantic request/response shapes for the recipes module (doc 06 §3).

These model the recipe DSL (validated against the canonical draft-07 JSON Schema
in ``packages/recipe-schema``) and the typed outputs of the connector lifecycle
``discover -> fetch -> extract -> normalize`` that ``RecipeRunner`` drives (doc 18
§2, TODO D1). The JSON Schema — not these models — is the source of truth for
*validation*; these models give the runner a typed, ergonomic view *after* a
recipe has passed schema validation.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# Default fetch posture (doc 18 §2.2, doc 16 §18). Mirrors the JSON Schema
# defaults; kept here so the runner has them without re-reading the schema.
DEFAULT_USER_AGENT = "CivicSignalsBot/1.0 (+https://civicsignals.io/bot)"
DEFAULT_POLITENESS_SECONDS = 10.0


class FieldSpec(BaseModel):
    """One extraction field: an ordered selector list + optional attribute.

    D1 evaluates the *primary* (index 0) selector only; the ordered fallback
    chain (flagging ``degraded: true`` on a fallback hit) is wired up in D11.
    """

    model_config = ConfigDict(extra="forbid")

    selectors: list[str] = Field(min_length=1)
    attr: str | None = None
    required: bool = False

    @property
    def primary_selector(self) -> str:
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


class ExtractedDocument(BaseModel):
    """Structured fields produced by ``extract()`` (doc 18 §2.3, §3.1).

    ``extraction_method`` is ``"primary"`` for D1; ``degraded`` stays ``False``
    until the D11 fallback chain can flip it. ``recipe_version`` is carried
    through for provenance.
    """

    model_config = ConfigDict(extra="forbid")

    recipe_id: str
    recipe_version: int
    signal_type: str | None = None
    extraction_method: str = "primary"
    degraded: bool = False
    fields: dict[str, str | None] = Field(default_factory=dict)


class CanonicalRecord(BaseModel):
    """A normalized record emitted by ``normalize()`` (doc 16 §17.3).

    D1 produces the skeleton (entity ref + extracted fields + provenance);
    entity resolution and signal scoring are downstream tasks (doc 18 §2.4).
    """

    model_config = ConfigDict(extra="forbid")

    record_type: str
    recipe_id: str
    recipe_version: int
    source_url: str
    entity: EntityRef
    signal_type: str | None = None
    degraded: bool = False
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
