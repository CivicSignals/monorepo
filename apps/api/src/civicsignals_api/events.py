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

Handler = Callable[[dict[str, Any]], Awaitable[None]]

_subscribers: dict[str, list[Handler]] = defaultdict(list)


def subscribe(event: str, handler: Handler) -> None:
    _subscribers[event].append(handler)


async def publish(event: str, payload: dict[str, Any]) -> None:
    for handler in _subscribers.get(event, []):
        await handler(payload)
