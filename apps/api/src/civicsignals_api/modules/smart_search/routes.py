"""HTTP endpoints for the smart_search module, mounted under `/api/v1/smart-search`."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header

from .schemas import RewriteRequest, RewriteResponse, RewriteUsage
from .services import QueryRewriter

router = APIRouter(prefix="/smart-search", tags=["smart-search"])


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
