"""ExecutionGuard — runtime safety wrapper around ExecutionEngine.

Adds three guard rails WITHOUT modifying execution core logic:

  1. Confidence threshold hardened to 0.75 (instead of 0.7).
  2. Volatility spike detection: if ATR/price ratio spikes > 2x
     trailing average, position size factor reduced 50%.
  3. Consecutive-failure soft circuit breaker: 3 failures → 30s pause.

All decisions are logged with structured events.
"""
from __future__ import annotations

import time
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal

import structlog

from ...domain.value_objects.execution_signal import ExecutionSignal

log = structlog.get_logger()


class ExecutionGuard:
    """Wraps an ExecutionEngine-like ``execute`` callable.

    Usage in streaming loop:
        guard = ExecutionGuard(execute_fn)
        result = await guard.execute(signal)
    """

    def __init__(
        self,
        execute_fn: object,
        confidence_threshold: float = 0.75,
        soft_pause_failures: int = 3,
        soft_pause_seconds: float = 30.0,
        atr_window: int = 20,
    ) -> None:
        self._execute = execute_fn
        self._confidence_threshold = confidence_threshold
        self._soft_pause_failures = soft_pause_failures
        self._soft_pause_seconds = soft_pause_seconds

        self._consecutive_failures = 0
        self._paused_until: float = 0.0

        self._atr_ratios: deque[float] = deque(maxlen=atr_window)
        self._rejected_low_confidence = 0
        self._volatility_reductions = 0
        self._soft_pauses_triggered = 0

    async def execute(self, signal: ExecutionSignal) -> object:
        now = time.monotonic()

        if now < self._paused_until:
            remaining = self._paused_until - now
            log.info(
                "execution_guard_paused",
                remaining_s=round(remaining, 1),
                reason="soft_circuit_breaker",
            )
            return _GuardRejected("soft_circuit_breaker_active")

        if signal.confidence < self._confidence_threshold:
            self._rejected_low_confidence += 1
            log.info(
                "rejected_low_confidence",
                confidence=signal.confidence,
                threshold=self._confidence_threshold,
                signal_id=signal.signal_id,
            )
            return _GuardRejected(
                f"low_confidence: {signal.confidence:.3f} < {self._confidence_threshold}"
            )

        # Inject volatility-based risk multiplier into signal metadata
        risk_mult = self.volatility_reduction_factor()
        signal.metadata["risk_multiplier"] = risk_mult

        try:
            result = await self._execute(signal)
        except Exception as e:
            self._consecutive_failures += 1
            log.warning(
                "execution_guard_failure",
                consecutive=self._consecutive_failures,
                error=str(e),
            )
            self._check_soft_pause()
            return _GuardRejected(f"execution_error: {e}")

        # Read back ATR/price ratio reported by execute_signal
        atr_ratio = signal.metadata.get("atr_price_ratio")
        if atr_ratio is not None:
            self.report_atr_ratio(atr_ratio)

        if self._is_rejected(result):
            self._consecutive_failures += 1
            self._check_soft_pause()
        else:
            self._consecutive_failures = 0

        return result

    def report_atr_ratio(self, ratio: float) -> None:
        if ratio > 0:
            self._atr_ratios.append(ratio)

    def volatility_reduction_factor(self) -> float:
        if len(self._atr_ratios) < 5:
            return 1.0
        avg = sum(self._atr_ratios) / len(self._atr_ratios)
        latest = self._atr_ratios[-1]
        if latest > avg * 2.0 and avg > 0:
            self._volatility_reductions += 1
            log.info(
                "volatility_spike_detected",
                latest_ratio=round(latest, 6),
                avg_ratio=round(avg, 6),
                reduction_50pct=True,
            )
            return 0.5
        return 1.0

    @property
    def rejected_low_confidence(self) -> int:
        return self._rejected_low_confidence

    @property
    def volatility_reductions(self) -> int:
        return self._volatility_reductions

    @property
    def soft_pauses_triggered(self) -> int:
        return self._soft_pauses_triggered

    @property
    def is_paused(self) -> bool:
        return time.monotonic() < self._paused_until

    # ── Private ──────────────────────────────────────────────────

    def _check_soft_pause(self) -> None:
        if self._consecutive_failures >= self._soft_pause_failures:
            self._paused_until = time.monotonic() + self._soft_pause_seconds
            self._soft_pauses_triggered += 1
            log.warning(
                "execution_guard_soft_pause",
                consecutive_failures=self._consecutive_failures,
                pause_s=self._soft_pause_seconds,
            )

    @staticmethod
    def _is_rejected(result: object) -> bool:
        approved = getattr(result, "approved", None)
        if approved is not None:
            return not approved
        reason = getattr(result, "reason", "")
        return bool(reason)


class _GuardRejected:
    """Minimal stand-in for ExecutionResult when guard rejects."""

    def __init__(self, reason: str) -> None:
        self.approved = False
        self.reason = reason
