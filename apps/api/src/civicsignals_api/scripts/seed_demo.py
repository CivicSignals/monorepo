"""Seed a demo workspace with synthetic signals (TODO A2).

Invoked by ``make seed`` (``docker compose run --rm api seed``), which dispatches
to this module's ``seed`` process type via ``docker-entrypoint.sh``. Outside the
stack, run it directly with ``python -m civicsignals_api.scripts.seed_demo``.
Gives a fresh developer real rows to look at the moment the stack is up.

Idempotent: safe to re-run. Every insert is guarded by a stable ``slug``/``key``
so re-running updates-or-skips rather than duplicating.

Why dedicated ``dev_seed_*`` tables instead of the real domain tables?
Most module models are still stubs (only ``A1`` is DONE), so the real
``accounts_workspace`` (TODO B5), ``accounts_user`` (TODO B1/B5),
``entities_entity`` (TODO C1) and ``signals_signal`` (TODO E4) tables do not
exist yet — there is nothing for Alembic to migrate. Rather than invent tables
owned by other modules' future work (which would collide with their migrations),
this seeder owns a small set of self-contained, dev-only ``dev_seed_*`` tables it
creates itself with ``CREATE TABLE IF NOT EXISTS``. They are not migrated by
Alembic and carry no foreign keys into module tables.

When the real models land, this script should switch to seeding through the
owning modules' ``services.py`` (never their internals — doc 06 §3):

- workspace + user  -> ``accounts`` services            (TODO B5, B1)
- entities          -> ``entities`` services            (TODO C1)
- signals           -> ``signals`` services             (TODO E4)

Until then the ``dev_seed_*`` tables are intentionally throwaway and can be
dropped wholesale once the seeder is rewritten.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import TypedDict

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from civicsignals_api.config import get_settings
from civicsignals_api.logging import configure_logging

logger = structlog.get_logger(__name__)


class _Signal(TypedDict):
    dedupe_key: str
    entity_slug: str
    signal_type: str
    title: str
    summary: str
    score: int
    age_days: int


# --- Demo fixtures -----------------------------------------------------------

DEMO_WORKSPACE = {
    "slug": "demo",
    "name": "Demo Workspace",
}

DEMO_USER = {
    "email": "demo@civicsignals.io",
    "name": "Demo Operator",
    "role": "admin",
}

# A handful of synthetic public-sector entities (TODO C1 will own the real ones).
DEMO_ENTITIES: list[dict[str, str]] = [
    {"slug": "tx-austin-isd", "name": "Austin Independent School District",
     "kind": "k12_district", "state": "TX"},
    {"slug": "ca-sac-county", "name": "Sacramento County", "kind": "county", "state": "CA"},
    {"slug": "wa-seattle-city", "name": "City of Seattle", "kind": "city", "state": "WA"},
]

# Synthetic signals across a few of the MVP signal types (doc 19). The
# ``entity_slug`` ties each to a demo entity above; ``dedupe_key`` keeps re-runs
# idempotent.
DEMO_SIGNALS: list[_Signal] = [
    {
        "dedupe_key": "tx-austin-isd:rfp_posted:erp-modernization-2026",
        "entity_slug": "tx-austin-isd",
        "signal_type": "rfp_posted",
        "title": "RFP: District-wide ERP modernization",
        "summary": "Austin ISD seeks vendors for a multi-year ERP replacement covering "
                   "finance, HR, and procurement.",
        "score": 88,
        "age_days": 2,
    },
    {
        "dedupe_key": "ca-sac-county:contract_expiring:fleet-telematics",
        "entity_slug": "ca-sac-county",
        "signal_type": "contract_expiring",
        "title": "Fleet telematics contract expiring in 120 days",
        "summary": "Sacramento County's incumbent fleet-tracking contract lapses in Q3; "
                   "renewal or rebid expected.",
        "score": 74,
        "age_days": 9,
    },
    {
        "dedupe_key": "wa-seattle-city:budget_published:2027-proposed",
        "entity_slug": "wa-seattle-city",
        "signal_type": "budget_published",
        "title": "Proposed 2027 budget published",
        "summary": "Seattle's proposed 2027 budget allocates new capital spending for "
                   "transportation and IT modernization.",
        "score": 61,
        "age_days": 21,
    },
    {
        "dedupe_key": "tx-austin-isd:leadership_change:new-cto",
        "entity_slug": "tx-austin-isd",
        "signal_type": "leadership_change",
        "title": "New Chief Technology Officer appointed",
        "summary": "Austin ISD names a new CTO who has signaled interest in cloud migration.",
        "score": 52,
        "age_days": 35,
    },
    {
        "dedupe_key": "wa-seattle-city:board_agenda:public-safety-tech",
        "entity_slug": "wa-seattle-city",
        "signal_type": "board_agenda",
        "title": "Council agenda: public-safety technology review",
        "summary": "Upcoming council session includes a review of public-safety technology "
                   "vendors and contracts.",
        "score": 47,
        "age_days": 4,
    },
]


# --- DDL (dev-only; not migrated by Alembic) ---------------------------------

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS dev_seed_workspace (
        id          BIGSERIAL PRIMARY KEY,
        slug        TEXT NOT NULL UNIQUE,
        name        TEXT NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dev_seed_user (
        id            BIGSERIAL PRIMARY KEY,
        workspace_id  BIGINT NOT NULL REFERENCES dev_seed_workspace(id) ON DELETE CASCADE,
        email         TEXT NOT NULL UNIQUE,
        name          TEXT NOT NULL,
        role          TEXT NOT NULL,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dev_seed_entity (
        id          BIGSERIAL PRIMARY KEY,
        slug        TEXT NOT NULL UNIQUE,
        name        TEXT NOT NULL,
        kind        TEXT NOT NULL,
        state       TEXT NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dev_seed_signal (
        id            BIGSERIAL PRIMARY KEY,
        workspace_id  BIGINT NOT NULL REFERENCES dev_seed_workspace(id) ON DELETE CASCADE,
        entity_id     BIGINT NOT NULL REFERENCES dev_seed_entity(id) ON DELETE CASCADE,
        dedupe_key    TEXT NOT NULL UNIQUE,
        signal_type   TEXT NOT NULL,
        title         TEXT NOT NULL,
        summary       TEXT NOT NULL,
        score         INTEGER NOT NULL,
        payload       JSONB NOT NULL DEFAULT '{}'::jsonb,
        published_at  TIMESTAMPTZ NOT NULL,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
]


async def _create_tables(conn: AsyncConnection) -> None:
    for stmt in _DDL:
        await conn.execute(text(stmt))


async def _upsert_workspace(conn: AsyncConnection) -> int:
    row = await conn.execute(
        text(
            """
            INSERT INTO dev_seed_workspace (slug, name)
            VALUES (:slug, :name)
            ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """
        ),
        DEMO_WORKSPACE,
    )
    return int(row.scalar_one())


async def _upsert_user(conn: AsyncConnection, workspace_id: int) -> None:
    await conn.execute(
        text(
            """
            INSERT INTO dev_seed_user (workspace_id, email, name, role)
            VALUES (:workspace_id, :email, :name, :role)
            ON CONFLICT (email) DO UPDATE
                SET name = EXCLUDED.name,
                    role = EXCLUDED.role,
                    workspace_id = EXCLUDED.workspace_id
            """
        ),
        {"workspace_id": workspace_id, **DEMO_USER},
    )


async def _upsert_entities(conn: AsyncConnection) -> dict[str, int]:
    slug_to_id: dict[str, int] = {}
    for entity in DEMO_ENTITIES:
        row = await conn.execute(
            text(
                """
                INSERT INTO dev_seed_entity (slug, name, kind, state)
                VALUES (:slug, :name, :kind, :state)
                ON CONFLICT (slug) DO UPDATE
                    SET name = EXCLUDED.name,
                        kind = EXCLUDED.kind,
                        state = EXCLUDED.state
                RETURNING id
                """
            ),
            entity,
        )
        slug_to_id[entity["slug"]] = int(row.scalar_one())
    return slug_to_id


async def _upsert_signals(
    conn: AsyncConnection, workspace_id: int, entity_ids: dict[str, int]
) -> int:
    now = datetime.now(UTC)
    count = 0
    for signal in DEMO_SIGNALS:
        entity_slug = signal["entity_slug"]
        entity_id = entity_ids.get(entity_slug)
        if entity_id is None:
            logger.warning("seed_demo.skip_signal_unknown_entity", entity_slug=entity_slug)
            continue
        published_at = now - timedelta(days=signal["age_days"])
        await conn.execute(
            text(
                """
                INSERT INTO dev_seed_signal (
                    workspace_id, entity_id, dedupe_key, signal_type,
                    title, summary, score, payload, published_at
                )
                VALUES (
                    :workspace_id, :entity_id, :dedupe_key, :signal_type,
                    :title, :summary, :score, CAST(:payload AS jsonb), :published_at
                )
                ON CONFLICT (dedupe_key) DO UPDATE
                    SET title = EXCLUDED.title,
                        summary = EXCLUDED.summary,
                        score = EXCLUDED.score,
                        published_at = EXCLUDED.published_at
                """
            ),
            {
                "workspace_id": workspace_id,
                "entity_id": entity_id,
                "dedupe_key": signal["dedupe_key"],
                "signal_type": signal["signal_type"],
                "title": signal["title"],
                "summary": signal["summary"],
                "score": signal["score"],
                "payload": json.dumps({"source": "seed_demo", "synthetic": True}),
                "published_at": published_at,
            },
        )
        count += 1
    return count


async def seed() -> None:
    """Create dev-only demo tables and idempotently upsert demo data."""
    settings = get_settings()
    # Seeding is DDL + a batch of writes; use the direct (non-PgBouncer) URL when
    # available so we get a stable session (doc 06 §4, doc 18 §6.3).
    db_url = settings.database_direct_url or settings.database_url
    engine = create_async_engine(db_url, echo=False)
    try:
        async with engine.begin() as conn:
            await _create_tables(conn)
            workspace_id = await _upsert_workspace(conn)
            await _upsert_user(conn, workspace_id)
            entity_ids = await _upsert_entities(conn)
            signal_count = await _upsert_signals(conn, workspace_id, entity_ids)
        logger.info(
            "seed_demo.done",
            workspace=DEMO_WORKSPACE["slug"],
            user=DEMO_USER["email"],
            entities=len(entity_ids),
            signals=signal_count,
        )
    finally:
        await engine.dispose()


def main() -> None:
    configure_logging(get_settings().log_level)
    asyncio.run(seed())


if __name__ == "__main__":
    main()
