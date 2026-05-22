"""Signal embedding service (I1, doc 19 §4/§7.4, doc 14 §6.2).

Each extracted signal is embedded into the ``signals_signal.vector_embedding``
pgvector column at extraction time. The embedding powers two downstream features:

- **fuzzy dedupe** (doc 19 §7.4 / E10): an ANN query for near-duplicate signals
  of the same entity + type within the dedupe window;
- **smart-search / hybrid retrieval** (doc 14 §6.2, I3): cosine similarity between
  a signal and a query (or a workspace-keyword) embedding.

The embedding text is built from the feed-visible surface (``title`` + ``summary``)
plus a few high-signal structured fields, mirroring the keyword-scoring surface
(doc 14 §6.2). All access to the embeddings provider goes through the
:class:`~civicsignals_api.llm_gateway.LLMGateway` — **no module calls a vendor
embeddings SDK directly** (doc 06 §7).

Embedding is **best-effort** at extraction (doc 19 §12.1 resilience): a failure to
embed must never lose the signal — we log it and leave ``vector_embedding`` NULL so
the signal is still stored/served, and a later :func:`backfill_embeddings` pass
(or E10's fuzzy-dedupe path) fills it in.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.llm_gateway import LLMError, LLMGateway, get_gateway

from .models import EMBEDDING_DIM, Signal

log = structlog.get_logger(__name__)

# High-signal structured fields appended to the embedding text when present. These
# are the same dimensions keyword scoring (doc 14 §6.2) leans on — vendor names,
# categories, agencies, roles — so the vector captures them too. Kept short and
# string-coerced; lists are joined. Unknown/missing keys are simply skipped.
_EMBED_FIELD_KEYS: tuple[str, ...] = (
    "entity_name",
    "vendor_name",
    "category",
    "posting_agency",
    "approving_body",
    "role",
    "person_name",
    "products_categories",
    "raw_keywords",
)

# A signal with neither title nor summary should never reach here (the strict schema
# gate, signals.schemas, requires both), but guard anyway so a degraded/manual row
# never embeds an empty string.
_MAX_EMBED_CHARS = 8_000


class EmbeddingDimMismatchError(Exception):
    """The embeddings model returned a vector whose width != the column dim.

    A configuration error (the ``embedding_model`` / ``embedding_dim`` settings and
    the ``signals_signal.vector_embedding`` column must agree, doc 07 ``VECTOR(1536)``).
    Surfaced rather than silently truncating/padding, which would corrupt every ANN
    query. The pipeline's best-effort caller catches it and leaves the column NULL.
    """

    def __init__(self, got: int, expected: int) -> None:
        super().__init__(f"embedding dim {got} != column dim {expected}")
        self.got = got
        self.expected = expected


def build_embedding_text(
    *,
    title: str,
    summary: str,
    details: dict[str, object] | None = None,
) -> str:
    """Build the text embedded for a signal (doc 19 §7.4, doc 14 §6.2).

    ``title + " " + summary`` plus a few high-signal structured fields (vendor,
    category, agency, …) pulled from ``details`` when present. Deterministic field
    order (``_EMBED_FIELD_KEYS``) so the same signal always yields the same text →
    the same vector. Truncated to a bounded length to keep the embed call cheap.
    """
    parts: list[str] = []
    if title.strip():
        parts.append(title.strip())
    if summary.strip():
        parts.append(summary.strip())
    if details:
        for key in _EMBED_FIELD_KEYS:
            value = details.get(key)
            text = _coerce_field(value)
            if text:
                parts.append(text)
    return " ".join(parts)[:_MAX_EMBED_CHARS]


def _coerce_field(value: object) -> str:
    """Coerce a details field to a trimmed string (joining lists), or '' to skip."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return " ".join(_coerce_field(v) for v in value if v is not None).strip()
    return str(value).strip()


def embedding_text_for_signal(signal: Signal) -> str:
    """Build the embedding text for a persisted :class:`Signal` row."""
    return build_embedding_text(
        title=signal.title,
        summary=signal.summary,
        details=signal.details,
    )


async def embed_signals(
    session: AsyncSession,
    signal_ids: list[uuid.UUID],
    *,
    gateway: LLMGateway | None = None,
    workspace_id: str | None = None,
) -> int:
    """Compute + persist embeddings for the given signals (I1; the pipeline embed step).

    Loads each signal, builds its embedding text, embeds the whole batch in one
    gateway call (one round-trip, accounted under :data:`TASK_EMBED`), and writes the
    vector onto ``signals_signal.vector_embedding``. Returns the number of signals
    embedded. The caller owns the transaction (this flushes, not commits).

    **Best-effort** (doc 19 §12.1): a gateway failure or a dim mismatch is logged and
    swallowed — the column is left NULL and the signal is untouched, so the extraction
    is never lost. ``backfill_embeddings`` (or E10's fuzzy-dedupe pass) re-attempts.

    # TODO I1: a small retry queue for transient embed failures (the gateway already
    # retries within a call; this would re-enqueue a whole signal whose embed call
    # exhausted retries) instead of waiting for the next backfill sweep.
    """
    if not signal_ids:
        return 0
    gateway = gateway or get_gateway()

    rows = list(
        (await session.execute(select(Signal).where(Signal.id.in_(signal_ids)))).scalars().all()
    )
    rows = [r for r in rows if r.vector_embedding is None]
    if not rows:
        return 0

    texts = [embedding_text_for_signal(r) for r in rows]
    try:
        result = await gateway.embed(texts, workspace_id=workspace_id)
        if result.dim != EMBEDDING_DIM:
            raise EmbeddingDimMismatchError(result.dim, EMBEDDING_DIM)
    except (LLMError, EmbeddingDimMismatchError) as exc:
        # Best-effort: do not lose the signal on an embed failure / misconfig
        # (doc 19 §12.1). Leave the column NULL; backfill_embeddings re-attempts.
        log.warning(
            "signals.embed.failed",
            signal_ids=[str(r.id) for r in rows],
            error=str(exc),
        )
        return 0

    embedded = 0
    for row, vector in zip(rows, result.vectors, strict=True):
        row.vector_embedding = vector
        embedded += 1
    await session.flush()
    log.info("signals.embed.done", count=embedded, model=result.model)
    return embedded


async def backfill_embeddings(
    session: AsyncSession,
    *,
    gateway: LLMGateway | None = None,
    batch_size: int = 128,
    limit: int | None = None,
    workspace_id: str | None = None,
) -> int:
    """Embed existing signals whose ``vector_embedding`` is still NULL (I1).

    For signals stored before I1, for any whose extraction-time embed failed
    (best-effort left them NULL), and for an operator re-embed after a model change.
    Walks NULL-embedding signals in id order, embedding ``batch_size`` at a time, and
    returns the total embedded. The caller owns the transaction; this flushes per
    batch but does not commit, so a long backfill should be driven by a task that
    commits between batches (a scheduled sweep — TODO I1 beat task).

    ``limit`` caps the total embedded (a bounded one-shot run); ``None`` processes
    every NULL-embedding signal currently visible.
    """
    gateway = gateway or get_gateway()
    total = 0
    while True:
        remaining = None if limit is None else max(0, limit - total)
        if remaining == 0:
            break
        take = batch_size if remaining is None else min(batch_size, remaining)
        stmt = (
            select(Signal.id)
            .where(Signal.vector_embedding.is_(None))
            .order_by(Signal.id)
            .limit(take)
        )
        ids = list((await session.execute(stmt)).scalars().all())
        if not ids:
            break
        embedded = await embed_signals(session, ids, gateway=gateway, workspace_id=workspace_id)
        total += embedded
        # If a batch embedded nothing (e.g. every row failed best-effort), stop
        # rather than spin on the same NULL rows forever.
        if embedded == 0:
            break
    return total
