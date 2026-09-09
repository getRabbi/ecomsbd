"""Identifier generation.

Master spec section 70: primary keys are UUIDv7 where supported, otherwise UUID.
UUIDv7 is time-ordered, which keeps B-tree index inserts local and makes
"most recent first" listings cheap without exposing an incrementing counter.

``uuid.uuid7`` landed in CPython 3.14; on 3.12 we generate the same RFC 9562
layout ourselves so the on-disk format never changes when the runtime is
upgraded.
"""

from __future__ import annotations

import os
import time
import uuid

__all__ = ["is_uuid", "new_id", "uuid7"]


def uuid7() -> uuid.UUID:
    """Generate an RFC 9562 version 7 UUID (48-bit millisecond timestamp + random)."""
    generated = getattr(uuid, "uuid7", None)
    if generated is not None:  # pragma: no cover - depends on interpreter version
        return generated()

    unix_ts_ms = time.time_ns() // 1_000_000
    rand = os.urandom(10)

    value = bytearray(16)
    value[0:6] = unix_ts_ms.to_bytes(6, "big")
    value[6:16] = rand
    # Version 7 in the high nibble of octet 6.
    value[6] = (value[6] & 0x0F) | 0x70
    # RFC 4122 variant in the two high bits of octet 8.
    value[8] = (value[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(value))


def new_id() -> uuid.UUID:
    """Primary-key factory used by every model."""
    return uuid7()


def is_uuid(value: str) -> bool:
    """Whether a string parses as a UUID. Used to validate path parameters."""
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True
