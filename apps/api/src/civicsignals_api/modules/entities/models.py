"""entities SQLAlchemy models (doc 07 §2 "entities", doc 16 §4/§5/§6, doc 14).

The entity directory is the **global** account universe other modules resolve
signals/contacts against — it is *not* workspace-scoped (doc 07 §3). Three tables,
all prefixed ``entities_`` and migrated only by this module (doc 06 §3, §4):

- ``entities_kind`` — taxonomy of entity kinds (K-12 district, city, county,
  university, special district, …). Doc 07 models ``type`` as an inline enum on
  the entity; we normalize it into a small lookup table so the taxonomy is
  query-/seed-/UI-stable and extensible without a migration per new kind, while
  keeping the canonical ``type`` slug on the entity for the fast ICP pre-filter
  (doc 14 §6.1: ``entity_kinds @> ARRAY['k12_district']``).
- ``entities_geo`` — geography rows keyed by FIPS codes (state/county/place),
  the normalization layer doc 16 §12c calls foundational for dedupe + matching.
- ``entities_entity`` — the government/education org itself, with a hierarchical
  ``parent_id`` self-FK (school → district → state) and the identifier columns
  (NCES LEAID, IPEDS UnitID, Census GID) that seeding UPSERTs on.

Indexes target the cheap ICP candidate pre-filter (doc 14 §6.1) and name search
(doc 07 §2 ships a trigram GIN index on ``name``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Connection,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from civicsignals_api.db import Base

from .ids import uuid7

# Canonical entity-type slugs (doc 07 §2 enum). Stored as the ``type`` column on
# every entity (the value the ICP pre-filter matches on) and as the natural key
# of ``entities_kind``. Kept in sync via a CHECK constraint + the kind seed.
ENTITY_TYPES: tuple[str, ...] = (
    "state",
    "county",
    "city",
    "town",
    "village",
    "school_district",
    "school",
    "university",
    "community_college",
    "special_district",
    "agency",
    "authority",
    "library_system",
    "transit",
)

ENTITY_STATUSES: tuple[str, ...] = ("active", "dissolved", "merged")

# Geography levels we model (doc 16 §12c FIPS scheme: state → county → place).
GEO_LEVELS: tuple[str, ...] = ("state", "county", "place")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)


class EntityKind(Base):
    """Taxonomy row for one kind of public-sector entity (doc 07 §2 enum).

    ``slug`` is the canonical type token (e.g. ``school_district``) shared with
    ``entities_entity.type``; ``category`` groups kinds for UI faceting
    (``k12`` / ``higher_ed`` / ``local_gov`` / ``state_gov`` / ``special``).
    """

    __tablename__ = "entities_kind"

    id: Mapped[uuid.UUID] = _uuid_pk()
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    entities: Mapped[list[Entity]] = relationship(back_populates="kind")

    __table_args__ = (
        CheckConstraint(
            "slug IN (" + ", ".join(f"'{t}'" for t in ENTITY_TYPES) + ")",
            name="entities_kind_slug_check",
        ),
        Index("entities_kind_category_idx", "category"),
    )


class Geo(Base):
    """A geography row (state, county, or place) keyed by FIPS (doc 16 §12c).

    ``geo_id`` is the fully-qualified FIPS code (state ``53``, county ``53033``,
    place ``5363000``) and is the stable natural key seeding UPSERTs on.
    ``parent_geo_id`` links place → county → state for rollups.
    """

    __tablename__ = "entities_geo"

    id: Mapped[uuid.UUID] = _uuid_pk()
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    geo_id: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False, server_default=text("'US'"))
    state: Mapped[str | None] = mapped_column(String(2))
    state_fips: Mapped[str | None] = mapped_column(String(2))
    county_fips: Mapped[str | None] = mapped_column(String(5))
    place_fips: Mapped[str | None] = mapped_column(String(7))
    parent_geo_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities_geo.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    parent: Mapped[Geo | None] = relationship(remote_side="Geo.id", back_populates="children")
    children: Mapped[list[Geo]] = relationship(back_populates="parent")
    entities: Mapped[list[Entity]] = relationship(back_populates="geo")

    __table_args__ = (
        CheckConstraint(
            "level IN (" + ", ".join(f"'{lvl}'" for lvl in GEO_LEVELS) + ")",
            name="entities_geo_level_check",
        ),
        Index("entities_geo_state_idx", "state"),
        Index("entities_geo_level_state_idx", "level", "state"),
    )


class Entity(Base):
    """A public-sector organization — the global account universe (doc 07 §2).

    Hierarchy via ``parent_id`` (school → district → state). ``type`` is the
    canonical kind slug duplicated from ``kind.slug`` for the fast ICP pre-filter
    (doc 14 §6.1); ``kind_id`` is the FK into the taxonomy. Stable external
    identifiers (NCES LEAID, IPEDS UnitID, Census GID) live in dedicated columns
    so seeding can UPSERT on them idempotently (doc 16 §12c).
    """

    __tablename__ = "entities_entity"

    id: Mapped[uuid.UUID] = _uuid_pk()
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'active'"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    short_name: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str] = mapped_column(String(2), nullable=False, server_default=text("'US'"))
    state: Mapped[str | None] = mapped_column(String(2))
    region: Mapped[str | None] = mapped_column(Text)

    kind_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities_kind.id", ondelete="RESTRICT")
    )
    geo_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities_geo.id", ondelete="SET NULL")
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities_entity.id", ondelete="SET NULL")
    )

    # External identifiers used as natural keys by the seed loaders (doc 16 §12c).
    nces_leaid: Mapped[str | None] = mapped_column(String(16), unique=True)
    ipeds_unitid: Mapped[str | None] = mapped_column(String(16), unique=True)
    census_gid: Mapped[str | None] = mapped_column(String(32), unique=True)

    population: Mapped[int | None] = mapped_column(Integer)
    enrollment: Mapped[int | None] = mapped_column(Integer)
    annual_budget_usd: Mapped[float | None] = mapped_column(Numeric(15, 2))
    primary_website: Mapped[str | None] = mapped_column(Text)
    procurement_portal_url: Mapped[str | None] = mapped_column(Text)
    board_meeting_cadence: Mapped[str | None] = mapped_column(Text)

    attributes: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    source_urls: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    kind: Mapped[EntityKind | None] = relationship(back_populates="entities")
    geo: Mapped[Geo | None] = relationship(back_populates="entities")
    parent: Mapped[Entity | None] = relationship(remote_side="Entity.id", back_populates="children")
    children: Mapped[list[Entity]] = relationship(back_populates="parent")

    __table_args__ = (
        CheckConstraint(
            "type IN (" + ", ".join(f"'{t}'" for t in ENTITY_TYPES) + ")",
            name="entities_entity_type_check",
        ),
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in ENTITY_STATUSES) + ")",
            name="entities_entity_status_check",
        ),
        # ICP candidate pre-filter (doc 14 §6.1): state + type set-intersection.
        Index("entities_state_type_idx", "state", "type"),
        Index("entities_kind_fk_idx", "kind_id"),
        Index("entities_geo_fk_idx", "geo_id"),
        Index("entities_parent_idx", "parent_id"),
        # Trigram name search (doc 07 §2). Needs the pg_trgm extension +
        # gin_trgm_ops opclass; the extension is ensured by the before_create
        # DDL hook below (so both Alembic *and* metadata.create_all install it).
        Index(
            "entities_name_trgm_idx",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )


# The trigram GIN index above needs pg_trgm's ``gin_trgm_ops`` opclass. Ensuring
# the extension in ``entities_entity``'s ``before_create`` makes the table
# self-sufficient under *any* creation path — the Alembic migration, and
# ``Base.metadata.create_all`` (used by other modules' DB-backed test fixtures).
# ``IF NOT EXISTS`` keeps it idempotent; the dialect guard skips non-Postgres
# backends (none today) so metadata operations there don't error.
@event.listens_for(Entity.__table__, "before_create")
def _ensure_pg_trgm(target: object, connection: Connection, **kw: object) -> None:
    if connection.dialect.name == "postgresql":
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
