"""Tests para ExecuteSignalUseCase."""
from decimal import Decimal

import pytest

from app.domain.value_objects.atr import Atr

from app.application.use_cases.execute_signal import (
    ExecuteSignalUseCase, ExecuteSignalError,
)
from app.domain.entities.signal_validator import SignalValidator
from app.domain.value_objects.execution_signal import ExecutionSignal
from app.domain.value_objects.order import OrderResult, OrderStatus, OrderType, OrderSide
from app.domain.value_objects.signal_side import SignalSide
from app.infrastructure.execution import InMemoryPositionRepository


class _MockBalanceProvider:
    async def get_free_balance(self, symbol: str) -> Decimal:
        return Decimal("10000")


class _MockAtrCalculator:
    async def get_atr(self, symbol: str, timeframe: str = "1m", window: int = 14) -> Atr:
        return Atr(500)


class _MockExchangeClient:
    def __init__(self):
        self.cancelled_orders: list[str] = []
        self.placed_sl_orders: list[dict] = []

    async def place_composite_order(self, order) -> OrderResult:
        return OrderResult(
            id="mock-order-1",
            symbol=order.symbol,
            side=order.side,
            type=OrderType.MARKET,
            filled_amount=order.entry_amount,
            avg_price=order.entry_price or Decimal("60000"),
            status=OrderStatus.FILLED,
            sl_order_id="mock-sl-original",
        )

    async def place_stop_loss_order(self, symbol, side, amount, stop_price) -> OrderResult:
        self.placed_sl_orders.append({"symbol": symbol, "side": side, "stop_price": stop_price})
        return OrderResult(
            id=f"mock-sl-{len(self.placed_sl_orders)}",
            symbol=symbol,
            side=side,
            type=OrderType.STOP_LOSS_MARKET,
            filled_amount=amount,
            status=OrderStatus.NEW,
        )

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        self.cancelled_orders.append(order_id)
        return True

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

    async def close_partial(self, symbol: str, quantity: Decimal) -> None:
        pass


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


class TestExecuteSignalUseCase:
    @pytest.fixture
    def use_case(self):
        return ExecuteSignalUseCase(
            signal_validator=SignalValidator(min_confidence=0.0),
            balance_provider=_MockBalanceProvider(),
            atr_calculator=_MockAtrCalculator(),
            exchange_client=_MockExchangeClient(),
            position_repo=InMemoryPositionRepository(),
            execution_logger=_MockLogger(),
            is_halted=_not_halted,
        )

    async def test_happy_path(self, use_case):
        sig = ExecutionSignal(
            side=SignalSide.BUY, confidence=0.85, symbol="BTC/USDT",
            price=Decimal("60000"),
        )
        result = await use_case.execute(sig)
        assert result.report.status == "FILLED"
        assert result.report.symbol == "BTC/USDT"
        assert result.position is not None
        assert result.position.units > 0

    async def test_rejected_signal(self, use_case):
        sv = SignalValidator(min_confidence=0.9)
        uc = ExecuteSignalUseCase(
            signal_validator=sv,
            balance_provider=_MockBalanceProvider(),
            atr_calculator=_MockAtrCalculator(),
            exchange_client=_MockExchangeClient(),
            position_repo=InMemoryPositionRepository(),
            execution_logger=_MockLogger(),
            is_halted=_not_halted,
        )
        sig = ExecutionSignal(
            side=SignalSide.BUY, confidence=0.5, symbol="BTC/USDT",
            price=Decimal("60000"),
        )
        with pytest.raises(ExecuteSignalError, match="rejected"):
            await uc.execute(sig)

    async def test_circuit_breaker_halted(self):
        async def _halted() -> bool:
            return True
        uc = ExecuteSignalUseCase(
            signal_validator=SignalValidator(min_confidence=0.0),
            balance_provider=_MockBalanceProvider(),
            atr_calculator=_MockAtrCalculator(),
            exchange_client=_MockExchangeClient(),
            position_repo=InMemoryPositionRepository(),
            execution_logger=_MockLogger(),
            is_halted=_halted,
        )
        sig = ExecutionSignal(
            side=SignalSide.BUY, confidence=0.85, symbol="BTC/USDT",
            price=Decimal("60000"),
        )
        with pytest.raises(ExecuteSignalError, match="HALTED"):
            await uc.execute(sig)

    async def test_position_persisted(self, use_case):
        repo = InMemoryPositionRepository()
        uc = ExecuteSignalUseCase(
            signal_validator=SignalValidator(min_confidence=0.0),
            balance_provider=_MockBalanceProvider(),
            atr_calculator=_MockAtrCalculator(),
            exchange_client=_MockExchangeClient(),
            position_repo=repo,
            execution_logger=_MockLogger(),
            is_halted=_not_halted,
        )
        sig = ExecutionSignal(
            side=SignalSide.SELL, confidence=0.85, symbol="ETH/USDT",
            price=Decimal("3000"),
        )
        result = await uc.execute(sig)
        loaded = await repo.load(result.position.position_id)
        assert loaded is not None
        assert loaded.symbol == "ETH/USDT"

    async def test_short_position(self, use_case):
        sig = ExecutionSignal(
            side=SignalSide.SELL, confidence=0.85, symbol="BTC/USDT",
            price=Decimal("60000"),
        )
        result = await use_case.execute(sig)
        assert result.report.side == OrderSide.SELL
        assert result.position.side == OrderSide.SELL

    # ── Fix 1: Price Reconciliation tests ─────────────────────────

    async def test_price_reconciliation_corrects_sl_on_divergence(self):
        """When fill price diverges >0.3% from signal price, SL should be
        recalculated from fill price and a corrected SL order placed."""
        exchange = _MockExchangeClient()

        class _DivergentMockAtr:
            async def get_atr(self, symbol, timeframe="1m", window=14):
                return Atr(500)

        uc = ExecuteSignalUseCase(
            signal_validator=SignalValidator(min_confidence=0.0),
            balance_provider=_MockBalanceProvider(),
            atr_calculator=_DivergentMockAtr(),
            exchange_client=exchange,
            position_repo=InMemoryPositionRepository(),
            execution_logger=_MockLogger(),
            is_halted=_not_halted,
        )
        sig = ExecutionSignal(
            side=SignalSide.BUY, confidence=0.85, symbol="BTC/USDT",
            price=Decimal("60000"),
        )
        # Mock avg_price to be 0.5% higher (60300 vs 60000 = 0.5%)
        orig_place = exchange.place_composite_order

        async def _divergent_place(order):
            result = await orig_place(order)
            from dataclasses import replace
            return replace(result, avg_price=Decimal("60300"))
        exchange.place_composite_order = _divergent_place

        result = await uc.execute(sig)
        assert result.position is not None

        # A corrected SL should have been placed
        assert len(exchange.placed_sl_orders) == 1, "corrected SL must be placed"
        corrected = exchange.placed_sl_orders[0]
        assert corrected["side"] == OrderSide.SELL

        # Original SL should have been cancelled
        assert "mock-sl-original" in exchange.cancelled_orders, "original SL must be cancelled"

        # Corrected SL price should be closer to fill (60300) than to signal (60000)
        # Original: sl = 60000 - max(500*1.5, 60000*0.005) = 60000 - 750 = 59250
        # Corrected: sl = 60300 - max(500*1.5, 60300*0.005) = 60300 - 750 = 59550
        assert corrected["stop_price"] == Decimal("59550"), (
            f"expected 59550, got {corrected['stop_price']}"
        )

    async def test_price_reconciliation_no_action_on_minor_divergence(self):
        """Divergence <= 0.3% should NOT trigger SL correction."""
        exchange = _MockExchangeClient()

        uc = ExecuteSignalUseCase(
            signal_validator=SignalValidator(min_confidence=0.0),
            balance_provider=_MockBalanceProvider(),
            atr_calculator=_MockAtrCalculator(),
            exchange_client=exchange,
            position_repo=InMemoryPositionRepository(),
            execution_logger=_MockLogger(),
            is_halted=_not_halted,
        )
        sig = ExecutionSignal(
            side=SignalSide.BUY, confidence=0.85, symbol="BTC/USDT",
            price=Decimal("60000"),
        )
        orig_place = exchange.place_composite_order

        async def _minor_divergence_place(order):
            result = await orig_place(order)
            from dataclasses import replace
            return replace(result, avg_price=Decimal("60150"))  # 0.25%
        exchange.place_composite_order = _minor_divergence_place

        result = await uc.execute(sig)
        assert result.position is not None

        # No corrected SL should have been placed
        assert len(exchange.placed_sl_orders) == 0, "no correction for minor divergence"

    # ── Fix 2: Regime-aware SL tests ──────────────────────────────

    async def test_regime_trending_wider_sl(self):
        """TRENDING regime should apply wider SL (2.0 ATR via 4/3× of 1.5)."""
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
            side=SignalSide.BUY, confidence=0.85, symbol="BTC/USDT",
            price=Decimal("60000"),
            metadata={"regime": "TRENDING"},
        )
        result = await uc.execute(sig)
        assert result.position is not None
        # TRENDING: regime_adj = 4/3, sl_mult = 1.5 * 4/3 ≈ 2.0
        # sl_distance = max(500 * 2.0, 60000 * 0.005) = max(1000, 300) = 1000
        # sl = 60000 - 1000 = 59000
        # (tiny precision artifact from Decimal 4/3 → quantize for comparison)
        assert result.position.sl_price.quantize(Decimal("1")) == Decimal("59000"), (
            f"TRENDING SL expected 59000, got {result.position.sl_price}"
        )

    async def test_regime_ranging_tighter_sl(self):
        """RANGING regime should apply tighter SL (1.0 ATR via 2/3× of 1.5)."""
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
            side=SignalSide.BUY, confidence=0.85, symbol="BTC/USDT",
            price=Decimal("60000"),
            metadata={"regime": "RANGING"},
        )
        result = await uc.execute(sig)
        assert result.position is not None
        # RANGING: regime_adj = 2/3, sl_mult = 1.5 * 2/3 ≈ 1.0
        # sl_distance = max(500 * 1.0, 60000 * 0.005) = max(500, 300) = 500
        # sl = 60000 - 500 = 59500
        assert result.position.sl_price.quantize(Decimal("1")) == Decimal("59500"), (
            f"RANGING SL expected 59500, got {result.position.sl_price}"
        )

    async def test_regime_unknown_default_sl(self):
        """UNKNOWN regime should default to 1.5× ATR SL (regime_adj=1.0)."""
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
            side=SignalSide.BUY, confidence=0.85, symbol="BTC/USDT",
            price=Decimal("60000"),
        )
        result = await uc.execute(sig)
        assert result.position is not None
        # Default (no regime): sl_mult = 1.5 * 1.0 = 1.5
        # sl_distance = max(500 * 1.5, 60000 * 0.005) = max(750, 300) = 750
        # sl = 60000 - 750 = 59250
        assert result.position.sl_price == Decimal("59250"), (
            f"DEFAULT SL expected 59250, got {result.position.sl_price}"
        )


async def _not_halted() -> bool:
    return False
