"""ExecutionAuthority — single execution entry point for ALL exchange writes.

Four-layer architecture:
  1. Hard Safety: circuit breaker check before placing new trades
  2. Guard: delegated to ExecutionGuard (confidence, volatility, soft CB)
  3. Domain Execution: delegated to the use case
  4. Single Exchange Client: one ExchangeOrderClient instance, one authority

All use cases receive the same ExecutionAuthority instance, consolidating
the previously 5+ independent ExchangeOrderClient instances into one.
"""
from __future__ import annotations

import time
from collections import deque
from decimal import Decimal
from typing import Awaitable, Callable

import structlog

from ...domain.value_objects.execution_signal import ExecutionSignal
from ...domain.value_objects.order import CompositeOrder, OrderResult, OrderSide
from ...application.ports.exchange_order_client import (
    ExchangeOrderClient,
    ExchangeOrderClientError,
    PositionSummary,
)

log = structlog.get_logger()

IsHaltedFn = Callable[[], Awaitable[bool]]


class _GuardRejected:
    """Minimal stand-in for ExecutionResult when guard rejects.

    Mirrors the same class in execution_guard.py so that callers
    can detect rejection via ``hasattr(result, "report")`` or
    ``isinstance(result, _GuardRejected)``.
    """

    def __init__(self, reason: str) -> None:
        self.approved = False
        self.reason = reason


class ExecutionAuthority:
    """Single execution entry point for ALL exchange writes.

    Wraps one ExchangeOrderClient instance and injects the same instance
    into every use case that needs exchange access. ``place_composite_order``
    is gated by the circuit breaker (Layer 1 Hard Safety); other operations
    (close, cancel, emergency) are always allowed since they are part of the
    safety-release mechanism.

    The ``execute_signal`` method provides Layer 2 Guard (confidence threshold,
    volatility detection, soft circuit breaker) wrapping a callable that
    executes the signal pipeline (e.g. ``ExecuteSignalUseCase.execute``).

    Args:
        order_client: The underlying exchange adapter (single instance).
        is_halted:    Callable that returns True when the circuit breaker
                      has halted trading.
    """

    def __init__(
        self,
        order_client: ExchangeOrderClient,
        is_halted: IsHaltedFn,
        confidence_threshold: float = 0.70,
        soft_pause_failures: int = 3,
        soft_pause_seconds: float = 30.0,
        atr_window: int = 20,
    ) -> None:
        self._client = order_client
        self._is_halted = is_halted
        self._confidence_threshold = confidence_threshold
        self._soft_pause_failures = soft_pause_failures
        self._soft_pause_seconds = soft_pause_seconds

        self._consecutive_failures = 0
        self._paused_until: float = 0.0
        self._atr_ratios: deque[float] = deque(maxlen=atr_window)
        self._rejected_low_confidence = 0
        self._volatility_reductions = 0
        self._soft_pauses_triggered = 0

    # ── ExchangeOrderClient protocol ─────────────────────────────────

    async def cancel_all_orders(self) -> int:
        return await self._client.cancel_all_orders()

    async def close_all_positions(self) -> list[PositionSummary]:
        return await self._client.close_all_positions()

    async def close_position(self, symbol: str) -> PositionSummary:
        return await self._client.close_position(symbol)

    async def close_partial(self, symbol: str, quantity: Decimal) -> None:
        return await self._client.close_partial(symbol, quantity)

    async def place_composite_order(self, order: CompositeOrder) -> OrderResult:
        if await self._is_halted():
            raise ExchangeOrderClientError("circuit breaker HALTED — order rejected")
        return await self._client.place_composite_order(order)

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        return await self._client.cancel_order(order_id, symbol)

    async def place_stop_loss_order(
        self,
        symbol: str,
        side: OrderSide,
        amount: Decimal,
        stop_price: Decimal,
    ) -> OrderResult:
        return await self._client.place_stop_loss_order(symbol, side, amount, stop_price)

    async def fetch_open_orders(self, symbol: str) -> list[OrderResult]:
        return await self._client.fetch_open_orders(symbol)

    async def place_emergency_market(
        self,
        symbol: str,
        side: OrderSide,
        amount: Decimal,
    ) -> OrderResult:
        return await self._client.place_emergency_market(symbol, side, amount)

    # Extra helpers (not in ExchangeOrderClient protocol but present on adapters)
    async def get_price(self, symbol: str) -> Decimal:
        if hasattr(self._client, "get_price"):
            return await self._client.get_price(symbol)  # type: ignore[union-attr]
        from decimal import Decimal
        return Decimal("0")

    # ── Signal execution with guard ──────────────────────────────────

    async def execute_signal(
        self,
        signal: ExecutionSignal,
        execute_fn: Callable[[ExecutionSignal], Awaitable[object]],
    ) -> object:
        """Layer 1 + 2: Hard Safety → Guard → delegated execution.

        Args:
            signal:     The trading signal to execute.
            execute_fn: Callable that performs the actual execution
                        (typically ``ExecuteSignalUseCase.execute``).

        Returns:
            The result from ``execute_fn``, or a ``_GuardRejected``
            instance if the guard blocked execution.
        """
        now = time.monotonic()

        # Layer 1: Hard Safety — circuit breaker
        if await self._is_halted():
            return _GuardRejected("circuit_breaker_halted")

        # Layer 2: Guard — soft circuit breaker pause
        if now < self._paused_until:
            remaining = self._paused_until - now
            log.info(
                "authority_paused",
                remaining_s=round(remaining, 1),
                reason="soft_circuit_breaker",
            )
            return _GuardRejected("soft_circuit_breaker_active")

        # Layer 2: Guard — confidence threshold
        if signal.confidence < self._confidence_threshold:
            self._rejected_low_confidence += 1
            log.info(
                "authority_rejected_low_confidence",
                confidence=signal.confidence,
                threshold=self._confidence_threshold,
                signal_id=signal.signal_id,
            )
            return _GuardRejected(
                f"low_confidence: {signal.confidence:.3f} < {self._confidence_threshold}"
            )

        # Layer 2: Guard — volatility-based risk multiplier
        risk_mult = self.volatility_reduction_factor()
        signal.metadata["risk_multiplier"] = risk_mult

        # Layer 3: Delegated execution
        try:
            result = await execute_fn(signal)
        except Exception as e:
            self._consecutive_failures += 1
            log.warning(
                "authority_execution_failure",
                consecutive=self._consecutive_failures,
                error=str(e),
            )
            self._check_soft_pause()
            return _GuardRejected(f"execution_error: {e}")

        # Track ATR/price ratio for volatility detection
        atr_ratio = signal.metadata.get("atr_price_ratio")
        if atr_ratio is not None:
            self.report_atr_ratio(atr_ratio)

        if self._is_rejected(result):
            self._consecutive_failures += 1
            self._check_soft_pause()
        else:
            self._consecutive_failures = 0

        return result

    # ── Guard helpers ────────────────────────────────────────────────

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
                "authority_volatility_spike",
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

    # ── Private ──────────────────────────────────────────────────────

    def _check_soft_pause(self) -> None:
        if self._consecutive_failures >= self._soft_pause_failures:
            self._paused_until = time.monotonic() + self._soft_pause_seconds
            self._soft_pauses_triggered += 1
            log.warning(
                "authority_soft_pause",
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
