"""Entity directory seeding framework (C1; doc 16 §4 NCES, §5 IPEDS, §6 Census).

Loads the **account universe** from the three foundational public-domain
directories doc 16 lists as Tier 1:

- **NCES Common Core of Data** (doc 16 §4) — every public K-12 district/school.
- **Census of Governments** (doc 16 §6) — ~90k state/local governments.
- **IPEDS** (doc 16 §5) — ~6k higher-ed institutions.

Design (per C1 requirements):

- Each source is a :class:`Loader` that reads tabular rows from a *file or URL*
  and maps them onto entity/geo/kind UPSERTs through :mod:`.services` (the
  cross-module seam — seeding never writes models directly elsewhere). The same
  loader code that reads the small committed sample fixtures points at the full
  multi-GB downloads by passing the real file path/URL (see ``FULL DATASET``
  TODOs on each loader) — we deliberately ship only a handful of real rows per
  source so the repo and CI stay small (doc 16 §16: the directories are
  static bulk data).
- Seeding is **idempotent**: every UPSERT keys on a stable natural key (NCES
  LEAID / IPEDS UnitID / Census GID, doc 16 §12c), so re-running converges to
  the same row counts.
- **Hierarchy** (doc 07 §1, C1 req 3): a state government is the parent of the
  districts/colleges/local-govs in that state. We resolve the parent by state
  FIPS after all rows are loaded, so source ordering does not matter.

Run it standalone with ``python -m civicsignals_api.modules.entities.seeding``
(reads ``DATABASE_DIRECT_URL``), or call :func:`seed_entities` from a script.
"""

from __future__ import annotations

import asyncio
import csv
import io
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.config import get_settings
from civicsignals_api.logging import configure_logging

from . import services
from .models import Entity, EntityKind, Geo

logger = structlog.get_logger(__name__)

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Canonical kind taxonomy seeded into ``entities_kind`` (doc 07 §2 enum, grouped
# into UI-facing categories). ``slug`` must be one of models.ENTITY_TYPES.
KIND_SEED: tuple[tuple[str, str, str, str], ...] = (
    ("state", "State", "state_gov", "A US state government."),
    ("county", "County", "local_gov", "A county / parish government."),
    ("city", "City", "local_gov", "A municipal (city) government."),
    ("town", "Town", "local_gov", "A town government."),
    ("village", "Village", "local_gov", "A village government."),
    ("school_district", "K-12 School District", "k12", "A public K-12 local education agency."),
    ("school", "School", "k12", "An individual public school within a district."),
    ("university", "University", "higher_ed", "A 4-year public university."),
    ("community_college", "Community College", "higher_ed", "A 2-year community college."),
    ("special_district", "Special District", "special", "A single-purpose special district."),
    ("agency", "Agency", "state_gov", "A government agency."),
    ("authority", "Authority", "special", "A public authority."),
    ("library_system", "Library System", "special", "A public library system."),
    ("transit", "Transit Authority", "special", "A public transit agency."),
)

# US state FIPS -> (USPS abbr, full name). Trimmed to the states our sample
# fixtures touch; extend (or load from the FIPS download, doc 16 §12c) for full
# coverage. Used to synthesize the parent state-government rows.
STATE_FIPS: dict[str, tuple[str, str]] = {
    "06": ("CA", "California"),
    "41": ("OR", "Oregon"),
    "48": ("TX", "Texas"),
    "53": ("WA", "Washington"),
}


@dataclass(slots=True)
class SeedStats:
    """Per-run counters (returned so callers/tests can assert convergence)."""

    kinds: int = 0
    geos: int = 0
    entities: int = 0
    by_source: dict[str, int] = field(default_factory=dict)


# --- Row source: file or URL ------------------------------------------------


def read_rows(source: str | Path) -> Iterator[dict[str, str]]:
    """Yield CSV rows (as dicts) from a local path or an ``http(s)://`` URL.

    This is the single seam that makes a loader point at either the committed
    sample fixture or the real full dataset (doc 16 §4/§5/§6) — only the
    ``source`` argument changes.
    """
    text = _fetch_text(source)
    yield from csv.DictReader(io.StringIO(text))


def _fetch_text(source: str | Path) -> str:
    s = str(source)
    if s.startswith(("http://", "https://")):
        # Identified UA per doc 18 §legal posture.
        req = urllib.request.Request(s, headers={"User-Agent": "CivicSignalsBot/1.0"})
        with urllib.request.urlopen(req) as resp:
            data: bytes = resp.read()
        return data.decode("utf-8")
    return Path(source).read_text(encoding="utf-8")


# --- Loaders ----------------------------------------------------------------


async def load_kinds(session: AsyncSession) -> int:
    """Seed the ``entities_kind`` taxonomy (idempotent UPSERT on slug)."""
    for slug, label, category, description in KIND_SEED:
        await services.upsert_kind(
            session, slug=slug, label=label, category=category, description=description
        )
    return len(KIND_SEED)


async def _ensure_state(session: AsyncSession, state_fips: str) -> Entity | None:
    """Idempotently create (and return) the state government + its geo row.

    States are the hierarchy roots (doc 07 §1). Synthesized from :data:`STATE_FIPS`
    rather than a separate source row, with a stable ``census_gid`` natural key.
    """
    info = STATE_FIPS.get(state_fips)
    if info is None:
        return None
    abbr, name = info
    await services.upsert_geo(
        session,
        geo_id=state_fips,
        level="state",
        name=name,
        state=abbr,
        state_fips=state_fips,
    )
    geo = (await session.execute(select(Geo).where(Geo.geo_id == state_fips))).scalar_one()
    return await services.upsert_entity(
        session,
        natural_key="census_gid",
        census_gid=f"{state_fips}-00000-1000-0001",
        type="state",
        name=f"State of {name}",
        state=abbr,
        geo_id=geo.id,
        attributes={"state_fips": state_fips},
    )


async def load_nces(session: AsyncSession, source: str | Path) -> int:
    """Load NCES CCD K-12 districts (doc 16 §4), keyed on LEAID.

    FULL DATASET: pass the CCD district directory file from
    https://nces.ed.gov/ccd/ccddata.asp (the ``ccd_lea_029_*`` LEA universe).
    Column names below match that layout; trim/extend the mapping as the file
    version changes.
    """
    count = 0
    for row in read_rows(source):
        leaid = (row.get("LEAID") or "").strip()
        if not leaid:
            continue
        state = (row.get("STABBR") or row.get("LSTATE") or "").strip().upper() or None
        county_fips = (row.get("CONUM") or "").strip() or None
        enrollment = _to_int(row.get("TOTAL_STUDENTS"))
        await services.upsert_entity(
            session,
            natural_key="nces_leaid",
            nces_leaid=leaid,
            type="school_district",
            name=(row.get("LEA_NAME") or "").strip(),
            state=state,
            enrollment=enrollment,
            primary_website=_clean_url(row.get("WEBSITE")),
            attributes={
                "ncesid": leaid,
                "fips_state": (row.get("FIPST") or "").strip(),
                "fips_county": county_fips,
                "source": "nces_ccd",
            },
            source_urls=[s for s in [_clean_url(row.get("WEBSITE"))] if s],
        )
        count += 1
    return count


async def load_ipeds(session: AsyncSession, source: str | Path) -> int:
    """Load IPEDS higher-ed institutions (doc 16 §5), keyed on UnitID.

    SECTOR/CONTROL distinguish 2-year (community college) from 4-year (university);
    we only seed public institutions here. FULL DATASET: pass the IPEDS HD
    (institutional characteristics directory) CSV from
    https://nces.ed.gov/ipeds/use-the-data.
    """
    count = 0
    for row in read_rows(source):
        unitid = (row.get("UNITID") or "").strip()
        if not unitid:
            continue
        sector = (row.get("SECTOR") or "").strip()
        # IPEDS SECTOR: 1=public 4yr, 4=public 2yr (doc 16 §5).
        kind_type = "community_college" if sector == "4" else "university"
        state = (row.get("STABBR") or "").strip().upper() or None
        await services.upsert_entity(
            session,
            natural_key="ipeds_unitid",
            ipeds_unitid=unitid,
            type=kind_type,
            name=(row.get("INSTNM") or "").strip(),
            state=state,
            enrollment=_to_int(row.get("EFTOTAL")),
            primary_website=_clean_url(row.get("WEBADDR")),
            attributes={
                "ipeds_unitid": unitid,
                "fips_state": (row.get("FIPS") or "").strip(),
                "fips_county": (row.get("COUNTYCD") or "").strip() or None,
                "sector": sector,
                "source": "ipeds",
            },
            source_urls=[s for s in [_clean_url(row.get("WEBADDR"))] if s],
        )
        count += 1
    return count


async def load_census_gov(session: AsyncSession, source: str | Path) -> int:
    """Load Census of Governments local govs (doc 16 §6), keyed on GID.

    Skips ``state``-type rows: those are synthesized as hierarchy roots by
    :func:`_ensure_state` so every entity gets the same canonical parent. FULL
    DATASET: pass the Census of Governments organization file (the
    ~90k-row Government Units file) from the Census Bureau bulk download.
    """
    count = 0
    for row in read_rows(source):
        gid = (row.get("GID") or "").strip()
        govt_type = (row.get("GOVT_TYPE") or "").strip()
        if not gid or govt_type == "state":
            continue
        state = (row.get("STABBR") or "").strip().upper() or None
        geo_id = await _resolve_local_geo(session, row)
        await services.upsert_entity(
            session,
            natural_key="census_gid",
            census_gid=gid,
            type=govt_type or "agency",
            name=(row.get("GOVT_NAME") or "").strip(),
            state=state,
            geo_id=geo_id,
            population=_to_int(row.get("POPULATION")),
            primary_website=_clean_url(row.get("WEBSITE")),
            attributes={
                "census_gid": gid,
                "state_fips": (row.get("STATE_FIPS") or "").strip(),
                "county_fips": (row.get("COUNTY_FIPS") or "").strip() or None,
                "place_fips": (row.get("PLACE_FIPS") or "").strip() or None,
                "source": "census_gov",
            },
            source_urls=[s for s in [_clean_url(row.get("WEBSITE"))] if s],
        )
        count += 1
    return count


async def _resolve_local_geo(session: AsyncSession, row: dict[str, str]) -> object | None:
    """Upsert and return the geo (place > county) row for a local-gov row."""
    state_fips = (row.get("STATE_FIPS") or "").strip()
    county_fips = (row.get("COUNTY_FIPS") or "").strip()
    place_fips = (row.get("PLACE_FIPS") or "").strip()
    state = (row.get("STABBR") or "").strip().upper() or None
    name = (row.get("GOVT_NAME") or "").strip()
    if place_fips:
        await services.upsert_geo(
            session,
            geo_id=place_fips,
            level="place",
            name=name,
            state=state,
            state_fips=state_fips or None,
            county_fips=county_fips or None,
            place_fips=place_fips,
        )
        geo = (await session.execute(select(Geo).where(Geo.geo_id == place_fips))).scalar_one()
        return geo.id
    if county_fips:
        await services.upsert_geo(
            session,
            geo_id=county_fips,
            level="county",
            name=name,
            state=state,
            state_fips=state_fips or None,
            county_fips=county_fips,
        )
        geo = (await session.execute(select(Geo).where(Geo.geo_id == county_fips))).scalar_one()
        return geo.id
    return None


async def link_hierarchy(session: AsyncSession) -> None:
    """Set ``parent_id`` for every entity to its state government (doc 07 §1).

    Idempotent: a no-op on re-run once parents are already set correctly. Run
    after all sources load so ordering is irrelevant.
    """
    for state_fips, (abbr, _name) in STATE_FIPS.items():
        state_entity = await _ensure_state(session, state_fips)
        if state_entity is None:
            continue
        await session.execute(
            update(Entity)
            .where(
                Entity.state == abbr,
                Entity.type != "state",
                Entity.id != state_entity.id,
            )
            .values(parent_id=state_entity.id)
        )


async def link_kinds(session: AsyncSession) -> None:
    """Set ``kind_id`` for every entity from its ``type`` slug (doc 07 §2).

    The loaders write the canonical ``type`` slug (the fast ICP pre-filter
    column, doc 14 §6.1); this resolves the FK into ``entities_kind`` so the
    normalized taxonomy joins work. Idempotent: a no-op once linked. Run after
    the taxonomy is seeded.
    """
    kinds = (await session.execute(select(EntityKind))).scalars().all()
    for kind in kinds:
        await session.execute(
            update(Entity)
            .where(Entity.type == kind.slug, Entity.kind_id.is_distinct_from(kind.id))
            .values(kind_id=kind.id)
        )


# --- Orchestrator -----------------------------------------------------------


async def seed_entities(
    session: AsyncSession,
    *,
    nces_source: str | Path | None = None,
    ipeds_source: str | Path | None = None,
    census_source: str | Path | None = None,
) -> SeedStats:
    """Seed kinds, geos, entities, and hierarchy in one idempotent pass.

    Sources default to the committed sample fixtures; pass real file paths/URLs
    to load the full directories (doc 16 §4/§5/§6).
    """
    nces_source = nces_source or FIXTURES_DIR / "nces_ccd_sample.csv"
    ipeds_source = ipeds_source or FIXTURES_DIR / "ipeds_hd_sample.csv"
    census_source = census_source or FIXTURES_DIR / "census_gov_sample.csv"

    stats = SeedStats()
    stats.kinds = await load_kinds(session)

    # Pre-create the state roots so local-gov / district rows can resolve a
    # parent regardless of source ordering.
    for state_fips in STATE_FIPS:
        await _ensure_state(session, state_fips)

    stats.by_source["nces"] = await load_nces(session, nces_source)
    stats.by_source["ipeds"] = await load_ipeds(session, ipeds_source)
    stats.by_source["census_gov"] = await load_census_gov(session, census_source)

    await link_kinds(session)
    await link_hierarchy(session)

    stats.entities = int(
        (await session.execute(select(func.count()).select_from(Entity))).scalar_one()
    )
    stats.geos = int((await session.execute(select(func.count()).select_from(Geo))).scalar_one())
    logger.info(
        "entities.seed.done",
        kinds=stats.kinds,
        geos=stats.geos,
        entities=stats.entities,
        by_source=stats.by_source,
    )
    return stats


# --- Helpers ----------------------------------------------------------------


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    value = value.strip().replace(",", "")
    if not value:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _clean_url(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    return value


async def _async_main() -> None:
    settings = get_settings()
    db_url = settings.database_direct_url or settings.database_url
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(db_url, echo=False)
    try:
        async with AsyncSession(engine) as session, session.begin():
            await seed_entities(session)
    finally:
        await engine.dispose()


def main() -> None:
    configure_logging(get_settings().log_level)
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
