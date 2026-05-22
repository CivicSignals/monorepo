"""UUID v7 generation (doc 07 conventions: every ``id`` is a UUID v7).

UUIDv7 is time-ordered (millisecond Unix timestamp in the high bits) so primary
keys cluster by creation time — friendly to Postgres B-tree index locality and
cursor pagination. The stdlib only ships ``uuid7`` from Python 3.14, so this is
a small RFC 9562 §5.7 implementation usable on the 3.12 runtime.
"""

from __future__ import annotations

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    """Return a new time-ordered UUID version 7."""
    unix_ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")  # 80 random bits

    value = (unix_ms & 0xFFFFFFFFFFFF) << 80
    value |= 0x7 << 76  # version 7
    value |= (rand >> 64 & 0x0FFF) << 64  # rand_a (12 bits)
    value |= 0b10 << 62  # RFC 4122 variant
    value |= rand & 0x3FFFFFFFFFFFFFFF  # rand_b (62 bits)
    return uuid.UUID(int=value)
