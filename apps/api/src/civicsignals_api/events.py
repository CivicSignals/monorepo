"""In-process event bus for the modulith (doc 06 §8, doc 18 §6.5).

Subscribers live in their own modules. We deliberately avoid Kafka/NATS in the
MVP — Postgres LISTEN/NOTIFY plus Celery is sufficient. When scale forces a
module out to its own service, this bus is what we replace, not the database.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

# Well-known event names. Add as modules emit them.
SIGNAL_CREATED = "signal.created"
SIGNAL_SCORED = "signal.scored"
SIGNAL_PUSHED = "signal.pushed"
MEMBER_INVITED = "member.invited"
MEMBER_JOINED = "member.joined"
# B3: password reset audit events; B9 persists these via the admin listener.
AUTH_PASSWORD_RESET_REQUESTED = "auth.password_reset.requested"
AUTH_PASSWORD_RESET_COMPLETED = "auth.password_reset.completed"
# B9: additional auth audit events persisted by the admin listener.
AUTH_LOGIN = "auth.login"
AUTH_LOGOUT = "auth.logout"
MEMBER_ROLE_CHANGED = "member.role_changed"
# K1: integration connection lifecycle; B9 persists these via the admin listener.
INTEGRATION_CONNECTION_CREATED = "integration.connection.created"
INTEGRATION_CONNECTION_DELETED = "integration.connection.deleted"
INTEGRATION_PUSH_FAILED = "integration.push.failed"
# K2: emitted after every push attempt (success or failure) is recorded.
INTEGRATION_PUSH_RECORDED = "integration.push.recorded"
# F6: emitted after an ICP is created, updated (patched), or its active state
# changes so the backfill listener can kick off the sync+async rescore.
ICP_CHANGED = "icp.changed"

Handler = Callable[[dict[str, Any]], Awaitable[None]]

_subscribers: dict[str, list[Handler]] = defaultdict(list)


def subscribe(event: str, handler: Handler) -> None:
    _subscribers[event].append(handler)


def unsubscribe(event: str, handler: Handler) -> None:
    """Remove all registrations of ``handler`` for ``event`` (idempotent).

    If the same handler was registered more than once, all copies are removed so
    the caller can call this once without regard to the registration count.
    """
    handlers = _subscribers.get(event)
    if handlers:
        _subscribers[event] = [h for h in handlers if h is not handler]


async def publish(event: str, payload: dict[str, Any]) -> None:
    for handler in _subscribers.get(event, []):
        await handler(payload)
