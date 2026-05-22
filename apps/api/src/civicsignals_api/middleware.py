"""Cross-cutting HTTP middleware (A6).

Middleware registered here:

``RequestContextMiddleware``
    Generates / propagates a ``request_id`` UUID (honouring inbound
    ``X-Request-Id``), binds ``request_id``, ``workspace_id`` (from the
    ``X-Workspace-Id`` header), and ``user_id`` (TODO B1 - auth module) into
    structlog contextvars so every JSON log line emitted during the request
    automatically carries those fields.  The ``X-Request-Id`` is echoed back on
    every response.

Prometheus metrics middleware is wired directly in ``main.py`` via the
``starlette-prometheus`` instrumentator so it can use the ``/metrics`` route.
"""

from __future__ import annotations

import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

logger = structlog.get_logger(__name__)

_REQUEST_ID_HEADER = "X-Request-Id"
_WORKSPACE_ID_HEADER = "X-Workspace-Id"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind ``request_id``, ``workspace_id``, and ``user_id`` into structlog contextvars.

    * **request_id**: taken from the inbound ``X-Request-Id`` header if present;
      otherwise a fresh UUID4 is generated.
    * **workspace_id**: taken from the inbound ``X-Workspace-Id`` header if present;
      ``None`` otherwise.
    * **user_id**: ``None`` until B1 (auth) is merged - the auth dependency will
      bind it via ``structlog.contextvars.bind_contextvars(user_id=...)`` after
      token validation.  See ``# TODO B1`` below.

    The middleware clears contextvars before and after each request so fields
    never leak between requests in the same worker thread/coroutine.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Reset any stale bindings from a previous request on this worker.
        structlog.contextvars.clear_contextvars()

        request_id = request.headers.get(_REQUEST_ID_HEADER) or str(uuid.uuid4())
        workspace_id: str | None = request.headers.get(_WORKSPACE_ID_HEADER)

        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            workspace_id=workspace_id,
            # TODO B1: replace None with the resolved user_id once the auth
            # module (B1) exposes a lightweight synchronous accessor that can run
            # here (e.g. decode the JWT payload without a DB hit).  For now the
            # auth dependency in each route will call
            # structlog.contextvars.bind_contextvars(user_id=current_user.id)
            # after successful token validation.
            user_id=None,
        )

        response: Response = await call_next(request)
        response.headers[_REQUEST_ID_HEADER] = request_id
        return response
