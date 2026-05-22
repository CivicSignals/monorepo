"""Public service interface for the ingestion module.

Other modules call ingestion only through the functions defined here — never by
importing ingestion's models or routes directly (doc 06 §3).

Ingestion orchestrates *crawl runs*: it owns scheduling, the pending-document
queue, and raw-document storage (TODO D3/D4). The recipe **DSL + runner** live
in the recipes module (TODO D1) — ingestion drives them through that module's
``services.py`` rather than reaching into the runner internals (the cross-module
seam, doc 06 §3). The concrete connector fetchers it injects are TODO D6.
"""

from __future__ import annotations

from collections.abc import Sequence

from civicsignals_api.modules.recipes import services as recipes_services
from civicsignals_api.modules.recipes.services import (
    CanonicalRecord,
    Fetcher,
)


def crawl_recipe(
    recipe_id: str,
    fetcher: Fetcher,
    seed_urls: Sequence[str],
) -> list[CanonicalRecord]:
    """Run one recipe's full lifecycle (doc 18 §2; TODO D4 schedules this).

    Thin delegation to the recipes module's runner. The ``fetcher`` is supplied
    by the connector selected for the recipe (TODO D6); raw-document persistence
    to S3 wraps the fetch step in TODO D3.
    """
    return recipes_services.run_recipe(recipe_id, fetcher, seed_urls)
