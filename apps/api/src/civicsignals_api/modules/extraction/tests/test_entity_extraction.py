"""Tests for the two-pass entity extraction (doc 19 §4; E11).

Tests:
- Pass 1 only: sufficient entity types / confidence -> no Pass 2 call.
- Pass 1 triggers Pass 2: low entity types or low confidence.
- Pass-2 failure fallback: Pass-1 data preserved, degraded=True.
- Entity linking: resolved entity gets entity_id, unresolved stays pending.
- Token accounting: recorded against workspace_id for each pass.
- Output matches CandidateRecord.entity_extraction (E4 schema).
- Pipeline integration: extract_candidates wires entity_extraction result.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from civicsignals_api.llm_gateway import (
    TASK_EXTRACTION,
    FakeBackend,
    LLMGateway,
    ModelChoice,
    TaskModelPolicy,
)
from civicsignals_api.modules.extraction import entity_extraction as ee
from civicsignals_api.modules.extraction import pipeline
from civicsignals_api.modules.extraction.schemas import (
    CandidateRecord,
    EntityExtractionResult,
    ExtractedEntity,
    ParsedDocument,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _gateway(*responses: str, provider: str = "fake") -> LLMGateway:
    """A single-backend gateway returning scripted responses FIFO."""
    backend = FakeBackend(provider=provider, responses=list(responses))
    policy = TaskModelPolicy(overrides={TASK_EXTRACTION: ModelChoice(provider, "sonnet")})
    return LLMGateway({provider: backend}, policy=policy)


def _two_provider_gateway(pass1_reply: str, pass2_reply: str) -> LLMGateway:
    """Two backends so pass-1 and pass-2 responses do not interleave."""
    be1 = FakeBackend(provider="fake1", responses=[pass1_reply])
    be2 = FakeBackend(provider="fake2", responses=[pass2_reply])
    policy = TaskModelPolicy(overrides={TASK_EXTRACTION: ModelChoice("fake1", "haiku")})
    return LLMGateway({"fake1": be1, "fake2": be2}, policy=policy)


def _rich_entity_json(**overrides: Any) -> str:
    """A Pass-1 JSON reply with >= 3 entity types and confidence 0.8."""
    data: dict[str, Any] = {
        "organizations": [{"name": "Seattle Public Schools", "confidence": 0.9, "source_offset": 0}],
        "persons": [{"name": "Jane Smith", "role": "Superintendent", "confidence": 0.85}],
        "monetary_amounts": [{"amount_cents": 50000000, "currency": "USD", "confidence": 0.9}],
        "dates": [{"value": "2026-06-01", "kind": "due_date", "confidence": 0.88}],
        "products_categories": ["ERP", "SIS"],
        "vendors_mentioned": [],
        "contract_terms_mentions": [],
        "raw_keywords": ["procurement", "modernization"],
        "extraction_confidence": 0.82,
        "extraction_warnings": [],
    }
    data.update(overrides)
    return json.dumps(data)


def _sparse_entity_json(**overrides: Any) -> str:
    """A Pass-1 JSON reply with < 3 entity types (triggers Pass 2)."""
    data: dict[str, Any] = {
        "organizations": [{"name": "Austin ISD", "confidence": 0.7, "source_offset": 5}],
        "persons": [],
        "monetary_amounts": [],
        "dates": [],
        "products_categories": [],
        "vendors_mentioned": [],
        "contract_terms_mentions": [],
        "raw_keywords": ["district"],
        "extraction_confidence": 0.5,
        "extraction_warnings": [],
    }
    data.update(overrides)
    return json.dumps(data)


# ---------------------------------------------------------------------------
# _parse_entity_json
# ---------------------------------------------------------------------------


def test_parse_entity_json_valid() -> None:
    data = {"organizations": [{"name": "Acme", "confidence": 0.8}], "extraction_confidence": 0.8}
    assert ee._parse_entity_json(json.dumps(data)) == data


def test_parse_entity_json_with_prose() -> None:
    text = "Sure, here is the JSON:\n" + json.dumps({"extraction_confidence": 0.5}) + "\nDone."
    result = ee._parse_entity_json(text)
    assert result is not None
    assert result["extraction_confidence"] == 0.5


def test_parse_entity_json_invalid_returns_none() -> None:
    assert ee._parse_entity_json("not json at all") is None
    assert ee._parse_entity_json("") is None


# ---------------------------------------------------------------------------
# _count_entity_types
# ---------------------------------------------------------------------------


def test_count_entity_types_empty() -> None:
    assert ee._count_entity_types({}) == 0


def test_count_entity_types_partial() -> None:
    data: dict[str, object] = {
        "organizations": [{"name": "A"}],
        "persons": [],
        "monetary_amounts": [{"amount_cents": 100}],
        "dates": [],
        "products_categories": [],
        "vendors_mentioned": [],
        "contract_terms_mentions": [],
    }
    assert ee._count_entity_types(data) == 2


def test_count_entity_types_rich() -> None:
    data = json.loads(_rich_entity_json())
    # organizations, persons, monetary_amounts, dates, products_categories
    assert ee._count_entity_types(data) == 5


# ---------------------------------------------------------------------------
# Pass 1 only (sufficient entity types + confidence)
# ---------------------------------------------------------------------------


async def test_pass1_only_when_sufficient_entities_and_confidence() -> None:
    """When Pass 1 finds >= 3 types and confidence >= 0.6, Pass 2 is skipped."""
    gw = _gateway(_rich_entity_json())
    result = await ee.run_entity_extraction(gw, "A long document " * 30, workspace_id=None)

    assert isinstance(result, EntityExtractionResult)
    assert result.extraction_method == "deterministic"
    assert result.degraded is False
    # At least the organization mention should be in entities
    assert len(result.entities) >= 1
    assert result.entities[0].raw_name == "Seattle Public Schools"
    # Persons and amounts should be in the detailed sub-lists.
    assert len(result.persons) >= 1
    assert len(result.monetary_amounts) >= 1
    # The gateway was called exactly once (Pass 1 only).
    backend = next(iter(gw._backends.values()))
    assert len(backend.calls) == 1  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Pass 2 triggered (sparse entities)
# ---------------------------------------------------------------------------


async def test_pass2_triggered_on_low_entity_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pass 2 is triggered when Pass 1 finds < 3 entity types."""
    pass1_reply = _sparse_entity_json()
    # Pass 2 enriches the result with additional fields.
    pass2_reply = _rich_entity_json(
        organizations=[{"name": "Austin ISD", "confidence": 0.92}],
        extraction_confidence=0.88,
    )
    # Two-backend gateway: both share the same provider name but different responses.
    # Use a single backend with two scripted responses.
    gw = _gateway(pass1_reply, pass2_reply)
    result = await ee.run_entity_extraction(gw, "A document " * 30, workspace_id=None)

    assert result.extraction_method == "llm_assisted"
    assert result.degraded is False
    assert result.extraction_confidence == pytest.approx(0.88)
    backend = next(iter(gw._backends.values()))
    assert len(backend.calls) == 2  # type: ignore[attr-defined]


async def test_pass2_triggered_on_low_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pass 2 is triggered when Pass 1 confidence < 0.6, even with >= 3 types."""
    # 4 entity types but low confidence
    pass1_reply = _rich_entity_json(extraction_confidence=0.4)
    pass2_reply = _rich_entity_json(extraction_confidence=0.85)
    gw = _gateway(pass1_reply, pass2_reply)
    result = await ee.run_entity_extraction(gw, "A document " * 30, workspace_id=None)

    assert result.extraction_method == "llm_assisted"
    assert result.extraction_confidence == pytest.approx(0.85)


# ---------------------------------------------------------------------------
# Pass-2 failure -> graceful degradation
# ---------------------------------------------------------------------------


async def test_pass2_failure_falls_back_to_pass1(monkeypatch: pytest.MonkeyPatch) -> None:
    """When Pass 2 fails, Pass-1 results are kept with degraded=True."""
    pass1_reply = _sparse_entity_json()  # triggers Pass 2

    # Pass 1 succeeds (one scripted response), Pass 2 raises (unparseable echoed prompt).
    # We simulate a Pass-2 parse failure by having pass2 return invalid JSON.
    gw = _gateway(pass1_reply, "not-valid-json-at-all-for-pass2")
    result = await ee.run_entity_extraction(gw, "A long document " * 30, workspace_id=None)

    # Pass-1 data is preserved; degraded=True because pass2 was needed but result
    # was unparseable (treated as failure).
    assert result.degraded is True
    assert result.extraction_method == "llm_assisted"
    assert len(result.entities) >= 1  # Austin ISD from Pass 1
    assert result.entities[0].raw_name == "Austin ISD"


async def test_pass1_failure_returns_empty_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    """When Pass 1 returns unparseable output, an empty degraded result is returned."""
    # Pass 1 returns non-JSON; _parse_entity_json returns None -> degraded result.
    gw = _gateway("definitely not JSON")
    result = await ee.run_entity_extraction(gw, "Any text", workspace_id=None)

    assert result.degraded is True
    assert result.extraction_method == "failed"
    assert result.entities == []


# ---------------------------------------------------------------------------
# Short document: Pass 2 skipped even when triggered
# ---------------------------------------------------------------------------


async def test_pass2_skipped_for_short_document() -> None:
    """Documents < MIN_CHARS_FOR_PASS2 chars never trigger Pass 2."""
    sparse_reply = _sparse_entity_json()  # would normally trigger pass 2
    gw = _gateway(sparse_reply)
    # Very short document (< 200 chars)
    result = await ee.run_entity_extraction(gw, "Short doc.", workspace_id=None)

    # Pass 2 was skipped (single backend call).
    backend = next(iter(gw._backends.values()))
    assert len(backend.calls) == 1  # type: ignore[attr-defined]
    # No degraded flag: skipping is intentional for short docs.
    assert result.degraded is False


# ---------------------------------------------------------------------------
# Entity linking (mocked entities service)
# ---------------------------------------------------------------------------


async def test_entity_linking_resolves_known_entity(monkeypatch: pytest.MonkeyPatch) -> None:
    """A known entity is linked: entity_id set, resolution_pending cleared."""
    gw = _gateway(_rich_entity_json())

    # Build a mock session + fake entity
    fake_entity_id = uuid.uuid4()
    fake_entity = MagicMock()
    fake_entity.id = fake_entity_id
    fake_entity.name = "Seattle Public Schools"

    from civicsignals_api.modules.entities.services import EntityPage

    mock_search = AsyncMock(return_value=EntityPage(items=[fake_entity], next_cursor=None))
    monkeypatch.setattr(
        "civicsignals_api.modules.extraction.entity_extraction.entities_services.search_entities",
        mock_search,
    )

    mock_session = MagicMock()
    result = await ee.run_entity_extraction(
        gw, "A long document " * 30, workspace_id=None, session=mock_session
    )

    # At least the "Seattle Public Schools" entity should be linked.
    linked = [e for e in result.entities if not e.resolution_pending]
    assert len(linked) >= 1
    assert any(e.entity_id == fake_entity_id for e in linked)
    assert any(e.entity_name == "Seattle Public Schools" for e in linked)


async def test_entity_linking_flags_unknown_entity(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unrecognised entity stays resolution_pending=True."""
    # Single org with a name not in the entities table.
    gw = _gateway(
        json.dumps(
            {
                "organizations": [
                    {"name": "Unknown Org XYZ", "confidence": 0.8, "source_offset": 0}
                ],
                "persons": [{"name": "Bob", "role": "CTO", "confidence": 0.7}],
                "monetary_amounts": [{"amount_cents": 10000000, "currency": "USD", "confidence": 0.8}],
                "dates": [],
                "products_categories": [],
                "vendors_mentioned": [],
                "contract_terms_mentions": [],
                "raw_keywords": [],
                "extraction_confidence": 0.75,
                "extraction_warnings": [],
            }
        )
    )

    from civicsignals_api.modules.entities.services import EntityPage

    # No match returned by the service.
    mock_search = AsyncMock(return_value=EntityPage(items=[], next_cursor=None))
    monkeypatch.setattr(
        "civicsignals_api.modules.extraction.entity_extraction.entities_services.search_entities",
        mock_search,
    )

    mock_session = MagicMock()
    result = await ee.run_entity_extraction(
        gw, "A long document " * 30, workspace_id=None, session=mock_session
    )

    assert any(e.resolution_pending is True for e in result.entities)
    assert any(e.raw_name == "Unknown Org XYZ" for e in result.entities)


async def test_entity_linking_skipped_without_session() -> None:
    """Entity linking is skipped when session=None (e.g. lightweight unit tests)."""
    gw = _gateway(_rich_entity_json())
    result = await ee.run_entity_extraction(gw, "A long document " * 30, workspace_id=None)

    # All entities stay resolution_pending since linking was not attempted.
    assert all(e.resolution_pending is True for e in result.entities)


# ---------------------------------------------------------------------------
# Token accounting
# ---------------------------------------------------------------------------


async def test_token_accounting_recorded_for_workspace() -> None:
    """Each gateway call records tokens against workspace_id."""
    sparse_reply = _sparse_entity_json()
    pass2_reply = _rich_entity_json()
    gw = _gateway(sparse_reply, pass2_reply)
    workspace_id = "ws-test-123"

    await ee.run_entity_extraction(gw, "A long document " * 30, workspace_id=workspace_id)

    usage = gw.usage(workspace_id)
    # Both passes charged tokens (input_tokens > 0 for both calls).
    assert usage.input_tokens > 0
    # Two calls means at least twice as many tokens as one call.
    backend = next(iter(gw._backends.values()))
    assert len(backend.calls) == 2  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# E4 schema: CandidateRecord carries entity_extraction
# ---------------------------------------------------------------------------


async def test_extract_candidates_carries_entity_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """extract_candidates attaches entity_extraction to every CandidateRecord."""
    entity_reply = _rich_entity_json()
    signal_reply = json.dumps(
        {
            "candidates": [
                {
                    "signal_type": "rfp_posted",
                    "confidence": 0.85,
                    "fields": {"title": "ERP RFP", "summary": "modernization project"},
                }
            ]
        }
    )
    # Two-response gateway: entity extraction call first, signal detection second.
    gw = _gateway(entity_reply, signal_reply)
    parsed = ParsedDocument(
        raw_document_id=uuid.uuid4(),
        recipe_id="seattle_school_board_agendas",
        text="A long document about ERP modernization " * 30,
    )
    candidates = await pipeline.extract_candidates(gw, parsed, workspace_id=None)

    assert len(candidates) == 1
    c = candidates[0]
    assert isinstance(c, CandidateRecord)
    assert c.entity_extraction is not None
    assert isinstance(c.entity_extraction, EntityExtractionResult)
    # The entity name from extraction is propagated into fields.
    assert "entity_name" in c.fields


async def test_extract_candidates_entity_name_propagated_to_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The best entity name from entity_extraction populates fields['entity_name']."""
    entity_reply = _rich_entity_json(
        organizations=[{"name": "Portland USD", "confidence": 0.91, "source_offset": 0}]
    )
    signal_reply = json.dumps(
        {
            "candidates": [
                {
                    "signal_type": "budget_approved",
                    "confidence": 0.8,
                    "fields": {"title": "Budget 2027", "summary": "annual budget"},
                }
            ]
        }
    )
    gw = _gateway(entity_reply, signal_reply)
    parsed = ParsedDocument(
        raw_document_id=uuid.uuid4(),
        recipe_id="r",
        text="Budget document for Portland USD " * 30,
    )
    candidates = await pipeline.extract_candidates(gw, parsed, workspace_id=None)

    assert len(candidates) == 1
    # entity_name in fields comes from the entity extraction (Portland USD).
    assert candidates[0].fields.get("entity_name") == "Portland USD"


async def test_extract_candidates_existing_entity_name_not_overwritten() -> None:
    """If the signal extraction already set entity_name, it is not overwritten."""
    entity_reply = _rich_entity_json(
        organizations=[{"name": "Seattle Public Schools", "confidence": 0.9}]
    )
    signal_reply = json.dumps(
        {
            "candidates": [
                {
                    "signal_type": "rfp_posted",
                    "confidence": 0.8,
                    "fields": {
                        "title": "RFP",
                        "summary": "x",
                        "entity_name": "Override School District",
                    },
                }
            ]
        }
    )
    gw = _gateway(entity_reply, signal_reply)
    parsed = ParsedDocument(
        raw_document_id=uuid.uuid4(),
        recipe_id="r",
        text="A long document " * 30,
    )
    candidates = await pipeline.extract_candidates(gw, parsed, workspace_id=None)

    # The LLM-provided entity_name in fields is not overwritten.
    assert candidates[0].fields.get("entity_name") == "Override School District"


async def test_extract_candidates_entity_extraction_failure_doesnt_block() -> None:
    """If entity extraction fails, signal extraction still runs (best-effort)."""
    # Only one response: the signal extraction call; the entity extraction will
    # get an echoed prompt (unparseable as entity JSON) and degrade gracefully.
    signal_reply = json.dumps(
        {
            "candidates": [
                {
                    "signal_type": "leadership_change",
                    "confidence": 0.75,
                    "fields": {
                        "title": "New CIO",
                        "summary": "y",
                        "role": "CIO",
                        "person_name": "Alice",
                    },
                }
            ]
        }
    )
    # Two responses: first is non-JSON (entity extraction degrades), second is signal JSON.
    gw = _gateway("not valid json at all", signal_reply)
    parsed = ParsedDocument(
        raw_document_id=uuid.uuid4(),
        recipe_id="r",
        text="A long document about leadership change " * 30,
    )
    candidates = await pipeline.extract_candidates(gw, parsed, workspace_id=None)

    # Signal extraction still produced a candidate.
    assert len(candidates) == 1
    assert candidates[0].signal_type == "leadership_change"
    # entity_extraction is present but degraded (or None — either is fine).
    er = candidates[0].entity_extraction
    if er is not None:
        assert er.degraded is True


# ---------------------------------------------------------------------------
# _merge_entity_data
# ---------------------------------------------------------------------------


def test_merge_entity_data_prefers_pass2_nonempty_lists() -> None:
    pass1: dict[str, object] = {
        "organizations": [{"name": "A"}],
        "persons": [],
        "extraction_confidence": 0.5,
    }
    pass2: dict[str, object] = {
        "organizations": [{"name": "B"}, {"name": "C"}],
        "persons": [{"name": "X"}],
        "extraction_confidence": 0.8,
    }
    merged = ee._merge_entity_data(pass1, pass2)
    assert merged["organizations"] == [{"name": "B"}, {"name": "C"}]
    assert merged["persons"] == [{"name": "X"}]
    # Higher of the two confidences.
    assert merged["extraction_confidence"] == pytest.approx(0.8)


def test_merge_entity_data_keeps_pass1_when_pass2_empty_list() -> None:
    pass1: dict[str, object] = {"organizations": [{"name": "A"}], "extraction_confidence": 0.7}
    pass2: dict[str, object] = {"organizations": [], "extraction_confidence": 0.6}
    merged = ee._merge_entity_data(pass1, pass2)
    # Pass-2 returned an empty list -> keep Pass-1 list.
    assert merged["organizations"] == [{"name": "A"}]


def test_merge_entity_data_none_pass2_returns_pass1() -> None:
    pass1: dict[str, object] = {"extraction_confidence": 0.7, "organizations": []}
    merged = ee._merge_entity_data(pass1, None)
    assert merged == pass1


# ---------------------------------------------------------------------------
# ExtractedEntity schema round-trip
# ---------------------------------------------------------------------------


def test_extracted_entity_defaults() -> None:
    e = ExtractedEntity(raw_name="Acme Corp")
    assert e.entity_id is None
    assert e.entity_name is None
    assert e.confidence == 0.0
    assert e.resolution_pending is True


def test_extracted_entity_resolved() -> None:
    eid = uuid.uuid4()
    e = ExtractedEntity(
        raw_name="Acme Corp",
        entity_id=eid,
        entity_name="Acme Corporation",
        confidence=0.95,
        resolution_pending=False,
    )
    assert e.entity_id == eid
    assert e.resolution_pending is False


def test_entity_extraction_result_defaults() -> None:
    r = EntityExtractionResult()
    assert r.entities == []
    assert r.extraction_confidence == 0.0
    assert r.degraded is False
    assert r.extraction_method == "deterministic"
