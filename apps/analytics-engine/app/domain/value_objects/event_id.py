"""UUID v7 generator for Python < 3.14.

RFC 9562 UUID v7:
  - 48-bit Unix ms timestamp (sortable)
  - 4-bit version (0b0111)
  - 12-bit random_a
  - 2-bit variant (0b10)
  - 62-bit random_b
"""
from __future__ import annotations

import os
import time
from uuid import UUID


def uuid7() -> UUID:
    """Generate a time-ordered UUID v7."""
    timestamp_ms = int(time.time() * 1000)

    rand = int.from_bytes(os.urandom(10), "big")
    rand_a = (rand >> 62) & 0x0FFF   # 12 bits
    rand_b = rand & ((1 << 62) - 1)   # 62 bits

    # Build 16 bytes in network order (big-endian)
    # Byte layout:
    #   0-5:  48-bit unix_ts_ms
    #   6:    4-bit ver(7) + high 4 bits of rand_a
    #   7:    low 8 bits of rand_a
    #   8:    2-bit variant(2) + high 6 bits of rand_b
    #   9-15: remaining 56 bits of rand_b

    ts_bytes = timestamp_ms.to_bytes(6, "big")

    # Byte 6: version(4 bits) | rand_a_hi(4 bits)
    b6 = (0x7 << 4) | ((rand_a >> 8) & 0x0F)

    # Byte 7: rand_a_lo(8 bits)
    b7 = rand_a & 0xFF

    # Byte 8: variant(2 bits) | rand_b_hi(6 bits)
    b8 = (0b10 << 6) | ((rand_b >> 56) & 0x3F)

    # Bytes 9-15: rand_b bytes 6..0 (56 bits)
    rand_b_bytes = rand_b.to_bytes(8, "big")[1:]  # 7 bytes (discard top byte, we used 6 bits)

    raw = ts_bytes + bytes([b6, b7, b8]) + rand_b_bytes
    assert len(raw) == 16, f"expected 16 bytes, got {len(raw)}"

    return UUID(bytes=raw)
