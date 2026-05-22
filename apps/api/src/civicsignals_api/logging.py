"""Structured JSON logging to stdout (doc 06 §9).

Each request log carries ``request_id``, ``workspace_id``, ``user_id`` once the
auth + request-context middleware lands (TODO B1, B5). Raw scraped bodies, raw
LLM prompts with customer ICP text, and contact PII are never logged at INFO.
"""

from __future__ import annotations

import logging

import structlog


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(format="%(message)s", level=getattr(logging, level.upper(), logging.INFO))
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )
