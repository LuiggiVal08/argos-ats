"""ExecutionGuard — runtime safety wrapper around ExecutionEngine.

Adds three guard rails WITHOUT modifying execution core logic:

   1. Confidence threshold: regime-aware (Policy D).
      TRENDING >= threshold_trending (default 0.55)
      RANGING  >= threshold_ranging  (default 0.62)
   2. Volatility spike detection: if ATR/price ratio spikes > 2x
      trailing average, position size factor reduced 50%.
   3. Consecutive-failure soft circuit breaker: 3 failures → 30s pause.

All decisions are logged with structured events.

Regime thresholds are read from environment variables:
  EXECUTION_THRESHOLD_TRENDING (default 0.55)
  EXECUTION_THRESHOLD_RANGING  (default 0.62)
"""
from __future__ import annotations

import os
import time
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal

import structlog

from ...domain.value_objects.execution_signal import ExecutionSignal

log = structlog.get_logger()

_DEFAULT_THRESHOLD_TRENDING = 0.55
_DEFAULT_THRESHOLD_RANGING = 0.62


class ExecutionGuard:
    """Wraps an ExecutionEngine-like ``execute`` callable.

    Usage in streaming loop:
        guard = ExecutionGuard(execute_fn)
        result = await guard.execute(signal)
    """

    def __init__(
        self,
        execute_fn: object,
        confidence_threshold: float | None = None,
        threshold_trending: float | None = None,
        threshold_ranging: float | None = None,
        soft_pause_failures: int = 3,
        soft_pause_seconds: float = 30.0,
        atr_window: int = 20,
        market_continuity_guard: object | None = None,
    ) -> None:
        self._execute = execute_fn

        # Regime-aware thresholds (env var > constructor arg > default)
        self._threshold_trending = float(
            os.environ.get(
                "EXECUTION_THRESHOLD_TRENDING",
                str(threshold_trending if threshold_trending is not None else _DEFAULT_THRESHOLD_TRENDING),
            )
        )
        self._threshold_ranging = float(
            os.environ.get(
                "EXECUTION_THRESHOLD_RANGING",
                str(threshold_ranging if threshold_ranging is not None else _DEFAULT_THRESHOLD_RANGING),
            )
        )

        # Legacy fallback (used when regime is unknown)
        if confidence_threshold is not None:
            self._fallback_threshold = confidence_threshold
        else:
            self._fallback_threshold = max(self._threshold_trending, self._threshold_ranging)

        self._soft_pause_failures = soft_pause_failures
        self._soft_pause_seconds = soft_pause_seconds

        self._consecutive_failures = 0
        self._paused_until: float = 0.0

        self._atr_ratios: deque[float] = deque(maxlen=atr_window)
        self._rejected_low_confidence = 0
        self._volatility_reductions = 0
        self._soft_pauses_triggered = 0
        self._mcg = market_continuity_guard

    def _get_threshold(self, signal: ExecutionSignal) -> float:
        """Return the confidence threshold for the signal's regime."""
        regime = (signal.metadata or {}).get("regime", "") or \
                 (signal.metadata or {}).get("regime_at_dispatch", "")
        if regime == "TRENDING":
            return self._threshold_trending
        elif regime == "RANGING":
            return self._threshold_ranging
        return self._fallback_threshold

    async def execute(self, signal: ExecutionSignal) -> object:
        now = time.monotonic()

        # Market Continuity Guard: block execution if data gap active
        if self._mcg is not None and getattr(self._mcg, "market_data_gap", False):
            log.info(
                "execution_guard_market_data_gap",
                gap_duration_s=round(getattr(self._mcg, "gap_duration_s", 0), 1),
                reason="market_data_gap_active",
            )
            return _GuardRejected("market_data_gap_active")

        if now < self._paused_until:
            remaining = self._paused_until - now
            log.info(
                "execution_guard_paused",
                remaining_s=round(remaining, 1),
                reason="soft_circuit_breaker",
            )
            return _GuardRejected("soft_circuit_breaker_active")

        regime = (signal.metadata or {}).get("regime", "") or \
                 (signal.metadata or {}).get("regime_at_dispatch", "UNKNOWN")
        threshold = self._get_threshold(signal)
        predicted_class = signal.side.value

        if signal.confidence < threshold:
            self._rejected_low_confidence += 1
            log.info(
                "signal_rejected_threshold",
                signal_id=signal.signal_id,
                regime=regime,
                predicted_class=predicted_class,
                confidence=round(signal.confidence, 4),
                required_threshold=threshold,
                decision="REJECTED_THRESHOLD",
            )
            return _GuardRejected(
                f"low_confidence: {signal.confidence:.3f} < {threshold} (regime={regime})"
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
