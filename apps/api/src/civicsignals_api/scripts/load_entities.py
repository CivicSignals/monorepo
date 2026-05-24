"""Load the entity directory — the **account universe** — from the foundational
public-domain datasets (C1; doc 16 §4 NCES, §5 IPEDS, §6 Census of Governments).

This is the operator-facing CLI that the per-source loaders in
:mod:`civicsignals_api.modules.entities.seeding` are designed to back. The same
loader code reads either the tiny committed sample fixtures *or* the full
multi-GB public downloads — only the ``--path-or-url`` argument changes (the
seeding ``read_rows`` seam accepts a local path **or** an ``http(s)://`` URL).

Usage (inside the API container, or with the venv active locally)::

    # Default: load all three sources from the committed sample fixtures
    # (offline-safe — a few real rows per source so the repo/CI stay small).
    python -m civicsignals_api.scripts.load_entities --source all

    # Load the FULL NCES CCD LEA universe from a local download.
    python -m civicsignals_api.scripts.load_entities \\
        --source nces --path-or-url /data/ccd_lea_029_2223_w_1a_071223.csv

    # Or stream a source straight from a URL.
    python -m civicsignals_api.scripts.load_entities \\
        --source census --path-or-url https://example.org/CenGovUnits.csv

Full dataset sources (download the relevant CSV first; do NOT point this at a
multi-GB URL inside CI):

  - NCES CCD (K-12 districts):  https://nces.ed.gov/ccd/ccddata.asp
        the ``ccd_lea_*`` LEA (local education agency) universe file.
  - IPEDS HD (higher-ed):       https://nces.ed.gov/ipeds/use-the-data
        the HD (institutional characteristics directory) CSV.
  - Census of Governments:      https://www.census.gov/programs-surveys/cog.html
        the Government Units organization file (~90k rows).

Idempotent: every row UPSERTs on a stable natural key (NCES LEAID / IPEDS UnitID
/ Census GID), so re-running converges to the same row counts (C1 req, doc 16
§12c). Re-run any time the directories publish a new vintage.

Connects via ``DATABASE_DIRECT_URL`` (bypasses PgBouncer; the same long-job
pattern as Alembic + ``seed_admin``, doc 06 §4), falling back to ``DATABASE_URL``.

SPDX-License-Identifier: AGPL-3.0-only
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from civicsignals_api.config import get_settings
from civicsignals_api.logging import configure_logging
from civicsignals_api.modules.entities import seeding

logger = structlog.get_logger(__name__)

SOURCES = ("nces", "ipeds", "census", "all")

# Documented full-dataset download pages, surfaced in --help so an operator
# knows where to fetch the real files (doc 16 §4/§5/§6).
_SOURCE_HELP = (
    "Which directory to load: "
    "'nces' (CCD K-12 LEA universe, https://nces.ed.gov/ccd/ccddata.asp), "
    "'ipeds' (HD higher-ed directory, https://nces.ed.gov/ipeds/use-the-data), "
    "'census' (Census of Governments units file, "
    "https://www.census.gov/programs-surveys/cog.html), "
    "or 'all'. Default: all."
)


async def _load(source: str, path_or_url: str | None) -> seeding.SeedStats:
    """Run the seeding loaders for ``source`` against the configured DB.

    Routes ``path_or_url`` to the matching per-source argument of
    :func:`seeding.seed_entities`; every source not selected falls back to its
    committed sample fixture (so ``--source nces`` with a real file still leaves
    IPEDS/Census on the samples and ``link_kinds``/``link_hierarchy`` run once
    over the whole pass). Kinds + state roots are always (idempotently) ensured.
    """
    settings = get_settings()
    db_url = settings.database_direct_url or settings.database_url

    kwargs: dict[str, str | Path] = {}
    if path_or_url is not None:
        if source == "all":
            raise SystemExit(
                "load_entities: --path-or-url is per-source; pick a single "
                "--source (nces|ipeds|census) when passing a file/URL, or omit "
                "--path-or-url with --source all to use the sample fixtures."
            )
        kwargs[f"{source}_source"] = path_or_url

    engine = create_async_engine(db_url, echo=False)
    try:
        async with AsyncSession(engine) as session, session.begin():
            stats = await seeding.seed_entities(session, **kwargs)
    finally:
        await engine.dispose()
    return stats


def _print_summary(source: str, path_or_url: str | None, stats: seeding.SeedStats) -> None:
    where = path_or_url or "(sample fixtures)"
    print(
        f"load_entities: source={source} from={where} -> "
        f"kinds={stats.kinds} geos={stats.geos} entities={stats.entities} "
        f"by_source={stats.by_source}",
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = argparse.ArgumentParser(
        prog="python -m civicsignals_api.scripts.load_entities",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--source", choices=SOURCES, default="all", help=_SOURCE_HELP)
    parser.add_argument(
        "--path-or-url",
        default=None,
        help=(
            "Local CSV path OR an http(s):// URL for the full dataset of the "
            "chosen --source. Omit to load the committed sample fixture "
            "(offline-safe). Not valid with --source all."
        ),
    )
    args = parser.parse_args(argv)

    configure_logging(get_settings().log_level)

    settings = get_settings()
    if not (settings.database_direct_url or settings.database_url):
        print(
            "load_entities: no DATABASE_DIRECT_URL / DATABASE_URL configured.",
            file=sys.stderr,
            flush=True,
        )
        return 1

    stats = asyncio.run(_load(args.source, args.path_or_url))
    _print_summary(args.source, args.path_or_url, stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
