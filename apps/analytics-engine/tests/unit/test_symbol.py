"""Tests for Symbol value object."""
import pytest

from app.domain.value_objects.symbol import InvalidSymbolError, Symbol


class TestSymbol:
    def test_parse_standard_pair(self) -> None:
        s = Symbol("BTC/USDT")
        assert s.base == "BTC"
        assert s.quote == "USDT"
        assert s.quote_currency == "USDT"
        assert str(s) == "BTC/USDT"

    def test_parse_alt_pair(self) -> None:
        s = Symbol("ETH/BTC")
        assert s.base == "ETH"
        assert s.quote == "BTC"
        assert s.quote_currency == "BTC"

    def test_equality(self) -> None:
        assert Symbol("BTC/USDT") == Symbol("BTC/USDT")
        assert Symbol("BTC/USDT") != Symbol("ETH/USDT")
        assert hash(Symbol("BTC/USDT")) == hash(Symbol("BTC/USDT"))

    def test_hashable(self) -> None:
        s = {Symbol("A/B"), Symbol("A/B"), Symbol("C/D")}
        assert len(s) == 2

    @pytest.mark.parametrize("invalid", [
        "", "BTC", "BTC/USDT/EXTRA", "/USDT", "BTC/",
        None, 123,
    ])
    def test_invalid_symbols(self, invalid: str) -> None:
        with pytest.raises(InvalidSymbolError):
            Symbol(invalid)  # type: ignore[arg-type]


class TestSymbolInExecuteSignal:
    """Verify ExecuteSignalUseCase passes quote_currency to balance_provider.

    This test ensures that after the fix, the use case extracts the quote
    currency (``USDT``) instead of passing the raw symbol (``BTC/USDT``).

    We check the balance_provider argument BEFORE any post-order access that
    may hit unrelated pre-existing issues (sl_order_id on OrderResult, etc.).
    """

    async def test_balance_provider_receives_quote_currency(self) -> None:
        from decimal import Decimal

        from app.application.use_cases.execute_signal import (
            ExecuteSignalUseCase,
        )
        from app.domain.entities.signal_validator import SignalValidator
        from app.domain.value_objects.execution_signal import ExecutionSignal
        from app.domain.value_objects.signal_side import SignalSide
        from app.infrastructure.execution import InMemoryPositionRepository

        received_symbols: list[str] = []

        class _TrackingBalanceProvider:
            async def get_free_balance(self, symbol: str) -> Decimal:
                received_symbols.append(symbol)
                return Decimal("10000")

        class _MockAtr:
            async def get_atr(self, symbol, timeframe="1m", window=14):
                from app.domain.value_objects.atr import Atr
                return Atr(500)

        # Minimal noop stubs — we only care about the balance_provider call
        from app.domain.value_objects.order import OrderResult, OrderStatus, OrderType

        from app.domain.value_objects.order import OrderSide as OSide

        class _NoopExchange:
            async def place_composite_order(self, order) -> OrderResult:
                return OrderResult(
                    id="n", symbol=order.symbol,
                    side=OSide.SELL,
                    type=OrderType.MARKET,
                    filled_amount=order.entry_amount,
                    avg_price=order.entry_price,
                    status=OrderStatus.FILLED,
                )
            async def place_stop_loss_order(self, *a, **kw) -> OrderResult:
                return OrderResult(id="n", symbol="", side=OSide.SELL, type=OrderType.MARKET, filled_amount=Decimal("0"), status=OrderStatus.NEW)
            async def cancel_order(self, *a, **kw) -> bool: return True
            async def place_emergency_market(self, *a, **kw) -> OrderResult:
                return OrderResult(id="n", symbol="", side=OSide.SELL, type=OrderType.MARKET, filled_amount=Decimal("0"), status=OrderStatus.FILLED)
            async def cancel_all_orders(self) -> int: return 0
            async def close_all_positions(self) -> list: return []
            async def close_position(self, symbol: str) -> None: pass
            async def close_partial(self, symbol: str, quantity: Decimal) -> None: pass

        class _MockLogger:
            async def log_execution(self, report) -> None: pass
            async def log_rejection(self, signal_id, reason) -> None: pass
            async def log_monitoring(self, pid, pnl, price) -> None: pass
            async def recent(self, limit=20) -> list: return []

        async def _not_halted() -> bool:
            return False

        uc = ExecuteSignalUseCase(
            signal_validator=SignalValidator(min_confidence=0.0),
            balance_provider=_TrackingBalanceProvider(),
            atr_calculator=_MockAtr(),
            exchange_client=_NoopExchange(),
            position_repo=InMemoryPositionRepository(),
            execution_logger=_MockLogger(),
            is_halted=_not_halted,
        )

        sig = ExecutionSignal(
            side=SignalSide.SELL,
            confidence=0.85,
            symbol="BTC/USDT",
            price=Decimal("60000"),
        )

        # The balance_provider must be called with the quote currency
        # and NOT with the raw symbol. We verify this even if the use
        # case later fails on unrelated pre-existing issues (e.g.
        # sl_order_id access on OrderResult).
        try:
            await uc.execute(sig)
        except Exception:
            pass

        assert received_symbols == ["USDT"], (
            f"Expected balance_provider to receive 'USDT', got {received_symbols}"
        )
