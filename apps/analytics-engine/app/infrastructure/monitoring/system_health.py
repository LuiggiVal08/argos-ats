"""SystemHealthSnapshot — periodic system state observability.

Runs every 5-15 minutes, collects key metrics from all streaming
components, and emits a structured health log.

No dependencies on external storage. Pure observability.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

log = structlog.get_logger()

_INFERENCE_TF_MS = 300_000
_CANDLE_TF_MS = 60_000


@dataclass
class HealthSnapshot:
    timestamp: str = ""
    uptime_seconds: float = 0.0
    candle_rate_ok: bool = True
    inference_health: str = "ok"
    signal_entropy: float = 0.0
    execution_activity: str = "normal"
    buffer_health: str = "ok"
    stream_lag_ms: int = 0
    desync_count: int = 0
    inference_latency_p50_ms: float = 0.0
    inference_latency_p95_ms: float = 0.0
    signal_count: int = 0
    execution_count: int = 0
    hold_ratio: float = 0.0
    rejected_ratio: float = 0.0
    buffer_occupancy_pct: float = 0.0
    missed_candles: int = 0
    errors: list[str] = field(default_factory=list)


class SystemHealthCollector:
    """Collects runtime metrics from shared state objects.

    All counters are in-memory monotonic — no persistence.
    Pass references to the actual components at construction.
    """

    def __init__(
        self,
        get_buffer_snapshot: Any = None,
        get_inference_pipeline: Any = None,
        get_signal_processor: Any = None,
        get_stream_integrity: Any = None,
        get_candle_builder: Any = None,
    ) -> None:
        self._get_buffer = get_buffer_snapshot
        self._get_pipeline = get_inference_pipeline
        self._get_processor = get_signal_processor
        self._get_integrity = get_stream_integrity
        self._get_builder = get_candle_builder

        self._start_ts = time.monotonic()
        self._inference_latencies: deque[float] = deque(maxlen=100)
        self._signal_results: deque[str] = deque(maxlen=200)
        self._execution_count = 0
        self._desync_count = 0
        self._missed_candles = 0

    def record_inference(self, latency_ms: float, has_signal: bool) -> None:
        self._inference_latencies.append(latency_ms)
        self._signal_results.append("signal" if has_signal else "hold")

    def record_execution(self) -> None:
        self._execution_count += 1

    def record_desync(self) -> None:
        self._desync_count += 1

    def record_missed_candle(self) -> None:
        self._missed_candles += 1

    def snapshot(self) -> HealthSnapshot:
        now = datetime.now(timezone.utc)
        uptime = time.monotonic() - self._start_ts
        buf = self._call_or_none(self._get_buffer) or {}
        pipe = self._call_or_none(self._get_pipeline)
        proc = self._call_or_none(self._get_processor)
        integ = self._call_or_none(self._get_integrity)

        candle_count = buf.get("count", 0)
        buffer_max = buf.get("maxlen", 200)
        buffer_pct = (candle_count / buffer_max * 100.0) if buffer_max > 0 else 0.0

        inference_health = "ok"
        if pipe is not None:
            pipe_loaded = getattr(pipe, "is_loaded", False)
            pipe_error = getattr(pipe, "load_error", "")
            if not pipe_loaded:
                inference_health = "no_model"
            elif pipe_error:
                inference_health = "error"

        latencies = list(self._inference_latencies)
        p50 = _percentile(latencies, 50) if latencies else 0.0
        p95 = _percentile(latencies, 95) if latencies else 0.0

        total_signals = len(self._signal_results)
        holds = sum(1 for r in self._signal_results if r == "hold")
        hold_ratio = holds / total_signals if total_signals > 0 else 0.0

        exec_count = self._execution_count
        if exec_count < 3:
            exec_activity = "low"
        elif exec_count < 20:
            exec_activity = "normal"
        else:
            exec_activity = "high"

        rejected_ratio = 0.0
        if proc is not None:
            total = getattr(proc, "_rejected_count", 0) + getattr(
                proc, "_accepted_count", 0
            )
            rejected = getattr(proc, "_rejected_count", 0)
            rejected_ratio = rejected / total if total > 0 else 0.0

        signal_entropy = hold_ratio if hold_ratio > 0 else 0.0

        expected_candles = int(uptime / (_CANDLE_TF_MS / 1000))
        actual_candles = candle_count
        candle_rate_ok = actual_candles >= max(expected_candles - 5, 0)

        stream_lag = 0
        if integ is not None:
            stream_lag = getattr(integ, "_max_lag_ms", 0) or 0

        errors: list[str] = []
        memory_degraded = buffer_pct > 95
        if memory_degraded:
            errors.append("buffer_near_capacity")

        return HealthSnapshot(
            timestamp=now.isoformat(),
            uptime_seconds=uptime,
            candle_rate_ok=candle_rate_ok,
            inference_health=inference_health,
            signal_entropy=round(signal_entropy, 4),
            execution_activity=exec_activity,
            buffer_health="ok" if not memory_degraded else "near_capacity",
            stream_lag_ms=stream_lag,
            desync_count=self._desync_count,
            inference_latency_p50_ms=round(p50, 2),
            inference_latency_p95_ms=round(p95, 2),
            signal_count=total_signals,
            execution_count=exec_count,
            hold_ratio=round(hold_ratio, 4),
            rejected_ratio=round(rejected_ratio, 4),
            buffer_occupancy_pct=round(buffer_pct, 1),
            missed_candles=self._missed_candles,
            errors=errors,
        )

    def emit(self) -> HealthSnapshot:
        s = self.snapshot()
        log.info(
            "system_health_snapshot",
            timestamp=s.timestamp,
            uptime_s=round(s.uptime_seconds),
            candle_rate_ok=s.candle_rate_ok,
            inference_health=s.inference_health,
            signal_entropy=s.signal_entropy,
            exec_activity=s.execution_activity,
            buffer_health=s.buffer_health,
            stream_lag_ms=s.stream_lag_ms,
            desync_count=s.desync_count,
            latency_p50=s.inference_latency_p50_ms,
            latency_p95=s.inference_latency_p95_ms,
            signals=s.signal_count,
            executions=s.execution_count,
            hold_ratio=s.hold_ratio,
            rejected_ratio=s.rejected_ratio,
            buffer_pct=s.buffer_occupancy_pct,
            missed_candles=s.missed_candles,
            errors=s.errors or None,
        )
        return s

    @staticmethod
    def _call_or_none(fn: Any) -> Any:
        if fn is None:
            return None
        try:
            return fn() if callable(fn) else fn
        except Exception:
            return None


def _percentile(sorted_samples: list[float], p: int) -> float:
    if not sorted_samples:
        return 0.0
    s = sorted(sorted_samples)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = f + 1
    if c >= len(s):
        return s[-1]
    return s[f] + (k - f) * (s[c] - s[f])
