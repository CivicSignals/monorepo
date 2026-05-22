"""ingestion module — scraper orchestration, raw document storage.

Houses the headless-browser fetch path (``browser.py``, D2): a Playwright-backed
:class:`~civicsignals_api.modules.recipes.runner.Fetcher` + browser pool for the
``http_browser`` connector (doc 18 §1 cat. D, §5 wave 4). It runs only on
``worker_ingest`` and is invoked via the ``ingestion.browser_fetch`` task.
"""
