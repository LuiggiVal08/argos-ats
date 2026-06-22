"""StreamingSignalProcessor — validate and route streaming signals to execution.

Wraps the TradingSignal from StreamingInferencePipeline, validates it,
and dispatches to the exchange adapter.
"""
from __future__ import annotations

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


@dataclass
class SignalProcessorResult:
    accepted: bool = False
    execution_signal: ExecutionSignal | None = None
    reason: str = ""
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class StreamingSignalProcessor:
    """Validates TradingSignals and produces ExecutionSignals.

    Performs three checks:
      1. Confidence >= min_confidence
      2. Side is BUY or SELL (rejects HOLD)
      3. Cooldown: same side within cooldown_seconds is skipped

    The ExecutionSignal is returned for the caller to dispatch.
    """

    def __init__(
        self,
        symbol: str,
        strategy_id: str = "novaquant",
        min_confidence: float = 0.7,
        cooldown_seconds: float = 300.0,
    ) -> None:
        self._symbol = symbol
        self._strategy_id = strategy_id
        self._min_confidence = min_confidence
        self._cooldown = cooldown_seconds

        self._last_side: SignalSide | None = None
        self._last_ts: float = 0.0

    def confirm_signal(self, side: SignalSide) -> None:
        """Register cooldown AFTER guard approves execution."""
        self._last_side = side
        self._last_ts = datetime.now(timezone.utc).timestamp()

    def process(self, signal: TradingSignal, price: Decimal | None = None) -> SignalProcessorResult:
        now = datetime.now(timezone.utc)
        now_ts = now.timestamp()

        if signal.side == SignalSide.HOLD:
            return SignalProcessorResult(
                accepted=False, reason="side_is_hold", ts=now
            )

        if signal.confidence < self._min_confidence:
            return SignalProcessorResult(
                accepted=False,
                reason=f"low_confidence: {signal.confidence:.3f} < {self._min_confidence}",
                ts=now,
            )

        if signal.side == self._last_side:
            elapsed = now_ts - self._last_ts
            if elapsed < self._cooldown:
                return SignalProcessorResult(
                    accepted=False,
                    reason=f"cooldown: {elapsed:.0f}s < {self._cooldown:.0f}s for {signal.side.value}",
                    ts=now,
                )

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
                **(signal.metadata or {}),
            },
        )

        log.info(
            "signal_accepted",
            side=execution.side.value,
            confidence=execution.confidence,
            symbol=execution.symbol,
        )

        return SignalProcessorResult(
            accepted=True, execution_signal=execution, ts=now
        )

    def reset_cooldown(self) -> None:
        self._last_side = None
        self._last_ts = 0.0
