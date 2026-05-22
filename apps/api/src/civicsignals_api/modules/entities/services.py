"""Public service interface for the entities module (doc 06 §3, doc 07 §2/§3).

Other modules call entities only through the functions defined here — never by
importing entities's models or routes directly (doc 06 §3). The directory is the
**global** account universe (doc 07 §3); these functions take an explicit
``AsyncSession`` (no implicit workspace scoping — entities are not
workspace-scoped) and back the public read API plus the cross-module callers
listed in C1 (contacts C2, scoring F3, dedupe E5, pipeline D3).

Reads:
- :func:`get_entity` — by id.
- :func:`search_entities` — name/kind/geo/type/state filters, **cursor**-paginated
  (doc 06 §5 — keyset on the time-ordered UUID v7 id, never offset).
- :func:`list_children` — hierarchy descent (district → schools, state → districts).
- :func:`resolve_entity_by_identifier` — natural-key lookup (NCES LEAID / IPEDS
  UnitID / Census GID) used by ingestion/dedupe to attach signals to an entity.

Writes (used by the seed loaders in :mod:`.seeding`, idempotent):
- :func:`upsert_kind` / :func:`upsert_geo` / :func:`upsert_entity`.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Entity, EntityKind, Geo

# Cursor pagination defaults (doc 06 §5). Hard cap keeps an unbounded ``limit``
# from scanning the whole directory.
DEFAULT_LIMIT = 25
MAX_LIMIT = 100

# Identifier schemes that map to a unique column on ``entities_entity``
# (doc 16 §12c). ``resolve_entity_by_identifier`` dispatches on these.
IDENTIFIER_COLUMNS = {
    "nces_leaid": Entity.nces_leaid,
    "ipeds_unitid": Entity.ipeds_unitid,
    "census_gid": Entity.census_gid,
}


# --- Cursor helpers ---------------------------------------------------------
# The cursor is just the last row's id (UUID v7 → already time-ordered, so a
# single keyset column gives a stable total order). Base64 keeps it opaque per
# doc 08 (clients must treat it as a token, not parse it).


def encode_cursor(entity_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(entity_id.bytes).decode("ascii")


def decode_cursor(cursor: str) -> uuid.UUID:
    try:
        return uuid.UUID(bytes=base64.urlsafe_b64decode(cursor.encode("ascii")))
    except (binascii.Error, ValueError) as exc:  # malformed token
        raise ValueError("invalid cursor") from exc


@dataclass(slots=True)
class EntityFilters:
    """Structured filters for :func:`search_entities` (doc 14 §6.1 dimensions)."""

    q: str | None = None
    types: Sequence[str] = ()
    kind_slugs: Sequence[str] = ()
    states: Sequence[str] = ()
    geo_id: uuid.UUID | None = None
    status: str | None = None
    parent_id: uuid.UUID | None = None


@dataclass(slots=True)
class EntityPage:
    """A page of entities plus the cursor for the next page (``None`` = last)."""

    items: list[Entity]
    next_cursor: str | None


# --- Reads ------------------------------------------------------------------


async def get_entity(session: AsyncSession, entity_id: uuid.UUID) -> Entity | None:
    """Fetch one entity by id, or ``None`` if it does not exist."""
    return await session.get(Entity, entity_id)


async def resolve_entity_by_identifier(
    session: AsyncSession, scheme: str, value: str
) -> Entity | None:
    """Look up an entity by an external natural key (doc 16 §12c).

    ``scheme`` is one of :data:`IDENTIFIER_COLUMNS` (``nces_leaid`` /
    ``ipeds_unitid`` / ``census_gid``). Used by ingestion/dedupe (E5) to attach
    a freshly-extracted signal to the right entity.
    """
    column = IDENTIFIER_COLUMNS.get(scheme)
    if column is None:
        raise ValueError(f"unknown identifier scheme: {scheme!r}")
    result = await session.execute(select(Entity).where(column == value))
    return result.scalar_one_or_none()


def _apply_filters(stmt: Select[tuple[Entity]], filters: EntityFilters) -> Select[tuple[Entity]]:
    if filters.q:
        # ILIKE on name/short_name; the trigram GIN index (doc 07 §2) keeps this
        # cheap. Escape LIKE wildcards in user input so they're literal.
        needle = filters.q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{needle}%"
        stmt = stmt.where(
            Entity.name.ilike(pattern, escape="\\") | Entity.short_name.ilike(pattern, escape="\\")
        )
    if filters.types:
        stmt = stmt.where(Entity.type.in_(list(filters.types)))
    if filters.kind_slugs:
        stmt = stmt.where(
            Entity.kind_id.in_(
                select(EntityKind.id).where(EntityKind.slug.in_(list(filters.kind_slugs)))
            )
        )
    if filters.states:
        stmt = stmt.where(Entity.state.in_([s.upper() for s in filters.states]))
    if filters.geo_id is not None:
        stmt = stmt.where(Entity.geo_id == filters.geo_id)
    if filters.status is not None:
        stmt = stmt.where(Entity.status == filters.status)
    if filters.parent_id is not None:
        stmt = stmt.where(Entity.parent_id == filters.parent_id)
    return stmt


async def search_entities(
    session: AsyncSession,
    filters: EntityFilters | None = None,
    *,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> EntityPage:
    """Cursor-paginated entity search by name/kind/geo/type/state (doc 14 §6.1).

    Ordered by id (UUID v7, time-ordered) so the keyset cursor gives a stable
    total order. Fetches ``limit + 1`` rows to decide whether a next page exists.
    """
    filters = filters or EntityFilters()
    limit = max(1, min(limit, MAX_LIMIT))

    stmt: Select[tuple[Entity]] = select(Entity)
    stmt = _apply_filters(stmt, filters)
    if cursor is not None:
        stmt = stmt.where(Entity.id > decode_cursor(cursor))
    stmt = stmt.order_by(Entity.id).limit(limit + 1)

    rows = list((await session.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = encode_cursor(items[-1].id) if has_more and items else None
    return EntityPage(items=items, next_cursor=next_cursor)


async def list_children(
    session: AsyncSession,
    parent_id: uuid.UUID,
    *,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> EntityPage:
    """List the direct children of an entity (district → schools, state → districts)."""
    return await search_entities(
        session, EntityFilters(parent_id=parent_id), cursor=cursor, limit=limit
    )


# --- Writes (idempotent UPSERTs used by the seed loaders) -------------------


async def upsert_kind(
    session: AsyncSession,
    *,
    slug: str,
    label: str,
    category: str,
    description: str | None = None,
) -> EntityKind:
    """Insert-or-update a taxonomy kind, keyed on ``slug`` (idempotent)."""
    stmt = (
        pg_insert(EntityKind)
        .values(slug=slug, label=label, category=category, description=description)
        .on_conflict_do_update(
            index_elements=[EntityKind.slug],
            set_={"label": label, "category": category, "description": description},
        )
        .returning(EntityKind)
    )
    result = await session.execute(stmt)
    return result.scalar_one()


async def upsert_geo(session: AsyncSession, **values: object) -> Geo:
    """Insert-or-update a geography row, keyed on ``geo_id`` (FIPS, idempotent)."""
    update_cols = {k: v for k, v in values.items() if k != "geo_id"}
    stmt = (
        pg_insert(Geo)
        .values(**values)
        .on_conflict_do_update(index_elements=[Geo.geo_id], set_=update_cols)
        .returning(Geo)
    )
    result = await session.execute(stmt)
    return result.scalar_one()


async def upsert_entity(session: AsyncSession, *, natural_key: str, **values: object) -> Entity:
    """Insert-or-update an entity, keyed on a unique external identifier.

    ``natural_key`` names the unique column to UPSERT on — one of
    :data:`IDENTIFIER_COLUMNS` (``nces_leaid`` / ``ipeds_unitid`` /
    ``census_gid``). The matching value must be present in ``values``. This is
    what makes seeding idempotent: re-running the loaders updates rows in place
    rather than duplicating them (C1 requirement, doc 16 §12c).
    """
    column = IDENTIFIER_COLUMNS.get(natural_key)
    if column is None:
        raise ValueError(f"unknown natural key: {natural_key!r}")
    if values.get(natural_key) in (None, ""):
        raise ValueError(f"upsert_entity requires a non-empty {natural_key}")

    # Never overwrite an existing id/created_at on conflict; everything else the
    # loader supplies is refreshed.
    update_cols = {k: v for k, v in values.items() if k not in {natural_key, "id", "created_at"}}
    stmt = (
        pg_insert(Entity)
        .values(**values)
        .on_conflict_do_update(index_elements=[column], set_=update_cols)
        .returning(Entity)
    )
    result = await session.execute(stmt)
    return result.scalar_one()
