from __future__ import annotations

import time
import structlog

log = structlog.get_logger()


class MarketContinuityGuard:
    """Detects market data gaps and prevents trading on stale/discontinuous data.

    The guard tracks the expected freshness of the tick stream. If no tick
    arrives within ``gap_threshold_s`` seconds of the last observed tick,
    the stream is considered interrupted and ``market_data_gap = True``.

    While in gap state:
      - Inference is skipped (no signals generated)
      - Order execution is blocked (no trading allowed)
      - Only data ingestion continues

    The gap is cleared only after fresh ticks resume AND at least
    ``recovery_ticks`` consecutive ticks arrive without a new gap.
    """

    def __init__(
        self,
        gap_threshold_s: float = 60.0,
        recovery_ticks: int = 10,
    ) -> None:
        self._gap_threshold_s = gap_threshold_s
        self._recovery_ticks = recovery_ticks
        self._last_tick_s: float = 0.0
        self._market_data_gap: bool = False
        self._gap_start_s: float = 0.0
        self._gap_duration_s: float = 0.0
        self._consecutive_ticks: int = 0
        self._total_gaps: int = 0

    # ── Public API ──────────────────────────────────────────────────

    @property
    def market_data_gap(self) -> bool:
        return self._market_data_gap

    @property
    def gap_duration_s(self) -> float:
        if self._market_data_gap:
            return time.time() - self._gap_start_s
        return self._gap_duration_s

    @property
    def last_tick_age_s(self) -> float:
        if self._last_tick_s == 0.0:
            return 0.0
        return time.time() - self._last_tick_s

    @property
    def total_gaps(self) -> int:
        return self._total_gaps

    def record_tick(self, tick_timestamp_s: float) -> None:
        """Record a tick and evaluate gap state.

        Call this for every tick that passes validation and is actually
        consumed by the candle builder (i.e. after dedup, barrier, etc).
        """
        now = time.time()

        if self._last_tick_s == 0.0:
            self._last_tick_s = tick_timestamp_s
            self._consecutive_ticks = 1
            return

        elapsed = tick_timestamp_s - self._last_tick_s
        self._last_tick_s = tick_timestamp_s

        if elapsed > self._gap_threshold_s:
            if not self._market_data_gap:
                self._market_data_gap = True
                self._gap_start_s = now
                self._total_gaps += 1
                log.warning(
                    "GAP_DETECTED",
                    elapsed_s=round(elapsed, 1),
                    threshold_s=self._gap_threshold_s,
                    last_tick_s=self._last_tick_s,
                    gap_count=self._total_gaps,
                )
            self._consecutive_ticks = 1
            return

        if self._market_data_gap:
            self._consecutive_ticks += 1
            if self._consecutive_ticks >= self._recovery_ticks:
                self._gap_duration_s = now - self._gap_start_s
                self._market_data_gap = False
                self._gap_start_s = 0.0
                log.info(
                    "GAP_RESOLVED",
                    duration_s=round(self._gap_duration_s, 1),
                    recovery_ticks=self._consecutive_ticks,
                )
        else:
            self._consecutive_ticks += 1

    def reset(self) -> None:
        """Force-reset gap state. Used when re-initializing the stream."""
        was_gap = self._market_data_gap
        self._market_data_gap = False
        self._last_tick_s = 0.0
        self._gap_start_s = 0.0
        self._consecutive_ticks = 0
        if was_gap:
            log.info("GAP_RESET_MANUAL", reason="stream_reinitialized")

    def snapshot(self) -> dict:
        return {
            "market_data_gap": self._market_data_gap,
            "gap_duration_s": round(self.gap_duration_s, 1),
            "last_tick_age_s": round(self.last_tick_age_s, 1),
            "consecutive_ticks": self._consecutive_ticks,
            "total_gaps": self._total_gaps,
            "gap_threshold_s": self._gap_threshold_s,
            "recovery_ticks": self._recovery_ticks,
        }
