"""RFC 7807 ``application/problem+json`` error helpers (doc 08 §1.7).

Every API error is returned as a Problem Details object. Modules raise
:class:`ProblemException` (directly or via the convenience constructors) and the
app-level handler installed by :func:`install_problem_handlers` serializes it
with the ``application/problem+json`` content type. The handler also maps
FastAPI's own ``HTTPException`` and request-validation errors into the same
shape so the public surface is uniformly Problem-formatted.

The ``type`` URI points at the published error reference
(``https://docs.civicsignals.io/errors/<code>``) per doc 08 §1.7. A short,
stable ``code`` slug is the machine-readable key SDKs branch on.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_CONTENT_TYPE = "application/problem+json"
_ERROR_DOCS_BASE = "https://docs.civicsignals.io/errors"


class ProblemException(Exception):
    """An error that serializes to an RFC 7807 Problem Details response.

    ``code`` is a stable machine-readable slug (e.g. ``invalid_credentials``)
    and also forms the ``type`` URI. ``errors`` is the optional per-field list
    used for validation problems (doc 08 §1.7).
    """

    def __init__(
        self,
        *,
        status: int,
        code: str,
        title: str,
        detail: str | None = None,
        errors: list[dict[str, str]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail or title)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.errors = errors
        self.headers = headers

    def to_dict(self, instance: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": f"{_ERROR_DOCS_BASE}/{self.code}",
            "title": self.title,
            "status": self.status,
        }
        if self.detail is not None:
            body["detail"] = self.detail
        if instance is not None:
            body["instance"] = instance
        if self.errors:
            body["errors"] = self.errors
        return body


def _problem_response(exc: ProblemException, instance: str | None) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content=exc.to_dict(instance),
        media_type=PROBLEM_CONTENT_TYPE,
        headers=exc.headers,
    )


def install_problem_handlers(app: FastAPI) -> None:
    """Register handlers that render errors as ``application/problem+json``."""

    @app.exception_handler(ProblemException)
    async def _handle_problem(request: Request, exc: ProblemException) -> JSONResponse:
        return _problem_response(exc, str(request.url.path))

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else None
        headers = dict(exc.headers) if exc.headers else None
        problem = ProblemException(
            status=exc.status_code,
            code=_DEFAULT_CODES.get(exc.status_code, "error"),
            title=_DEFAULT_TITLES.get(exc.status_code, "Error"),
            detail=detail,
            headers=headers,
        )
        return _problem_response(problem, str(request.url.path))

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {
                "field": ".".join(str(p) for p in err.get("loc", []) if p != "body"),
                "code": str(err.get("type", "invalid")),
                "message": str(err.get("msg", "")),
            }
            for err in exc.errors()
        ]
        problem = ProblemException(
            status=422,
            code="validation",
            title="Validation failed",
            detail="The request body failed validation.",
            errors=errors,
        )
        return _problem_response(problem, str(request.url.path))


_DEFAULT_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    410: "gone",
    422: "validation",
    429: "rate_limited",
    500: "internal_error",
    503: "degraded",
}

_DEFAULT_TITLES: dict[int, str] = {
    400: "Bad request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not found",
    409: "Conflict",
    410: "Gone",
    422: "Validation failed",
    429: "Rate limited",
    500: "Internal server error",
    503: "Service degraded",
}
