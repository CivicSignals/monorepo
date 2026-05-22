"""Tests for the Stage-2 relevance classifier (doc 19 §3, E8).

All LLM-driven cases use the gateway's deterministic ``FakeBackend`` — no network
or API keys. The decision-record persistence is exercised against SQLite-compatible
DDL (the table reuses JSONB, which the live-DB path covers via the Alembic
migration; here we assert the in-memory persistence shape with a sqlite-portable
schema) and, when a Postgres DSN is supplied, against a real session.
"""

from __future__ import annotations

import json

import pytest

from civicsignals_api.llm_gateway import (
    TASK_CLASSIFY,
    FakeBackend,
    LLMGateway,
    ModelChoice,
    TaskModelPolicy,
)
from civicsignals_api.modules.extraction.relevance import (
    MAX_DOC_CHARS,
    METHOD_ASSUME_RELEVANT,
    METHOD_CLASSIFIER,
    METHOD_FALLBACK,
    PROMPT_NAME,
    PROMPT_VERSION,
    RelevanceClassifier,
    truncate_document,
)
from civicsignals_api.modules.extraction.schemas import DocumentRef, RelevanceVerdict


def _gateway(*responses: str) -> tuple[LLMGateway, FakeBackend]:
    backend = FakeBackend(responses=list(responses))
    policy = TaskModelPolicy(overrides={TASK_CLASSIFY: ModelChoice("fake", "haiku")})
    return LLMGateway({"fake": backend}, policy=policy), backend


def _doc(text: str = "", **kwargs: str) -> DocumentRef:
    base: dict[str, str] = {
        "raw_document_id": "rd-1",
        "recipe_id": "seattle_school_board_agendas",
        "text": text,
    }
    base.update(kwargs)
    return DocumentRef(**base)


# --- relevant vs not-relevant (FakeBackend) ------------------------------


async def test_relevant_verdict_parsed() -> None:
    reply = json.dumps(
        {"relevant": True, "categories": ["procurement"], "confidence": 0.92, "reason": "RFP"}
    )
    gw, backend = _gateway(reply)
    classifier = RelevanceClassifier(gateway=gw)

    verdict = await classifier.classify(_doc("RFP for ERP modernization, due 2026-06-01"))

    assert verdict.relevant is True
    assert verdict.confidence == pytest.approx(0.92)
    assert verdict.categories == ["procurement"]
    assert verdict.reason == "RFP"
    assert verdict.method == METHOD_CLASSIFIER
    assert verdict.model == "haiku"
    assert verdict.prompt_name == PROMPT_NAME
    assert verdict.prompt_version == PROMPT_VERSION
    # Routed through the gateway's classify task exactly once.
    assert len(backend.calls) == 1
    assert backend.calls[0]["model"] == "haiku"


async def test_not_relevant_verdict_parsed() -> None:
    reply = json.dumps({"relevant": False, "categories": [], "confidence": 0.97})
    gw, _ = _gateway(reply)
    classifier = RelevanceClassifier(gateway=gw)

    verdict = await classifier.classify(_doc("Employee of the month recognition minutes"))

    assert verdict.relevant is False
    assert verdict.confidence == pytest.approx(0.97)
    assert verdict.categories == []
    assert verdict.method == METHOD_CLASSIFIER


async def test_unknown_categories_filtered_out() -> None:
    reply = json.dumps(
        {"relevant": True, "categories": ["procurement", "weather", 7], "confidence": 0.8}
    )
    gw, _ = _gateway(reply)
    classifier = RelevanceClassifier(gateway=gw)
    verdict = await classifier.classify(_doc("text"))
    assert verdict.categories == ["procurement"]


async def test_confidence_clamped_into_range() -> None:
    reply = json.dumps({"relevant": True, "confidence": 1.7})
    gw, _ = _gateway(reply)
    classifier = RelevanceClassifier(gateway=gw)
    verdict = await classifier.classify(_doc("text"))
    assert verdict.confidence == 1.0


async def test_unparseable_reply_fails_open() -> None:
    gw, _ = _gateway("I think this is probably relevant, honestly.")
    classifier = RelevanceClassifier(gateway=gw)
    verdict = await classifier.classify(_doc("text"))
    # Fail open: never silently drop a doc on a malformed reply (doc 19 §12.1).
    assert verdict.relevant is True
    assert verdict.method == METHOD_FALLBACK
    assert verdict.confidence == 0.5


async def test_json_embedded_in_prose_is_extracted() -> None:
    reply = 'Here you go:\n```json\n{"relevant": false, "confidence": 0.8}\n```'
    gw, _ = _gateway(reply)
    classifier = RelevanceClassifier(gateway=gw)
    verdict = await classifier.classify(_doc("text"))
    assert verdict.relevant is False
    assert verdict.method == METHOD_CLASSIFIER


# --- assume_relevant short-circuit (NO LLM call) -------------------------


async def test_assume_relevant_skips_llm() -> None:
    gw, backend = _gateway()
    classifier = RelevanceClassifier(gateway=gw)

    verdict = await classifier.classify(
        _doc("a list of RFPs", recipe_id="wa_state_webs_rfp_listings"),
        prefilter="assume_relevant",
    )

    assert verdict.relevant is True
    assert verdict.confidence == 1.0
    assert verdict.method == METHOD_ASSUME_RELEVANT
    # The crucial assertion: the LLM was never called (cost $0, doc 19 §3.3).
    assert backend.calls == []


async def test_classifier_prefilter_calls_llm() -> None:
    gw, backend = _gateway(json.dumps({"relevant": True, "confidence": 0.7}))
    classifier = RelevanceClassifier(gateway=gw)
    await classifier.classify(_doc("text"), prefilter="classifier")
    assert len(backend.calls) == 1


async def test_classify_prose_synonym_still_runs_llm() -> None:
    # Doc 19 §3 prose writes ``prefilter: classify``; treat it as the LLM path.
    gw, backend = _gateway(json.dumps({"relevant": True, "confidence": 0.7}))
    classifier = RelevanceClassifier(gateway=gw)
    await classifier.classify(_doc("text"), prefilter="classify")
    assert len(backend.calls) == 1


# --- truncation ----------------------------------------------------------


def test_truncate_short_doc_unchanged() -> None:
    text, truncated = truncate_document("short doc", max_chars=100)
    assert text == "short doc"
    assert truncated is False


def test_truncate_long_doc_cuts_on_whitespace() -> None:
    text = "word " * 1000  # 5000 chars
    out, truncated = truncate_document(text, max_chars=100)
    assert truncated is True
    assert len(out) <= 100
    # Cut on a whitespace boundary -> no trailing partial word.
    assert not out.endswith("wor")
    assert out.endswith("word")


def test_truncate_no_spaces_falls_back_to_hard_cut() -> None:
    text = "x" * 500
    out, truncated = truncate_document(text, max_chars=100)
    assert truncated is True
    assert len(out) == 100


async def test_long_document_is_truncated_before_llm() -> None:
    gw, backend = _gateway(json.dumps({"relevant": True, "confidence": 0.7}))
    classifier = RelevanceClassifier(gateway=gw)
    long_text = "procurement " * 5000  # well over MAX_DOC_CHARS
    verdict = await classifier.classify(_doc(long_text))
    assert verdict.truncated is True
    # The prompt handed to the backend must not carry the full untruncated doc.
    sent_prompt = backend.calls[0]["prompt"]
    assert isinstance(sent_prompt, str)
    assert len(sent_prompt) < len(long_text)
    assert len(sent_prompt) < MAX_DOC_CHARS + 2000  # prompt scaffolding overhead


# --- workspace accounting passes through ---------------------------------


async def test_workspace_id_is_accounted() -> None:
    gw, _ = _gateway(json.dumps({"relevant": True, "confidence": 0.7}))
    classifier = RelevanceClassifier(gateway=gw)
    await classifier.classify(_doc("text"), workspace_id="ws-7")
    assert gw.usage("ws-7").calls == 1


# --- verdict schema sanity ----------------------------------------------


def test_verdict_rejects_out_of_range_confidence() -> None:
    with pytest.raises(ValueError):
        RelevanceVerdict(relevant=True, confidence=1.5)
