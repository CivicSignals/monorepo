"""Validate recipe configs against their *live* sources (a recipe "doctor").

Golden-fixture replay (``recipes.cli test``) proves a recipe still extracts the
fields its committed fixture promises — but a fixture is a frozen snapshot, so it
never catches a source whose live endpoint moved, whose dataset id was retired,
or whose markup drifted out from under the selectors. This script closes that
gap: for each recipe it runs the **real** connector lifecycle against the live
source — exactly the production crawl path (``connector.discover`` →
``fetch → extract → normalize``) minus DB/S3 persistence — and reports whether
the source still discovers pointers and yields canonical records.

It is deliberately cheap and safe to run ad hoc:

* **No DB, no S3, no Celery** — pure functions only (mirrors how the runner
  itself never touches the database).
* **No LLM spend** — a no-op LLM extractor is injected, so an ``llm_assisted``
  field simply misses instead of calling a vendor. (No shipped recipe uses
  ``llm_assisted`` today, but this keeps the harness honest if one starts.)
* **A no-op clock** skips the per-recipe politeness/jitter sleeps so a sweep of
  many recipes finishes in seconds, and ``--max-pointers`` caps how many items
  of a feed/listing we actually fetch so we never hammer a source.

Only connectors whose ``discover`` derives pointers from ``connector_config``
(rss, socrata, ckan, arcgis_rest, gdelt, usaspending, the bulk directories, the
meeting platforms) can be validated from the YAML alone — an ``http_static``
recipe's seed URLs live in the ingestion DB, not the file, so those report
``needs-seeds`` and are skipped unless you pass ``--seed-url``.

Usage::

    # Validate specific recipes
    python -m civicsignals_api.scripts.validate_recipes_live statescoop-news-rss ca-grants-portal

    # Validate every config-driven recipe under recipes/
    python -m civicsignals_api.scripts.validate_recipes_live --all

    # Machine-readable
    python -m civicsignals_api.scripts.validate_recipes_live --all --json

Exit code is the number of recipes that errored or produced zero records (so it
can gate a periodic "are our live sources still alive?" check), capped at 1.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

from civicsignals_api.modules.ingestion.connectors.base import (
    ConnectorError,
    connector_for,
)
from civicsignals_api.modules.recipes import services as recipes_services
from civicsignals_api.modules.recipes.runner import RecipeError


class _NoSleepClock:
    """Real ``monotonic`` so politeness *windows* still compute, but ``sleep`` is a
    no-op so a many-recipe sweep doesn't wait the recipe's 10s politeness per item.
    Validation fetches at most ``--max-pointers`` items, so this is polite enough.
    """

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        return None


class _NoLLM:
    """LLM-assisted fallback rung that always misses — keeps validation free of
    vendor calls. Matches the ``LLMFieldExtractor`` protocol (returns ``None``)."""

    def extract_field(
        self,
        *,
        field_name: str,
        text: str,
        recipe_id: str,
        prompt_name: str | None,
    ) -> str | None:
        return None


@dataclass
class RecipeResult:
    recipe_id: str
    connector: str
    status: str  # ok | degraded | no-records | needs-seeds | error
    discovered: int = 0
    fetched_ok: int = 0
    records: int = 0
    degraded_records: int = 0
    sample: dict[str, object] = field(default_factory=dict)
    error: str | None = None


def validate_recipe(
    recipe_id: str,
    *,
    max_pointers: int,
    seed_urls: Sequence[str] = (),
) -> RecipeResult:
    """Run one recipe's live discover→fetch→extract→normalize and summarise it."""
    try:
        recipe = recipes_services.load_recipe(recipe_id)
    except (RecipeError, OSError) as exc:
        return RecipeResult(recipe_id, "?", "error", error=f"load: {exc}")

    connector_name = recipe.connector
    try:
        connector = connector_for(recipe, clock=_NoSleepClock())
    except ConnectorError as exc:
        return RecipeResult(recipe_id, connector_name, "error", error=f"connector: {exc}")

    res = RecipeResult(recipe_id, connector_name, "ok")

    # discover() does the connector's own source-type pointer expansion (parse a
    # feed, walk a paginated API, …). http_static & friends pass seeds through, so
    # an empty discover means "this recipe needs seed URLs we don't have in the YAML".
    try:
        pointers = connector.discover(list(seed_urls))
    except Exception as exc:
        res.status = "error"
        res.error = f"discover: {type(exc).__name__}: {exc}"
        return res

    res.discovered = len(pointers)
    if not pointers:
        res.status = "needs-seeds" if not seed_urls else "no-records"
        res.error = "discover() returned no pointers"
        return res

    fetcher = connector.build_fetcher()
    runner = recipes_services.make_runner(
        recipe, fetcher, clock=_NoSleepClock(), llm_extractor=_NoLLM()
    )
    try:
        for pointer in list(pointers)[:max_pointers]:
            try:
                raw = runner.fetch(pointer)
            except Exception as exc:
                if res.error is None:
                    res.error = f"fetch {pointer.url}: {type(exc).__name__}: {exc}"
                continue
            res.fetched_ok += 1
            try:
                extracted = runner.extract(raw)
                records = runner.normalize(extracted, pointer.url)
            except Exception as exc:
                if res.error is None:
                    res.error = f"extract {pointer.url}: {type(exc).__name__}: {exc}"
                continue
            for rec in records:
                res.records += 1
                if rec.degraded:
                    res.degraded_records += 1
                if not res.sample:
                    res.sample = {
                        "title": rec.fields.get("title"),
                        "signal_types": rec.signal_types,
                        "degraded_fields": rec.degraded_fields,
                        "source_url": rec.source_url,
                    }
    finally:
        close = getattr(fetcher, "close", None)
        if callable(close):
            close()

    if res.records == 0:
        res.status = "no-records"
    elif res.degraded_records:
        res.status = "degraded"
    else:
        res.status = "ok"
    return res


def _all_recipe_ids() -> list[str]:
    return sorted(recipes_services.list_recipe_ids())


def _print_table(results: list[RecipeResult]) -> None:
    width = max((len(r.recipe_id) for r in results), default=10)
    icon = {"ok": "✓", "degraded": "~", "no-records": "✗", "needs-seeds": "·", "error": "✗"}
    print(f"{'':2}{'recipe':<{width}}  {'connector':<14} disc fetch recs  status")
    print("-" * (width + 44))
    for r in sorted(results, key=lambda x: (x.status, x.recipe_id)):
        print(
            f"{icon.get(r.status, '?'):2}{r.recipe_id:<{width}}  "
            f"{r.connector:<14} {r.discovered:>4} {r.fetched_ok:>5} {r.records:>4}  {r.status}"
        )
        if r.error and r.status in {"error", "no-records"}:
            print(f"    └─ {r.error}")
    ok = sum(1 for r in results if r.status in {"ok", "degraded"})
    print(
        f"\n{ok}/{len(results)} recipes produced records "
        f"({sum(1 for r in results if r.status == 'ok')} clean, "
        f"{sum(1 for r in results if r.status == 'degraded')} degraded)."
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "recipe_ids", nargs="*", help="recipe id(s); default with --all: every recipe"
    )
    parser.add_argument("--all", action="store_true", help="validate every recipe under recipes/")
    parser.add_argument(
        "--max-pointers", type=int, default=3, help="max items to fetch per recipe (default 3)"
    )
    parser.add_argument(
        "--seed-url",
        action="append",
        default=[],
        help="seed URL(s) for pass-through connectors (http_static)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = parser.parse_args(argv)

    ids = list(args.recipe_ids)
    if args.all:
        ids = _all_recipe_ids()
    if not ids:
        parser.error("pass recipe id(s) or --all")

    results: list[RecipeResult] = []
    for recipe_id in ids:
        if not args.json:
            print(f"… {recipe_id}", file=sys.stderr, flush=True)
        try:
            results.append(
                validate_recipe(recipe_id, max_pointers=args.max_pointers, seed_urls=args.seed_url)
            )
        except Exception:
            results.append(
                RecipeResult(recipe_id, "?", "error", error=traceback.format_exc(limit=2))
            )

    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2, ensure_ascii=False))
    else:
        _print_table(results)

    failures = sum(1 for r in results if r.status in {"error", "no-records"})
    return min(failures, 1)


if __name__ == "__main__":
    raise SystemExit(main())
