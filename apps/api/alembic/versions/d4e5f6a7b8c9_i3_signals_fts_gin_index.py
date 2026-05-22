"""I3 signals_signal full-text (GIN tsvector) index for hybrid retrieval

Adds the GIN index over ``to_tsvector('english', title || ' ' || summary)`` on
``signals_signal`` that the BM25 / full-text leg of hybrid retrieval (doc 14 §6.2,
TODO I3) ranks against. Hybrid retrieval fuses three retrievers — vector ANN (the
I1 ivfflat index), BM25 full-text (this index), and structured-filter intersection
— so the FTS query (``websearch_to_tsquery`` + ``ts_rank``) needs a functional GIN
index to be fast instead of a per-query full table scan.

Index choice
------------
- A **functional** (expression) index on ``to_tsvector('english', title || ' ' ||
  summary)`` — no extra column. ``title`` and ``summary`` are both NOT NULL
  (signals.schemas requires them), but ``coalesce`` is used defensively so a future
  nullable surface never makes the whole vector NULL.
- ``english`` text-search config: the corpus is US public-sector English (doc 19
  §2.3 is US-only at MVP; non-English content is translated upstream). Stemming +
  stop-word removal improves recall vs the ``simple`` config.
- The service builds the *same* expression (``func.to_tsvector("english",
  func.concat(title, " ", summary))``) so the planner uses this index. The two must
  stay in lockstep: changing the expression here requires the matching change in
  ``smart_search.services._bm25`` (and a re-index).

GIN over GiST: GIN is the standard choice for static-ish text search columns — far
faster lookups, slower writes, which is the right trade for a read-heavy signal
corpus (signals are written once by the funnel, queried many times). Built
non-CONCURRENTLY because Alembic wraps each migration in a transaction; an online
rebuild on a hot table is an ops runbook step, not a schema migration.

Owned by the ``signals`` module's table but added by I3 (the smart_search consumer)
because it exists solely for hybrid retrieval — mirroring how I1 added the ivfflat
index for the same reason. No new column, no app-model change.

Revision ID: d4e5f6a7b8c9
Revises: 479ad775f192
Create Date: 2026-05-22 12:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "479ad775f192"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "signals_fts_gin_idx"
# Must match smart_search.services._bm25's to_tsvector expression exactly so the
# planner uses this index for the BM25 leg of hybrid retrieval.
_TSVECTOR_EXPR = "to_tsvector('english', coalesce(title, '') || ' ' || coalesce(summary, ''))"


def upgrade() -> None:
    op.execute(f"CREATE INDEX {INDEX_NAME} ON signals_signal USING gin ({_TSVECTOR_EXPR})")


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
