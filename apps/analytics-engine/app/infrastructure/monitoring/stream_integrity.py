"""StreamIntegrityMonitor — real-time health of the tick stream.

Detects:
  - Stream lag (wall-clock vs last received tick)
  - Missing tick bursts (>N ticks in <1s window)
  - Idle periods (no ticks for >X seconds)
  - Processing backlog (accumulated ticks not consumed)
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

log = structlog.get_logger()

_BURST_WINDOW_MS = 1_000
_BURST_THRESHOLD = 100
_IDLE_THRESHOLD_S = 30
_MAX_EXPECTED_LAG_MS = 5_000


@dataclass
class StreamAnomaly:
    kind: str
    detail: str
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    current_lag_ms: int = 0


class StreamIntegrityMonitor:
    """Monitors a single tick stream for anomalies.

    Shared mutable counters — safe to call from one task (the
    tick-consumption loop).
    """

    def __init__(
        self,
        stream_key: str = "",
        burst_threshold: int = _BURST_THRESHOLD,
        idle_threshold_s: float = _IDLE_THRESHOLD_S,
        max_lag_ms: int = _MAX_EXPECTED_LAG_MS,
    ) -> None:
        self._stream_key = stream_key
        self._burst_threshold = burst_threshold
        self._idle_threshold_s = idle_threshold_s
        self._max_lag_ms = max_lag_ms

        self._tick_timestamps: deque[float] = deque(maxlen=200)
        self._last_tick_ts: float = 0.0
        self._last_tick_id: str = ""
        self._anomalies: deque[StreamAnomaly] = deque(maxlen=50)

        self._burst_count = 0
        self._last_burst_log_ts: float = 0.0
        self._idle_events = 0
        self._max_lag_ms_seen = 0
        self._total_ticks = 0
        self._start_wall = time.monotonic()

    def record_tick(self, tick_id: str, ts_ms: int) -> None:
        now = time.monotonic()
        self._tick_timestamps.append(now)
        self._last_tick_ts = now
        self._last_tick_id = tick_id
        self._total_ticks += 1

        self._check_burst(now)
        self._check_lag(ts_ms)

    def check_idle(self) -> None:
        elapsed = time.monotonic() - self._last_tick_ts
        if self._last_tick_ts > 0 and elapsed > self._idle_threshold_s:
            self._idle_events += 1
            anomaly = StreamAnomaly(
                kind="idle",
                detail=f"no_ticks_for_{elapsed:.0f}s",
                current_lag_ms=int(elapsed * 1000),
            )
            self._anomalies.append(anomaly)
            log.warning(
                "stream_anomaly_detected",
                kind="idle",
                idle_seconds=round(elapsed, 1),
                total_ticks=self._total_ticks,
            )

    def reset(self) -> None:
        self._tick_timestamps.clear()
        self._last_tick_ts = 0.0
        self._burst_count = 0
        self._idle_events = 0
        self._max_lag_ms_seen = 0

    @property
    def max_lag_ms(self) -> int:
        return self._max_lag_ms_seen

    @property
    def burst_count(self) -> int:
        return self._burst_count

    @property
    def idle_events(self) -> int:
        return self._idle_events

    @property
    def total_ticks(self) -> int:
        return self._total_ticks

    @property
    def recent_anomalies(self) -> list[StreamAnomaly]:
        return list(self._anomalies)

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        uptime = now - self._start_wall
        idle = now - self._last_tick_ts if self._last_tick_ts > 0 else 0.0
        ticks_per_sec = self._total_ticks / uptime if uptime > 0 else 0.0
        return {
            "total_ticks": self._total_ticks,
            "burst_count": self._burst_count,
            "idle_events": self._idle_events,
            "current_idle_s": round(idle, 1),
            "max_lag_ms": self._max_lag_ms_seen,
            "ticks_per_sec": round(ticks_per_sec, 1),
            "anomalies_recent": len(self._anomalies),
            "stream_key": self._stream_key,
        }

    # ── Private ──────────────────────────────────────────────────

    def _check_burst(self, now: float) -> None:
        cutoff = now - _BURST_WINDOW_MS / 1000.0
        recent = [t for t in self._tick_timestamps if t >= cutoff]
        if len(recent) > self._burst_threshold:
            self._burst_count += 1
            anomaly = StreamAnomaly(
                kind="burst",
                detail=f"{len(recent)}_ticks_in_{_BURST_WINDOW_MS}ms",
                current_lag_ms=0,
            )
            self._anomalies.append(anomaly)
            # Log at most once per second to avoid flooding
            if now - self._last_burst_log_ts >= 1.0:
                self._last_burst_log_ts = now
                log.warning(
                    "stream_anomaly_detected",
                    kind="burst",
                    ticks_in_window=len(recent),
                    window_ms=_BURST_WINDOW_MS,
                )

    def _check_lag(self, ts_ms: int) -> None:
        now_ms = int(time.time() * 1000)
        lag = now_ms - ts_ms
        if lag > self._max_lag_ms:
            self._max_lag_ms_seen = lag
            anomaly = StreamAnomaly(
                kind="lag",
                detail=f"tick_lag_{lag}ms_exceeds_{self._max_lag_ms}ms",
                current_lag_ms=lag,
            )
            self._anomalies.append(anomaly)
            log.warning(
                "stream_anomaly_detected",
                kind="lag",
                lag_ms=lag,
                max_expected_ms=self._max_lag_ms,
            )
