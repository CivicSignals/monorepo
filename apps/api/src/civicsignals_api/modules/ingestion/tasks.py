"""Celery tasks for the ingestion module (doc 06 §8, doc 18 §6)."""

from __future__ import annotations

from civicsignals_api.celery_app import celery_app


@celery_app.task(name="ingestion.crawl_recipe")
def crawl_recipe(recipe_id: str) -> None:
    """Run one recipe's discover -> fetch cycle (TODO D4). Per-recipe cadence.

    The recipe DSL + runner exist (TODO D1, recipes module). Wiring this task to
    select a connector fetcher, persist raw docs to S3, and call
    ``ingestion.services.crawl_recipe`` is TODO D4/D6.
    """
