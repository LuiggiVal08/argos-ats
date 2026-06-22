"""StreamConsumptionBarrier — prevents stale tick processing post-recovery.

Race condition: recovery completes → streaming starts → Redis backlog
of ticks during recovery gets processed → stale state trades.

Fix: barrier blocks tick processing until a FRESH tick (timestamp >
recovery_completed_at) arrives. All backlogged ticks are silently dropped.

States:
  - BLOCKED: recovery not committed yet, no ticks processed
  - OPEN: recovery committed, processing fresh ticks only
"""
from __future__ import annotations

import time


class StreamConsumptionBarrier:
    """Domain entity: gates tick consumption after recovery.

    Usage:
        barrier = StreamConsumptionBarrier()
        barrier.open(recovery_completed_at=time.time())

        # In tick loop:
        if not barrier.should_process(tick_ts_ms):
            continue  # drop stale tick
    """

    def __init__(self) -> None:
        self._open = False
        self._recovery_ts: float | None = None
        self._fresh_tick_seen = False
        self._dropped_ticks = 0

    def open(self, recovery_timestamp: float | None = None) -> None:
        """Open the barrier. Only ticks after recovery_ts will process."""
        self._open = True
        self._recovery_ts = recovery_timestamp or time.time()
        self._fresh_tick_seen = False
        self._dropped_ticks = 0

    def block(self) -> None:
        """Re-lock the barrier (e.g. on recovery failure)."""
        self._open = False
        self._fresh_tick_seen = False

    def should_process(self, tick_timestamp_ms: int | None = None) -> bool:
        """Check if a tick should be processed.

        Args:
            tick_timestamp_ms: Tick timestamp in milliseconds (from exchange).

        Returns:
            True if the tick should be processed, False if it should be dropped.
        """
        if not self._open:
            self._dropped_ticks += 1
            return False

        if tick_timestamp_ms is not None and self._recovery_ts is not None:
            tick_sec = tick_timestamp_ms / 1000.0
            if tick_sec <= self._recovery_ts:
                self._dropped_ticks += 1
                return False

        if not self._fresh_tick_seen and tick_timestamp_ms is not None:
            self._fresh_tick_seen = True

        return True

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def dropped_ticks(self) -> int:
        return self._dropped_ticks

    @property
    def fresh_tick_seen(self) -> bool:
        return self._fresh_tick_seen
