"""ExecutionIdempotencyStore port.

Ensures each execution attempt is processed exactly once.
Prevents double execution from retries, SL/TP duplicates, or signal replay.

Key invariant:
  (idempotency_key, exchange_order_id) = UNIQUE GLOBAL

The idempotency_key is derived from:
  - For initial orders: `exec:{signal_id}`
  - For SL/TP modifications: `sl_mod:{position_id}:{sl_price}` | `tp_mod:{position_id}:{tp_price}`
  - For close operations: `close:{position_id}:{reason}`

Architecture rule:
  - Port lives in application/ports/ (interface only)
  - Concrete adapter lives in infrastructure/
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


class ExecutionIdempotencyError(RuntimeError):
    """Raised when idempotency check fails on I/O."""


@runtime_checkable
class ExecutionIdempotencyStore(Protocol):
    async def check_and_record(
        self,
        idempotency_key: str,
        exchange_order_id: str,
        event_id: str = "",
    ) -> bool:
        """Atomically check if key exists and record it.

        Args:
            idempotency_key: Canonical key for this execution attempt.
            exchange_order_id: The exchange-assigned order ID (if known).
            event_id: Global event ID for tracing.

        Returns:
            True if this is a NEW execution (first time).
            False if this is a DUPLICATE (already recorded).

        Raises:
            ExecutionIdempotencyError on I/O failure.
        """
        ...

    async def exists(self, idempotency_key: str) -> bool:
        """Check if an idempotency key already exists (without recording).

        Returns:
            True if the key has been recorded before.
        """
        ...

    async def update_exchange_order_id(
        self,
        idempotency_key: str,
        exchange_order_id: str,
    ) -> None:
        """Update the exchange_order_id after successful order placement.

        Args:
            idempotency_key: Same key used in check_and_record.
            exchange_order_id: Actual exchange order ID.
        """
        ...

    async def clear(self) -> None:
        """Clear all idempotency records (for testing / manual reset)."""
        ...

    async def cleanup_old_entries(self, retention_hours: int = 24) -> int:
        """Delete idempotency records older than retention_hours.

        Args:
            retention_hours: Entries older than this are deleted (default 24).

        Returns:
            Number of rows deleted.

        Raises:
            ExecutionIdempotencyError on I/O failure.
        """
        ...
