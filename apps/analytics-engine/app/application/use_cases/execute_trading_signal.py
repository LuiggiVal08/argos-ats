"""DEPRECATED — ExecuteTradingSignalUseCase.

WARNING: This use case bypasses CircuitBreaker, RiskEngine, and
PortfolioManager invariants. It is kept for reference only and
must NOT be wired into the API or composition root.

Use ExecutionEngine or ExecuteSignalUseCase instead.

Pipeline (for reference):
  1. If signal.side is HOLD → log rejection, return skipped (no error)
  2. If not BUY or confidence < threshold → log rejection, return skipped
  3. Fetch current ATR for the symbol
  4. Compute SL = close_price - (2 × ATR), TP = close_price + (3.5 × ATR)
  5. Place market order via ExchangeOrderGateway
  6. Persist LivePosition
  7. Log execution via ExecutionLogger
  8. Return ExecuteSignalResult
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from ...domain.value_objects.execution_report import ExecutionReport
from ...domain.value_objects.live_position import LivePosition
from ...domain.value_objects.order import OrderResult, OrderSide
from ...domain.value_objects.trading_signal import TradingSignal
from ..ports.atr_calculator import AtrCalculator, AtrCalculatorError
from ..ports.execution_logger import ExecutionLogger
from ..ports.exchange_order_gateway import (
    ExchangeOrderGateway,
    ExchangeOrderGatewayError,
)
from ..ports.position_repository import PositionRepository


@dataclass(frozen=True)
class ExecuteSignalResult:
    skipped: bool = False
    reason: str = ""
    report: ExecutionReport | None = None
    position: LivePosition | None = None


class ExecuteTradingSignalUseCase:
    """Simplified execution use case for model-generated TradingSignals.

    Args:
        exchange_gateway: Port to place market orders with SL/TP.
        atr_calculator:   Port to fetch current ATR.
        position_repo:    Port to persist positions.
        execution_logger: Port to log execution events.
        confidence_threshold: Minimum confidence to execute BUY (default 0.55).
        sl_atr_mult:      ATR multiplier for stop-loss (default 2.0).
        tp_atr_mult:      ATR multiplier for take-profit (default 3.5).
    """

    def __init__(
        self,
        exchange_gateway: ExchangeOrderGateway,
        atr_calculator: AtrCalculator,
        position_repo: PositionRepository,
        execution_logger: ExecutionLogger,
        confidence_threshold: float = 0.55,
        sl_atr_mult: float = 2.0,
        tp_atr_mult: float = 3.5,
    ) -> None:
        self._gateway = exchange_gateway
        self._atr_calc = atr_calculator
        self._position_repo = position_repo
        self._logger = execution_logger
        self._threshold = Decimal(str(confidence_threshold))
        self._sl_mult = Decimal(str(sl_atr_mult))
        self._tp_mult = Decimal(str(tp_atr_mult))

    async def execute(
        self,
        signal: TradingSignal,
        symbol: str,
        close_price: Decimal,
        amount: Decimal,
    ) -> ExecuteSignalResult:
        """Run the predict→execute pipeline for a single signal.

        Args:
            signal:      TradingSignal from NovaQuant (BUY/HOLD).
            symbol:      Trading pair, e.g. "BTC/USDT".
            close_price: Current market price (used for SL/TP).
            amount:      Quantity to trade in base currency.

        Returns:
            ExecuteSignalResult (skipped=True if HOLD or below threshold).
        """
        if signal.side.name == "HOLD":
            await self._logger.log_rejection(
                "novaquant", f"HOLD signal for {symbol}, skipping"
            )
            return ExecuteSignalResult(
                skipped=True, reason=f"HOLD signal for {symbol}"
            )

        if signal.side.name != "BUY":
            await self._logger.log_rejection(
                "novaquant",
                f"unsupported side {signal.side.name} for {symbol}",
            )
            return ExecuteSignalResult(
                skipped=True,
                reason=f"unsupported side {signal.side.name} for {symbol}",
            )

        if signal.confidence < self._threshold:
            await self._logger.log_rejection(
                "novaquant",
                f"confidence {signal.confidence:.4f} < threshold "
                f"{self._threshold} for {symbol}",
            )
            return ExecuteSignalResult(
                skipped=True,
                reason=f"confidence {signal.confidence:.4f} below threshold",
            )

        try:
            atr = await self._atr_calc.get_atr(symbol)
        except AtrCalculatorError as e:
            await self._logger.log_rejection(
                "novaquant", f"ATR fetch failed for {symbol}: {e}"
            )
            return ExecuteSignalResult(
                skipped=True, reason=f"ATR unavailable: {e}"
            )

        atr_value = atr.value
        sl_price = max(close_price - (self._sl_mult * atr_value), Decimal("0.01"))
        tp_price = close_price + (self._tp_mult * atr_value)

        side = OrderSide.BUY

        try:
            order_result: OrderResult = await self._gateway.place_market_order(
                symbol=symbol,
                side=side,
                amount=amount,
                sl_price=sl_price,
                tp_price=tp_price,
            )
        except ExchangeOrderGatewayError as e:
            await self._logger.log_rejection(
                "novaquant", f"order placement failed for {symbol}: {e}"
            )
            return ExecuteSignalResult(
                skipped=True, reason=f"order failed: {e}"
            )

        filled = order_result.filled_amount
        avg_price = order_result.avg_price or close_price

        position = LivePosition(
            position_id=uuid4().hex[:12],
            symbol=symbol,
            side=side,
            units=filled,
            entry_price=avg_price,
            current_price=close_price,
            sl_price=sl_price,
            tp_price=tp_price,
        )
        await self._position_repo.save(position)

        report = ExecutionReport(
            report_id=uuid4().hex[:12],
            signal_id="novaquant",
            symbol=symbol,
            side=side,
            status=order_result.status.value,
            filled_qty=filled,
            avg_price=avg_price,
            order_id=order_result.id,
            position_id=position.position_id,
        )
        await self._logger.log_execution(report)

        return ExecuteSignalResult(report=report, position=position)
