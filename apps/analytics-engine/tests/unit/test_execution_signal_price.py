"""Tests para verificar que ExecutionSignal.price no llega como None.

Valida el flujo completo:
  StreamingSignalProcessor.process(price=...)
  → ExecutionSignal.price != None
  → ExecuteSignalUseCase.execute() recibe precio válido
"""
from decimal import Decimal

import pytest

from app.application.use_cases.execute_signal import (
    ExecuteSignalUseCase, ExecuteSignalError,
)
from app.domain.entities.signal_validator import SignalValidator
from app.domain.value_objects.execution_signal import ExecutionSignal
from app.domain.value_objects.order import OrderResult, OrderStatus, OrderType, OrderSide
from app.domain.value_objects.signal_side import SignalSide
from app.domain.value_objects.trading_signal import TradingSignal
from app.infrastructure.execution import InMemoryPositionRepository
from app.infrastructure.trading.streaming_signal_processor import (
    StreamingSignalProcessor,
)


class _MockBalanceProvider:
    async def get_free_balance(self, symbol: str) -> Decimal:
        return Decimal("10000")


class _MockAtrCalculator:
    async def get_atr(self, symbol: str, timeframe: str = "1m", window: int = 14):
        from app.domain.value_objects.atr import Atr
        return Atr(500)


class _MockExchangeClient:
    async def place_composite_order(self, order) -> OrderResult:
        return OrderResult(
            id="mock-order-1",
            symbol=order.symbol,
            side=order.side,
            type=OrderType.MARKET,
            filled_amount=order.entry_amount,
            avg_price=order.entry_price or Decimal("60000"),
            status=OrderStatus.FILLED,
        )

    async def cancel_all_orders(self) -> int:
        return 0

    async def close_all_positions(self) -> list:
        return []

    async def close_position(self, symbol: str) -> None:
        pass

    async def place_emergency_market(self, symbol: str, side, amount) -> OrderResult:
        return OrderResult(
            id="mock-emergency", symbol=symbol, side=side,
            type=OrderType.MARKET, filled_amount=amount,
            status=OrderStatus.FILLED,
        )


class _MockLogger:
    def __init__(self):
        self.executions = []
        self.rejections = []
        self.monitoring = []

    async def log_execution(self, report) -> None:
        self.executions.append(report)

    async def log_rejection(self, signal_id: str, reason: str) -> None:
        self.rejections.append((signal_id, reason))

    async def log_monitoring(self, pid, pnl, price) -> None:
        self.monitoring.append((pid, pnl, price))

    async def recent(self, limit=20) -> list:
        return []


async def _not_halted() -> bool:
    return False


class TestExecutionSignalPriceFlow:
    """P0: Verificar que el precio fluye correctamente desde el pipeline hasta ExecuteSignalUseCase."""

    def test_processor_injects_price_buy(self):
        """StreamingSignalProcessor debe inyectar el precio en la señal BUY."""
        processor = StreamingSignalProcessor(symbol="BTC/USDT")
        trading_signal = TradingSignal(
            side=SignalSide.BUY,
            confidence=0.85,
            model_version="test-v1",
        )
        result = processor.process(trading_signal, price=Decimal("65432.10"))
        assert result.accepted
        assert result.execution_signal is not None
        assert result.execution_signal.price == Decimal("65432.10")
        assert result.execution_signal.side == SignalSide.BUY

    def test_processor_injects_price_sell(self):
        """StreamingSignalProcessor debe inyectar el precio en la señal SELL."""
        processor = StreamingSignalProcessor(symbol="ETH/USDT")
        trading_signal = TradingSignal(
            side=SignalSide.SELL,
            confidence=0.88,
            model_version="test-v1",
        )
        result = processor.process(trading_signal, price=Decimal("3456.78"))
        assert result.accepted
        assert result.execution_signal is not None
        assert result.execution_signal.price == Decimal("3456.78")
        assert result.execution_signal.side == SignalSide.SELL

    def test_processor_default_price_is_none(self):
        """Si no se pasa precio, debe ser None (backward compat)."""
        processor = StreamingSignalProcessor(symbol="BTC/USDT")
        trading_signal = TradingSignal(
            side=SignalSide.BUY,
            confidence=0.85,
            model_version="test-v1",
        )
        result = processor.process(trading_signal)
        assert result.accepted
        assert result.execution_signal is not None
        assert result.execution_signal.price is None

    async def test_execute_usecase_receives_price_buy(self):
        """ExecuteSignalUseCase debe recibir y usar el precio en BUY."""
        uc = ExecuteSignalUseCase(
            signal_validator=SignalValidator(min_confidence=0.0),
            balance_provider=_MockBalanceProvider(),
            atr_calculator=_MockAtrCalculator(),
            exchange_client=_MockExchangeClient(),
            position_repo=InMemoryPositionRepository(),
            execution_logger=_MockLogger(),
            is_halted=_not_halted,
        )
        sig = ExecutionSignal(
            side=SignalSide.BUY,
            confidence=0.85,
            symbol="BTC/USDT",
            price=Decimal("65432.10"),
        )
        result = await uc.execute(sig)
        assert result.report.status == "FILLED"
        assert result.position is not None
        assert result.position.entry_price == Decimal("65432.10")

    async def test_execute_usecase_receives_price_sell(self):
        """ExecuteSignalUseCase debe recibir y usar el precio en SELL."""
        uc = ExecuteSignalUseCase(
            signal_validator=SignalValidator(min_confidence=0.0),
            balance_provider=_MockBalanceProvider(),
            atr_calculator=_MockAtrCalculator(),
            exchange_client=_MockExchangeClient(),
            position_repo=InMemoryPositionRepository(),
            execution_logger=_MockLogger(),
            is_halted=_not_halted,
        )
        sig = ExecutionSignal(
            side=SignalSide.SELL,
            confidence=0.88,
            symbol="ETH/USDT",
            price=Decimal("3456.78"),
        )
        result = await uc.execute(sig)
        assert result.report.status == "FILLED"
        assert result.report.side == OrderSide.SELL
        assert result.position is not None
        assert result.position.entry_price == Decimal("3456.78")

    async def test_execute_usecase_raises_without_price(self):
        """ExecuteSignalUseCase debe rechazar señal sin precio."""
        uc = ExecuteSignalUseCase(
            signal_validator=SignalValidator(min_confidence=0.0),
            balance_provider=_MockBalanceProvider(),
            atr_calculator=_MockAtrCalculator(),
            exchange_client=_MockExchangeClient(),
            position_repo=InMemoryPositionRepository(),
            execution_logger=_MockLogger(),
            is_halted=_not_halted,
        )
        sig = ExecutionSignal(
            side=SignalSide.BUY,
            confidence=0.85,
            symbol="BTC/USDT",
            price=None,
        )
        with pytest.raises(ExecuteSignalError, match="signal has no price"):
            await uc.execute(sig)

    async def test_full_pipeline_price_flow(self):
        """Simula el pipeline completo: TradingSignal → StreamingSignalProcessor → ExecuteSignalUseCase."""
        processor = StreamingSignalProcessor(symbol="BTC/USDT")
        trading_signal = TradingSignal(
            side=SignalSide.BUY,
            confidence=0.85,
            model_version="test-v1",
        )
        candle_close = Decimal("65432.10")

        proc_result = processor.process(trading_signal, price=candle_close)
        assert proc_result.accepted
        assert proc_result.execution_signal is not None
        assert proc_result.execution_signal.price == candle_close

        uc = ExecuteSignalUseCase(
            signal_validator=SignalValidator(min_confidence=0.0),
            balance_provider=_MockBalanceProvider(),
            atr_calculator=_MockAtrCalculator(),
            exchange_client=_MockExchangeClient(),
            position_repo=InMemoryPositionRepository(),
            execution_logger=_MockLogger(),
            is_halted=_not_halted,
        )
        result = await uc.execute(proc_result.execution_signal)
        assert result.report.status == "FILLED"
        assert result.position is not None
        assert result.position.entry_price == candle_close
