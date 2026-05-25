"""Stage-2 relevance classifier — the cheap LLM relevance gate (doc 19 §3, E8).

This is the funnel's largest cost lever: a small/fast (Haiku-class) LLM that
decides whether a fetched document plausibly contains a signal we care about,
dropping ~70-80% of documents before the expensive extraction stages run
(doc 19 §1, §3.1). Skipping this gate would roughly triple total LLM spend.

The classifier:

- routes through :class:`~civicsignals_api.llm_gateway.LLMGateway` (never a vendor
  SDK directly — doc 06 §7) using the ``classify`` task, which the gateway's
  :class:`TaskModelPolicy` maps to a Haiku-class model;
- honours the recipe-level ``prefilter: assume_relevant`` short-circuit (doc 19
  §3.3) for pre-vetted sources, skipping the LLM entirely (cost $0);
- keeps the prompt short and truncates long documents to a token budget so the
  per-call cost stays around ~$0.0002 (doc 19 §3.2, §11);
- returns a structured :class:`RelevanceVerdict` and, when a session is given,
  persists an ``extraction_relevance_decision`` row for retrospective FP/FN
  analysis (doc 19 §3.4).
"""

from __future__ import annotations

import json
from typing import Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.llm_gateway import TASK_CLASSIFY, LLMGateway, get_gateway

from .models import RelevanceDecision
from .schemas import (
    RELEVANCE_CATEGORIES,
    DocumentRef,
    RelevanceVerdict,
)

log = structlog.get_logger(__name__)

# Recipe prefilter values. The canonical recipe schema
# (packages/recipe-schema/schema/recipe.schema.json) restricts ``prefilter`` to the
# enum ``["classifier", "assume_relevant"]`` (doc 19 §3.3), so those are the only
# values a schema-validated recipe can carry. ``assume_relevant`` skips the gate
# for inherently-relevant sources (a state portal's RFP listing is literally a
# list of RFPs); ``classifier`` runs the LLM gate.
#
# This service is a layer *below* recipe validation: ``classify`` short-circuits
# only on ``assume_relevant`` and runs the classifier for every other value. So
# the prose synonym ``classify`` (used in doc 19 §3's YAML examples) and any other
# non-``assume_relevant`` string both route to the LLM — a defensive default that
# never silently drops a document, even if a caller passes an unvalidated value.
PREFILTER_ASSUME_RELEVANT: Final = "assume_relevant"
PREFILTER_CLASSIFIER: Final = "classifier"

# Method tags recorded on the decision row (doc 19 §3 provenance).
METHOD_CLASSIFIER: Final = "classifier"
METHOD_ASSUME_RELEVANT: Final = "assume_relevant"
METHOD_FALLBACK: Final = "fallback"

# Prompt identity. TODO E3: resolve this name+version through the prompt registry
# (the gateway already threads ``prompt_name``/``prompt_version`` through to the
# result). Until then we inline the default prompt below and pin a version here so
# decision rows already carry reproducible provenance.
PROMPT_NAME: Final = "relevance_classifier"
PROMPT_VERSION: Final = "v1"

# Truncation budget. The prompt asks for ~4000 tokens of document (doc 19 §3.2);
# at the gateway's ~4 chars/token heuristic that is ~16000 chars. We cut on a
# whitespace boundary near the budget so we don't split a word mid-token.
MAX_DOC_CHARS: Final = 16_000

# Confidence the LLM-skipping ``assume_relevant`` short-circuit reports. The
# source is pre-vetted, so the gate is certain it is relevant (doc 19 §3.3).
ASSUME_RELEVANT_CONFIDENCE: Final = 1.0

# A conservative confidence for the fallback verdict when the LLM output cannot
# be parsed. We fail *open* (treat as relevant) so a malformed classifier reply
# never silently drops a document — a false positive only wastes one extraction,
# a false negative loses a signal entirely (doc 19 §12.1 weights FN > FP).
FALLBACK_CONFIDENCE: Final = 0.5

_SYSTEM_PROMPT: Final = (
    "You are a classifier for a sales-intelligence tool that monitors "
    "public-sector buying activity. You read one document and decide whether it "
    "is worth deeper analysis. Be terse and output only JSON."
)

# Rendered as a clean comma-separated list for the prompt, rather than a Python
# list repr (``['procurement', ...]``), which is harder for the model to read.
_CATEGORY_LIST: Final = ", ".join(RELEVANCE_CATEGORIES)

# Inlined default prompt (doc 19 §3.2). TODO E3: move to the prompt registry under
# the name/version above. Kept short to hold the per-call cost near $0.0002.
_PROMPT_TEMPLATE: Final = """Does this document mention or discuss any of the following?
- A procurement (RFP, RFI, RFQ, bid, contract, vendor selection)
- A budget allocation or approval for a category of spending
- A grant received or applied for
- A personnel change in IT, technology, procurement, or executive leadership
- A new strategic plan, capital improvement plan, or technology roadmap
- A board/council meeting agenda item discussing one of the above

Output ONLY a JSON object of the form:
{{"relevant": true|false, "categories": [...], "confidence": 0.0-1.0, "reason": "short"}}
where categories is a subset of {categories}.

Source: {source}

Document:
<<<{document}>>>"""


def truncate_document(text: str, *, max_chars: int = MAX_DOC_CHARS) -> tuple[str, bool]:
    """Truncate ``text`` to a char budget, cutting on a whitespace boundary.

    Returns ``(text, truncated)``. Cutting on a whitespace boundary keeps the
    final token intact and avoids feeding a split word to the model. Any
    whitespace counts (space, newline, tab) — text and HTML-derived content is
    often newline-delimited, so a space-only search would split tokens.
    """
    if len(text) <= max_chars:
        return text, False
    cut = text[:max_chars]
    # Scan backwards for the last whitespace char of any kind.
    boundary = -1
    for i in range(len(cut) - 1, -1, -1):
        if cut[i].isspace():
            boundary = i
            break
    # Only honour the boundary if it isn't pathologically early (a document with
    # no whitespace in the budget shouldn't collapse to almost nothing).
    if boundary >= max_chars // 2:
        cut = cut[:boundary]
    return cut.rstrip(), True


def _build_prompt(doc: DocumentRef, truncated_text: str) -> str:
    return _PROMPT_TEMPLATE.format(
        categories=_CATEGORY_LIST,
        source=doc.source or doc.recipe_id,
        document=truncated_text,
    )


def _coerce_categories(raw: object) -> list[str]:
    """Keep only known category strings from the model's ``categories`` field."""
    if not isinstance(raw, list):
        return []
    known = set(RELEVANCE_CATEGORIES)
    return [c for c in raw if isinstance(c, str) and c in known]


def _parse_verdict_json(text: str) -> dict[str, object] | None:
    """Parse the model's reply into a dict, tolerating surrounding prose.

    Models occasionally wrap JSON in code fences or a sentence. We extract the
    first ``{...}`` span and parse it; on any failure we return ``None`` and the
    caller falls back to a fail-open verdict.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


class RelevanceClassifier:
    """The Stage-2 relevance gate (doc 19 §3).

    Inject an :class:`LLMGateway` (use the gateway's ``FakeBackend`` in tests); it
    defaults to the process-wide gateway. ``classify`` decides relevance for one
    document, honouring the recipe prefilter and optionally persisting the
    decision for retrospective analysis.
    """

    def __init__(
        self,
        *,
        gateway: LLMGateway | None = None,
        max_doc_chars: int = MAX_DOC_CHARS,
    ) -> None:
        self._gateway = gateway or get_gateway()
        self._max_doc_chars = max_doc_chars

    async def classify(
        self,
        doc: DocumentRef,
        *,
        prefilter: str = PREFILTER_CLASSIFIER,
        workspace_id: str | None = None,
        session: AsyncSession | None = None,
    ) -> RelevanceVerdict:
        """Decide whether ``doc`` is worth deeper analysis.

        ``prefilter`` is the recipe's relevance posture (doc 19 §3.3). When it is
        ``assume_relevant`` the LLM is skipped entirely (cost $0) and the document
        is treated as relevant. Otherwise the cheap classifier runs.

        If ``session`` is given, the verdict is recorded as an
        ``extraction_relevance_decision`` row (doc 19 §3.4); the caller commits.
        """
        if prefilter == PREFILTER_ASSUME_RELEVANT:
            verdict = self._assume_relevant_verdict()
        else:
            verdict = await self._classify_with_llm(doc, workspace_id=workspace_id)

        if session is not None:
            await self.record_decision(session, doc, verdict)
        return verdict

    def _assume_relevant_verdict(self) -> RelevanceVerdict:
        return RelevanceVerdict(
            relevant=True,
            confidence=ASSUME_RELEVANT_CONFIDENCE,
            categories=[],
            reason="recipe prefilter: assume_relevant (pre-vetted source)",
            method=METHOD_ASSUME_RELEVANT,
        )

    async def _classify_with_llm(
        self, doc: DocumentRef, *, workspace_id: str | None
    ) -> RelevanceVerdict:
        truncated_text, truncated = truncate_document(doc.text, max_chars=self._max_doc_chars)
        prompt = _build_prompt(doc, truncated_text)

        result = await self._gateway.complete(
            prompt=prompt,
            task=TASK_CLASSIFY,
            system=_SYSTEM_PROMPT,
            # Just enough for the constrained JSON verdict.
            max_tokens=256,
            workspace_id=workspace_id,
            prompt_name=PROMPT_NAME,
            prompt_version=PROMPT_VERSION,
        )

        parsed = _parse_verdict_json(result.text)
        if parsed is None:
            log.warning(
                "relevance.unparseable_verdict",
                raw_document_id=doc.raw_document_id,
                recipe_id=doc.recipe_id,
                model=result.model,
            )
            return RelevanceVerdict(
                relevant=True,
                confidence=FALLBACK_CONFIDENCE,
                categories=[],
                reason="classifier output was not valid JSON; failing open",
                method=METHOD_FALLBACK,
                model=result.model,
                prompt_name=result.prompt_name,
                prompt_version=result.prompt_version,
                truncated=truncated,
            )

        relevant = _coerce_relevant(parsed.get("relevant"))
        confidence = _clamp_confidence(parsed.get("confidence"))
        reason = parsed.get("reason")
        return RelevanceVerdict(
            relevant=relevant,
            confidence=confidence,
            categories=_coerce_categories(parsed.get("categories")),
            reason=reason if isinstance(reason, str) else None,
            method=METHOD_CLASSIFIER,
            model=result.model,
            prompt_name=result.prompt_name,
            prompt_version=result.prompt_version,
            truncated=truncated,
        )

    async def record_decision(
        self, session: AsyncSession, doc: DocumentRef, verdict: RelevanceVerdict
    ) -> RelevanceDecision:
        """Persist a relevance verdict (doc 19 §3.4).

        Public so the extraction pipeline can run the relevance LLM with no DB
        session (``classify(session=None)`` — keeping the network call off an open
        transaction under PgBouncer transaction-mode pooling, doc 06 §4) and then
        record the decision inside the same transaction that stores the candidates.
        Flushes (not commits) so the caller controls the transaction boundary.
        """
        row = RelevanceDecision(
            raw_document_id=doc.raw_document_id,
            recipe_id=doc.recipe_id,
            relevant=verdict.relevant,
            confidence=verdict.confidence,
            categories=list(verdict.categories),
            reason=verdict.reason,
            method=verdict.method,
            model=verdict.model,
            prompt_name=verdict.prompt_name,
            prompt_version=verdict.prompt_version,
        )
        session.add(row)
        # Flush (not commit) so the caller controls the transaction boundary; this
        # populates the autoincrement id without ending the unit of work.
        await session.flush()
        return row


_RELEVANT_TRUE_STRINGS: Final = frozenset({"true", "yes", "y", "1"})


def _coerce_relevant(raw: object) -> bool:
    """Coerce the model's ``relevant`` field to a bool, conservatively.

    Plain ``bool(raw)`` is wrong here: the string ``"false"`` is truthy and any
    non-zero number reads as ``True``, which would silently flip verdicts toward
    relevant and inflate false positives. The field is meant to be a JSON bool, so
    we accept real bools and the common string spellings; for the numeric forms a
    model might emit we accept only an exact ``1`` as relevant and treat every
    other value (``0``, ``0.2``, ``2``, …) as not-relevant.
    """
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int | float):
        return raw == 1
    if isinstance(raw, str):
        return raw.strip().lower() in _RELEVANT_TRUE_STRINGS
    return False


def _clamp_confidence(raw: object) -> float:
    """Coerce the model's confidence to a float in [0, 1]; default 0.5 if absent."""
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return FALLBACK_CONFIDENCE
    return max(0.0, min(1.0, value))
