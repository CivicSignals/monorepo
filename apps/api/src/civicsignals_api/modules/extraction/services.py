"""Public service interface for the extraction module.

Other modules call extraction only through the functions/classes re-exported
here — never by importing extraction's models or routes directly (doc 06 §3).

E8 lands the Stage-2 relevance gate (doc 19 §3): a cheap LLM that decides whether
a fetched document is worth running through full extraction.
"""

from __future__ import annotations

from .relevance import (
    PREFILTER_ASSUME_RELEVANT,
    PREFILTER_CLASSIFIER,
    RelevanceClassifier,
    truncate_document,
)
from .schemas import (
    RELEVANCE_CATEGORIES,
    DocumentRef,
    RelevanceDecisionRecord,
    RelevanceVerdict,
)

__all__ = [
    "PREFILTER_ASSUME_RELEVANT",
    "PREFILTER_CLASSIFIER",
    "RELEVANCE_CATEGORIES",
    "DocumentRef",
    "RelevanceClassifier",
    "RelevanceDecisionRecord",
    "RelevanceVerdict",
    "truncate_document",
]
