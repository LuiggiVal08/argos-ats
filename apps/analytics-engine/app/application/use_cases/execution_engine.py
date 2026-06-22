"""ExecutionEngine — pipeline completo: Signal → Risk → Portfolio → Execution.

Pipeline:
  1. SignalValidator (confidence, cooldown, dedup)
  2. CircuitBreaker (is_halted?)
  3. RiskEngine (drawdown, consecutive losses, positions, exposure)
  4. PortfolioManager (heat, total exposure, symbol weight, correlation)
  5. Compute position size (balance + ATR)
  6. Place composite order (market entry + SL + TP)
  7. Persist position
  8. Log execution

If ANY step rejects, the entire pipeline aborts with a descriptive error.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Awaitable, Callable
from uuid import uuid4

from ...domain.entities.portfolio_manager import (
    PortfolioManager,
    PortfolioState as PortfolioManagerState,
    PortfolioVerdict,
)
from ...domain.entities.position_manager import PositionManager
from ...domain.entities.risk_engine import (
    RiskEngine,
    PortfolioState as RiskEngineState,
    RiskVerdict,
)
from ...domain.entities.signal_validator import SignalValidator
from ...domain.value_objects.execution_report import ExecutionReport
from ...domain.value_objects.execution_signal import ExecutionSignal
from ...domain.value_objects.live_position import LivePosition
from ...domain.value_objects.order import CompositeOrder, OrderSide
from ...domain.value_objects.symbol import Symbol
from ..ports.atr_calculator import AtrCalculator
from ..ports.balance_provider import BalanceProvider
from ..ports.exchange_order_client import ExchangeOrderClient
from ..ports.execution_logger import ExecutionLogger
from ..ports.position_repository import PositionRepository

IsHaltedFn = Callable[[], Awaitable[bool]]


class ExecutionEngineError(RuntimeError):
    """Raised when any step of the pipeline aborts."""


@dataclass(frozen=True)
class ExecutionResult:
    approved: bool
    report: ExecutionReport | None = None
    position: LivePosition | None = None
    risk_verdict: str | None = None
    portfolio_verdict: str | None = None
    reason: str = ""


class ExecutionEngine:
    """Top-level orchestrator: validates, assesses risk, checks portfolio,
    sizes, and executes trades.

    Args:
        signal_validator:   Validates signal confidence/cooldown/dedup.
        balance_provider:   Provides free balance for the symbol.
        atr_calculator:     Computes ATR for dynamic SL/TP.
        exchange_client:    Places orders on the exchange.
        position_repo:      Persists positions.
        execution_logger:   Logs execution events.
        is_halted:          Returns True if circuit breaker is HALTED.
        position_manager:   Domain entity for position decisions.
        risk_engine:        Domain entity for risk checks.
        portfolio_manager:  Domain entity for portfolio-level checks.
        risk_pct:           Fraction of balance to risk per trade.
        sl_atr_multiplier:  ATR multiplier for stop-loss distance.
        tp_atr_multiplier:  ATR multiplier for take-profit distance.
        consecutive_losses: Number of consecutive losses (for RiskEngine).
        daily_starting_balance: Balance at UTC 00:00 (for drawdown check).
        peak_balance:       Peak balance for portfolio heat calculation.
    """

    def __init__(
        self,
        signal_validator: SignalValidator,
        balance_provider: BalanceProvider,
        atr_calculator: AtrCalculator,
        exchange_client: ExchangeOrderClient,
        position_repo: PositionRepository,
        execution_logger: ExecutionLogger,
        is_halted: IsHaltedFn,
        position_manager: PositionManager | None = None,
        risk_engine: RiskEngine | None = None,
        portfolio_manager: PortfolioManager | None = None,
        risk_pct: float = 0.01,
        sl_atr_multiplier: float = 1.5,
        tp_atr_multiplier: float = 3.0,
        consecutive_losses: int = 0,
        daily_starting_balance: Decimal | None = None,
        peak_balance: Decimal | None = None,
    ) -> None:
        self._validator = signal_validator
        self._balance_provider = balance_provider
        self._atr_calculator = atr_calculator
        self._exchange = exchange_client
        self._position_repo = position_repo
        self._logger = execution_logger
        self._is_halted = is_halted
        self._position_manager = position_manager or PositionManager()
        self._risk_engine = risk_engine or RiskEngine()
        self._portfolio_manager = portfolio_manager or PortfolioManager()
        self._risk_pct = Decimal(str(risk_pct))
        self._sl_mult = Decimal(str(sl_atr_multiplier))
        self._tp_mult = Decimal(str(tp_atr_multiplier))
        self._consecutive_losses = consecutive_losses
        self._daily_starting_balance = daily_starting_balance
        self._peak_balance = peak_balance

    async def execute(self, signal: ExecutionSignal) -> ExecutionResult:
        validation = self._validator.validate(signal)
        if not validation.valid:
            await self._logger.log_rejection(signal.signal_id, validation.message)
            return ExecutionResult(
                approved=False,
                reason=f"signal rejected: {validation.message}",
            )

        if await self._is_halted():
            msg = f"circuit breaker HALTED, rejecting signal {signal.signal_id}"
            await self._logger.log_rejection(signal.signal_id, msg)
            return ExecutionResult(
                approved=False,
                reason=msg,
            )

        try:
            quote_currency = Symbol(signal.symbol).quote_currency
            balance = await self._balance_provider.get_free_balance(quote_currency)
            atr_value = await self._atr_calculator.get_atr(signal.symbol)
            atr = atr_value.value
        except Exception as e:
            await self._logger.log_rejection(
                signal.signal_id, f"market data error: {e}"
            )
            return ExecutionResult(
                approved=False,
                reason=f"failed to fetch market data: {e}",
            )

        open_positions = await self._position_repo.list_open()
        total_exposure = sum(
            (p.units * p.entry_price) for p in open_positions if p.is_open
        )

        risk_state = RiskEngineState(
            total_balance=balance,
            positions=open_positions,
            consecutive_losses=self._consecutive_losses,
            daily_starting_balance=self._daily_starting_balance,
        )
        risk_assessment = self._risk_engine.assess(risk_state, symbol=signal.symbol)
        if risk_assessment.verdict != RiskVerdict.APPROVED:
            await self._logger.log_rejection(
                signal.signal_id, f"risk rejected: {risk_assessment.reason}"
            )
            return ExecutionResult(
                approved=False,
                reason=risk_assessment.reason,
                risk_verdict=risk_assessment.verdict.value,
            )

        portfolio_state = PortfolioManagerState(
            positions=open_positions,
            total_balance=balance,
            peak_balance=self._peak_balance,
        )
        portfolio_assessment = self._portfolio_manager.assess(
            portfolio_state, symbol=signal.symbol
        )
        if portfolio_assessment.verdict != PortfolioVerdict.APPROVED:
            await self._logger.log_rejection(
                signal.signal_id,
                f"portfolio rejected: {portfolio_assessment.reason}",
            )
            return ExecutionResult(
                approved=False,
                reason=portfolio_assessment.reason,
                portfolio_verdict=portfolio_assessment.verdict.value,
            )

        entry_price = signal.price
        if entry_price is None or entry_price <= 0:
            await self._logger.log_rejection(
                signal.signal_id, "invalid or missing entry price"
            )
            return ExecutionResult(
                approved=False,
                reason="invalid entry_price — price provider required",
            )

        risk_amount = balance * self._risk_pct
        sl_distance = max(atr * self._sl_mult, entry_price * Decimal("0.005"))
        units = risk_amount / sl_distance
        if units <= 0:
            await self._logger.log_rejection(
                signal.signal_id, "computed position size is zero"
            )
            return ExecutionResult(
                approved=False,
                reason="computed position size is zero",
            )

        side = (
            OrderSide.BUY
            if signal.side.name == "BUY"
            else OrderSide.SELL
        )
        if side == OrderSide.BUY:
            sl_price = entry_price - sl_distance
            tp_price = entry_price + (atr * self._tp_mult)
        else:
            sl_price = entry_price + sl_distance
            tp_price = entry_price - (atr * self._tp_mult)

        if side == OrderSide.BUY:
            sl_price = max(sl_price, Decimal("0.01"))
            tp_price = max(tp_price, sl_price + Decimal("0.01"))
        else:
            sl_price = max(sl_price, entry_price + Decimal("0.01"))
            tp_price = min(tp_price, entry_price - Decimal("0.01"))
            tp_price = max(tp_price, Decimal("0.01"))

        order = CompositeOrder(
            symbol=signal.symbol,
            side=side,
            entry_amount=units,
            entry_price=entry_price,
            sl_price=sl_price,
            tp_price=tp_price,
        )
        try:
            order_result = await self._exchange.place_composite_order(order)
        except Exception as e:
            await self._logger.log_rejection(
                signal.signal_id, f"order placement failed: {e}"
            )
            return ExecutionResult(
                approved=False,
                reason=f"order placement failed: {e}",
            )

        position = LivePosition(
            position_id=uuid4().hex[:12],
            symbol=signal.symbol,
            side=side,
            units=order_result.filled_amount,
            entry_price=order_result.avg_price or entry_price,
            current_price=order_result.avg_price or entry_price,
            sl_price=sl_price,
            tp_price=tp_price,
        )
        await self._position_repo.save(position)

        report = ExecutionReport(
            report_id=uuid4().hex[:12],
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            side=side,
            status=order_result.status.value,
            filled_qty=order_result.filled_amount,
            avg_price=order_result.avg_price,
            order_id=order_result.id,
            position_id=position.position_id,
        )
        await self._logger.log_execution(report)

        return ExecutionResult(
            approved=True,
            report=report,
            position=position,
            risk_verdict="APPROVED",
            portfolio_verdict="APPROVED",
            reason="all_checks_passed",
        )
