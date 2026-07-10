"""StreamingSignalProcessor — validate and route streaming signals to execution.

Wraps the TradingSignal from StreamingInferencePipeline, validates it,
and dispatches to the exchange adapter.

Supports regime-aware dynamic thresholds (Policy D):
  TRENDING: BUY/SELL >= execution_threshold_trending (default 0.55)
  RANGING:  BUY/SELL >= execution_threshold_ranging (default 0.62)

Thresholds are configurable via environment variables:
  EXECUTION_THRESHOLD_TRENDING — confidence floor in TRENDING regime (default 0.55)
  EXECUTION_THRESHOLD_RANGING  — confidence floor in RANGING regime  (default 0.62)

Every rejection produces a structured log with: signal_id, regime, predicted_class,
confidence, required_threshold, decision.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import structlog

from ...domain.value_objects.execution_signal import ExecutionSignal
from ...domain.value_objects.signal_side import SignalSide
from ...domain.value_objects.trading_signal import TradingSignal

log = structlog.get_logger()

# ── Default thresholds (Policy D) ──────────────────────────────────
_DEFAULT_THRESHOLD_TRENDING = 0.55
_DEFAULT_THRESHOLD_RANGING = 0.62


@dataclass
class SignalProcessorResult:
    accepted: bool = False
    execution_signal: ExecutionSignal | None = None
    reason: str = ""
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class StreamingSignalProcessor:
    """Validates TradingSignals and produces ExecutionSignals.

    Performs three checks:
      1. Confidence >= regime-aware threshold
      2. Side is BUY or SELL (rejects HOLD)
      3. Cooldown: same side within cooldown_seconds is skipped

    The ExecutionSignal is returned for the caller to dispatch.
    """

    def __init__(
        self,
        symbol: str,
        strategy_id: str = "novaquant",
        min_confidence: float | None = None,
        cooldown_seconds: float = 300.0,
        threshold_trending: float | None = None,
        threshold_ranging: float | None = None,
    ) -> None:
        self._symbol = symbol
        self._strategy_id = strategy_id

        # Regime-aware thresholds (env var override > constructor arg > default)
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

        # Fallback min_confidence (only used if regime is unknown)
        self._min_confidence = float(
            min_confidence if min_confidence is not None else self._threshold_ranging
        ) if min_confidence is not None else self._threshold_ranging

        self._cooldown = cooldown_seconds

        self._last_side: SignalSide | None = None
        self._last_ts: float = 0.0

        # Metrics counters
        self._signals_total = 0
        self._signals_rejected_threshold = 0
        self._signals_accepted_threshold = 0

    def _get_threshold(self, regime: str) -> float:
        """Return the confidence threshold for the given market regime."""
        if regime == "TRENDING":
            return self._threshold_trending
        return self._threshold_ranging  # RANGING or UNKNOWN

    @property
    def signals_total(self) -> int:
        return self._signals_total

    @property
    def signals_rejected_threshold(self) -> int:
        return self._signals_rejected_threshold

    @property
    def signals_accepted_threshold(self) -> int:
        return self._signals_accepted_threshold

    def confirm_signal(self, side: SignalSide) -> None:
        """Register cooldown AFTER guard approves execution."""
        self._last_side = side
        self._last_ts = datetime.now(timezone.utc).timestamp()

    def process(self, signal: TradingSignal, price: Decimal | None = None) -> SignalProcessorResult:
        now = datetime.now(timezone.utc)
        now_ts = now.timestamp()

        self._signals_total += 1
        regime = (signal.metadata or {}).get("regime", "UNKNOWN")
        predicted_class = signal.side.value if signal.side != SignalSide.HOLD else "HOLD"
        required_threshold = self._get_threshold(regime)

        if signal.side == SignalSide.HOLD:
            return SignalProcessorResult(
                accepted=False, reason="side_is_hold", ts=now
            )

        if signal.confidence < required_threshold:
            self._signals_rejected_threshold += 1
            log.info(
                "signal_rejected_threshold",
                signal_id=signal.model_version,
                regime=regime,
                predicted_class=predicted_class,
                confidence=round(signal.confidence, 4),
                required_threshold=required_threshold,
                decision="REJECTED_THRESHOLD",
            )
            return SignalProcessorResult(
                accepted=False,
                reason=f"low_confidence: {signal.confidence:.3f} < {required_threshold} (regime={regime})",
                ts=now,
            )

        if signal.side == self._last_side:
            elapsed = now_ts - self._last_ts
            if elapsed < self._cooldown:
                log.info(
                    "signal_rejected_cooldown",
                    signal_id=signal.model_version,
                    regime=regime,
                    predicted_class=predicted_class,
                    confidence=round(signal.confidence, 4),
                    elapsed_s=round(elapsed, 1),
                    cooldown_s=self._cooldown,
                    decision="REJECTED_COOLDOWN",
                )
                return SignalProcessorResult(
                    accepted=False,
                    reason=f"cooldown: {elapsed:.0f}s < {self._cooldown:.0f}s for {signal.side.value}",
                    ts=now,
                )

        self._signals_accepted_threshold += 1
        execution = ExecutionSignal(
            side=signal.side,
            confidence=signal.confidence,
            symbol=self._symbol,
            signal_id=uuid4().hex[:12],
            strategy_id=self._strategy_id,
            price=price,
            timestamp=now,
            metadata={
                "model_version": signal.model_version,
                "regime_at_dispatch": regime,
                **(signal.metadata or {}),
            },
        )

        log.info(
            "signal_accepted",
            side=execution.side.value,
            confidence=round(execution.confidence, 4),
            symbol=execution.symbol,
            regime=regime,
        )

        return SignalProcessorResult(
            accepted=True, execution_signal=execution, ts=now
        )

    def reset_cooldown(self) -> None:
        self._last_side = None
        self._last_ts = 0.0
