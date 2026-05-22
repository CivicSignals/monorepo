"""I1 signals_signal.vector_embedding ivfflat index

Adds the ANN index over ``signals_signal.vector_embedding`` that E4 intentionally
deferred to I1 ("needs data to tune ``lists``; the column is present + nullable so
I1 needs no schema change"). This is I1's **only** schema change — the pgvector
column + the ``vector`` extension already exist (E4, ``0c0db3588693``); I1 only
populates the column (the embed step in the extraction pipeline) and adds this index
so fuzzy dedupe (doc 19 §7.4 / E10) + hybrid retrieval (doc 14 §6.2 / I3) get fast
nearest-neighbour lookups instead of a full scan.

Index choice
------------
- ``ivfflat`` with ``vector_cosine_ops``: the embeddings are L2-normalised and all
  downstream similarity is cosine (doc 19 §7.4 "within 0.92 cosine similarity"), so
  cosine ops is the right operator class. (We pick ivfflat over hnsw for the MVP:
  ivfflat builds far faster + uses far less memory, acceptable for ~5M signals; hnsw
  is a v2 swap if recall/latency demands it — a follow-up migration, no app change.)
- ``lists = 100``: ivfflat partitions the vectors into ``lists`` clusters; a query
  scans ``probes`` of them. pgvector's rule of thumb is ``rows / 1000`` up to ~1M
  rows. At MVP scale the live, embedded-and-not-yet-archived working set is well
  under 100k rows (doc 19 §1: ~3-5k new signals/day, fuzzy dedupe only queries a
  per-entity/type window), so 100 lists keeps each list usefully populated without
  over-partitioning a small table. This is a starting value to be re-tuned against
  real data (set ``ivfflat.probes`` at query time to trade recall vs latency); a
  re-tune is a cheap REINDEX/recreate, not an app change.

ivfflat indexes are best built on a populated table (an empty table builds a
mistuned index), but creating it on an empty/lightly-populated table is harmless —
it simply gets rebuilt naturally as data lands or via the documented re-tune. We
create it here (not CONCURRENTLY) because Alembic runs each migration in a
transaction; an online rebuild on a hot table is an ops runbook step, not a schema
migration.

Revision ID: c1d2e3f4a5b6
Revises: 0a3f28b23f5d
Create Date: 2026-05-22 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: str | None = "0a3f28b23f5d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ivfflat partition count — see module docstring for the rationale.
IVFFLAT_LISTS = 100
INDEX_NAME = "signals_vector_embedding_ivfflat_idx"


def upgrade() -> None:
    op.execute(
        f"CREATE INDEX {INDEX_NAME} ON signals_signal "
        f"USING ivfflat (vector_embedding vector_cosine_ops) "
        f"WITH (lists = {IVFFLAT_LISTS})"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
