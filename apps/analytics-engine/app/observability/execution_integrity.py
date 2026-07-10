"""Execution Integrity Report — pipeline timing metrics.

Tracks full lifecycle:
  signal → execution → fill → SL placement → verification

Measures:
- execution latency (signal → order filled)
- SL placement latency
- SL verification latency
- pipeline total latency (p50 / p99)
"""
from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .schema import emit_metric


@dataclass
class PipelineTiming:
    signal_id: str
    execution_latency_ms: float = 0.0
    sl_placement_latency_ms: float = 0.0
    sl_verify_latency_ms: float = 0.0
    total_latency_ms: float = 0.0
    divergence_pct: float = 0.0


@dataclass
class ExecutionIntegrityTracker:
    """Tracks execution pipeline timing.
    
    Pure instrumentation — no effect on execution.
    """
    _timings: deque = field(default_factory=lambda: deque(maxlen=1000))
    _window_start: float = 0.0

    def __post_init__(self) -> None:
        if self._window_start == 0.0:
            object.__setattr__(self, "_window_start", datetime.now(timezone.utc).timestamp())

    def record_execution(
        self,
        signal_id: str,
        execution_latency_ms: float,
        divergence_pct: float = 0.0,
    ) -> None:
        timing = PipelineTiming(
            signal_id=signal_id,
            execution_latency_ms=round(execution_latency_ms, 1),
            divergence_pct=round(divergence_pct, 4),
        )
        self._timings.append(timing)
        emit_metric(
            "execution.signal_to_fill",
            value=timing.execution_latency_ms,
            threshold="p99 < 500ms",
            level="WARNING" if execution_latency_ms > 500 else "INFO",
            signal_id=signal_id,
            execution_latency_ms=timing.execution_latency_ms,
            divergence_pct=timing.divergence_pct,
        )

    def record_sl_placement(
        self,
        signal_id: str,
        sl_placement_latency_ms: float,
    ) -> None:
        # Find the timing record by signal_id and update it
        for t in self._timings:
            if t.signal_id == signal_id:
                t.sl_placement_latency_ms = round(sl_placement_latency_ms, 1)
                t.total_latency_ms = round(
                    t.execution_latency_ms + sl_placement_latency_ms, 1
                )
                break
        emit_metric(
            "execution.sl_placement",
            value=sl_placement_latency_ms,
            threshold="p99 < 200ms",
            level="WARNING" if sl_placement_latency_ms > 200 else "INFO",
            signal_id=signal_id,
            sl_placement_latency_ms=round(sl_placement_latency_ms, 1),
        )

    def record_sl_verify(
        self,
        signal_id: str,
        sl_verify_latency_ms: float,
        sl_found: bool,
    ) -> None:
        for t in self._timings:
            if t.signal_id == signal_id:
                t.sl_verify_latency_ms = round(sl_verify_latency_ms, 1)
                t.total_latency_ms = round(
                    t.execution_latency_ms + t.sl_placement_latency_ms + sl_verify_latency_ms, 1
                )
                break
        emit_metric(
            "execution.sl_verify",
            value=sl_verify_latency_ms,
            threshold="p99 < 100ms",
            level="WARNING" if sl_verify_latency_ms > 100 else "INFO",
            signal_id=signal_id,
            sl_verify_latency_ms=round(sl_verify_latency_ms, 1),
            sl_found=sl_found,
        )

    @property
    def execution_latencies(self) -> list[float]:
        return [t.execution_latency_ms for t in self._timings]

    @property
    def total_latencies(self) -> list[float]:
        return [t.total_latency_ms for t in self._timings if t.total_latency_ms > 0]

    def _p_percentile(self, values: list[float], p: int) -> float:
        if not values:
            return 0.0
        sorted_vals = sorted(values)
        idx = max(0, min(len(sorted_vals) - 1, int(len(sorted_vals) * p / 100)))
        return sorted_vals[idx]

    def snapshot(self) -> dict:
        exec_lat = self.execution_latencies
        total_lat = self.total_latencies
        return {
            "total_executions": len(self._timings),
            "execution_latency_p50": round(self._p_percentile(exec_lat, 50), 1),
            "execution_latency_p99": round(self._p_percentile(exec_lat, 99), 1),
            "total_latency_p50": round(self._p_percentile(total_lat, 50), 1) if total_lat else 0.0,
            "total_latency_p99": round(self._p_percentile(total_lat, 99), 1) if total_lat else 0.0,
        }

    def emit_snapshot(self) -> None:
        data = self.snapshot()
        level = "WARNING" if data["execution_latency_p99"] > 500 else "INFO"
        emit_metric(
            "execution.integrity.snapshot",
            value=data,
            threshold="p99 < 500ms",
            level=level,
            **data,
        )


# Default singleton — import directly for zero-constructor-change instrumentation
default_execution_tracker = ExecutionIntegrityTracker()
