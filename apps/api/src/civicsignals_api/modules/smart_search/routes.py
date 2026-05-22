"""HTTP endpoints for the smart_search module, mounted under `/api/v1/smart-search`.

  POST /smart-search/rewrite  — NL -> structured query rewrite (TODO I2)
  POST /smart-search          — hybrid retrieval: ranked signals (TODO I3, I4)

Hybrid retrieval (doc 14 §6.2) runs behind ``require_workspace``: the signal corpus
is global (doc 14 §4.2), but the search runs in a workspace context so the rewrite +
query-embed token usage is metered against the workspace and F3's per-workspace
scores can boost ranking later. The rewrite endpoint stays open (no workspace) so the
search box is usable before workspace plumbing — it only meters usage when a header
is present. Cursor pagination + RFC 7807 errors (doc 06 §5). ``api/v1.py`` already
imports and mounts this router — do not add it there.

I4: ``POST /smart-search`` now accepts ``summarize=true`` to request an optional
LLM synthesis of the top-N results (see :class:`~.services.ResultSummarizer`). The
LLM cost is metered against the workspace. ``summary`` is ``None`` when not requested,
when there are no results, or when the summarizer fails (graceful degradation).

I5: A per-workspace daily LLM call budget is enforced inside
:meth:`~.services.HybridRetriever.search`. When the workspace has reached its daily
cap the response falls back to keyword-only retrieval and includes
``budget_exhausted=True`` — the route itself is unchanged (no extra logic here).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.db import get_session
from civicsignals_api.modules.auth.dependencies import RequireViewer

from .schemas import (
    RewriteRequest,
    RewriteResponse,
    RewriteUsage,
    SmartSearchRequest,
    SmartSearchResponse,
)
from .services import HybridRetriever, QueryRewriter

router = APIRouter(prefix="/smart-search", tags=["smart-search"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _problem(status: int, title: str, detail: str) -> JSONResponse:
    """RFC 7807 ``application/problem+json`` response (doc 06 §5)."""
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={"type": "about:blank", "title": title, "status": status, "detail": detail},
    )


@router.post("/rewrite", response_model=RewriteResponse)
async def rewrite_query(
    body: RewriteRequest,
    # Workspace scoping header (doc 06 §5); optional here so the rewrite usable
    # before auth/workspace plumbing lands. When present, token usage is metered
    # against the workspace by the gateway.
    x_workspace_id: Annotated[str | None, Header(alias="X-Workspace-Id")] = None,
) -> RewriteResponse:
    """Rewrite a natural-language query into a structured filter + text query (TODO I2)."""
    rewriter = QueryRewriter()
    query, result = await rewriter.rewrite(body.query, workspace_id=x_workspace_id)
    usage = (
        RewriteUsage(
            provider=result.provider,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        if result is not None
        else None
    )
    return RewriteResponse(query=query, usage=usage)


@router.post("", response_model=SmartSearchResponse, summary="Hybrid signal search")
async def smart_search(
    body: SmartSearchRequest,
    session: SessionDep,
    # Hybrid retrieval runs in a workspace context (doc 14 §6.2): the corpus is
    # global but token usage is metered against the workspace and F3 scoring can
    # boost ranking later. ``RequireViewer`` = any member (a read).
    ctx: RequireViewer,
) -> SmartSearchResponse | JSONResponse:
    """Hybrid retrieval over the global signal corpus (I3) with optional summary (I4).

    NL ``query`` -> rewrite (I2) -> vector ANN + BM25 + structured-filter
    intersection, fused with weighted RRF -> a ranked, cursor-paginated page of
    signals with scores + which retrievers matched (doc 14 §6.2). Set
    ``summarize=true`` to receive a short NL synthesis of the top results; the
    summary is omitted (``null``) when there are no results or on LLM failure.
    """
    retriever = HybridRetriever()
    try:
        return await retriever.search(
            session,
            body.query,
            workspace_id=str(ctx.workspace_id),
            extra_filters=body.filters,
            top_n=body.top_n,
            candidate_limit=body.candidate_limit,
            weights=body.weights,
            cursor=body.cursor,
            summarize=body.summarize,
        )
    except ValueError:
        return _problem(400, "Invalid cursor", "The supplied cursor is malformed.")
