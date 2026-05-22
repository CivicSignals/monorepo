"""UUID v7 generation for entity primary keys (doc 07 conventions).

doc 07 mandates UUID **v7** ids (time-ordered) for every table. Python's stdlib
``uuid`` gains ``uuid7`` only in 3.14; on 3.12 we generate a spec-compliant v7
value ourselves (RFC 9562 §5.7: 48-bit Unix-ms timestamp, 4-bit version, 12-bit
random, 2-bit variant, 62-bit random).

Kept module-local on purpose: it's a tiny self-contained helper and the entities
module is the first to need it. If a second module needs UUID v7 ids, promote
this to a shared package-root ``ids.py`` (cross-module utilities are allowed at
the seam layer, doc 06 §3) rather than importing it from here.
"""

from __future__ import annotations

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    """Return a time-ordered RFC 9562 UUID v7."""
    unix_ms = time.time_ns() // 1_000_000
    rand = os.urandom(10)

    # 48-bit big-endian millisecond timestamp.
    value = unix_ms & 0xFFFFFFFFFFFF
    # version (4 bits) = 0b0111, then 12 bits of randomness.
    value = (value << 16) | (0x7000 | (rand[0] << 4) | (rand[1] >> 4))
    # variant (2 bits) = 0b10, then 62 bits of randomness.
    tail = int.from_bytes(rand[2:], "big") & 0x3FFFFFFFFFFFFFFF
    value = (value << 64) | (0x8000000000000000 | tail)
    return uuid.UUID(int=value)
