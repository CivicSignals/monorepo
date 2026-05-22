"""Ingestion delegates crawl runs to the recipes runner (TODO D1/D4)."""

from __future__ import annotations

from civicsignals_api.modules.ingestion import services as ingestion_services


class _Fetcher:
    def fetch(
        self, url: str, *, user_agent: str, max_redirects: int
    ) -> tuple[int, str, dict[str, str]]:
        return 200, '<html><body><h1 class="solicitation-title">X</h1></body></html>', {}

    def robots_txt(self, url: str, *, user_agent: str) -> str | None:
        return None


def test_crawl_recipe_delegates_to_runner() -> None:
    records = ingestion_services.crawl_recipe(
        "wa-state-webs",
        _Fetcher(),
        ["https://webs.des.wa.gov/rfp-1"],
    )
    assert len(records) == 1
    assert records[0].recipe_id == "wa-state-webs"
    assert records[0].recipe_version == 1
