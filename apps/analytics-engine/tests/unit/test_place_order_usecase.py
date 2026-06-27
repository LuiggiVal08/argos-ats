"""Unit tests for PlaceOrderUseCase with retry + emergency fallback.

Mocks ExchangeOrderClient to exercise the happy path and both
sad paths: SL failure → emergency market, and total failure.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.application.ports.exchange_order_client import (
    ExchangeOrderClient,
    ExchangeOrderClientError,
    SlPlacementError,
)
from app.application.use_cases.place_order import (
    PlaceOrderError,
    PlaceOrderUseCase,
)
from app.domain.value_objects.order import (
    CompositeOrder,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
)


def _fake_result(symbol: str = "BTC/USDT", side: OrderSide = OrderSide.BUY) -> OrderResult:
    return OrderResult(
        id="mock-1",
        symbol=symbol,
        side=side,
        type=OrderType.MARKET,
        filled_amount=Decimal("0.1"),
        avg_price=Decimal("60000"),
        status=OrderStatus.FILLED,
    )


def _make_order_client(
    composite: OrderResult | Exception,
    emergency: OrderResult | Exception | None = None,
) -> AsyncMock:
    client = AsyncMock(spec=ExchangeOrderClient)
    if isinstance(composite, Exception):
        client.place_composite_order.side_effect = composite
    else:
        client.place_composite_order.return_value = composite

    if emergency is not None:
        if isinstance(emergency, Exception):
            client.place_emergency_market.side_effect = emergency
        else:
            client.place_emergency_market.return_value = emergency
    return client


class TestPlaceOrderUseCaseHappyPath:
    @pytest.mark.asyncio
    async def test_composite_order_succeeds_no_emergency(self) -> None:
        entry = _fake_result()
        client = _make_order_client(composite=entry)
        use_case = PlaceOrderUseCase(order_client=client)

        result = await use_case.execute(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            entry_amount=Decimal("0.1"),
            sl_price=Decimal("59000"),
            tp_price=Decimal("61000"),
        )

        assert result.succeeded is True
        assert result.entry_order.id == "mock-1"
        assert result.emergency_order is None

        client.place_composite_order.assert_awaited_once()
        client.place_emergency_market.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_minimal_order_no_sl_tp(self) -> None:
        entry = _fake_result()
        client = _make_order_client(composite=entry)
        use_case = PlaceOrderUseCase(order_client=client)

        result = await use_case.execute(
            symbol="ETH/USDT",
            side=OrderSide.SELL,
            entry_amount=Decimal("1.0"),
        )

        assert result.succeeded
        client.place_composite_order.assert_awaited_once_with(
            CompositeOrder(
                symbol="ETH/USDT",
                side=OrderSide.SELL,
                entry_amount=Decimal("1.0"),
                sl_price=None,
                tp_price=None,
            )
        )


class TestPlaceOrderUseCaseSadPaths:
    @pytest.mark.asyncio
    async def test_sl_failure_triggers_emergency_market(self) -> None:
        entry = _fake_result()
        client = _make_order_client(
            composite=SlPlacementError("SL retries exhausted", entry_order=entry),
            emergency=_fake_result(symbol="BTC/USDT", side=OrderSide.SELL),
        )
        use_case = PlaceOrderUseCase(order_client=client)

        result = await use_case.execute(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            entry_amount=Decimal("0.1"),
            sl_price=Decimal("59000"),
        )

        assert result.succeeded is False
        assert result.entry_order.id == "mock-1"
        assert result.emergency_order is not None
        assert result.emergency_order.id == "mock-1"
        client.place_emergency_market.assert_awaited_once_with(
            "BTC/USDT", OrderSide.SELL, Decimal("0.1")
        )

    @pytest.mark.asyncio
    async def test_emergency_order_is_for_opposite_side(self) -> None:
        """When the entry is a BUY, the emergency must be a SELL."""
        entry = _fake_result(side=OrderSide.BUY)
        client = _make_order_client(
            composite=SlPlacementError("timeout", entry_order=entry),
            emergency=_fake_result(side=OrderSide.SELL),
        )
        use_case = PlaceOrderUseCase(order_client=client)

        result = await use_case.execute(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            entry_amount=Decimal("0.1"),
        )

        assert result.succeeded is False
        assert result.entry_order.id == "mock-1"
        client.place_emergency_market.assert_awaited_once_with(
            "BTC/USDT", OrderSide.SELL, Decimal("0.1")
        )

    @pytest.mark.asyncio
    async def test_both_sl_and_emergency_fail_raises_error(self) -> None:
        entry = _fake_result()
        client = _make_order_client(
            composite=SlPlacementError("SL retries exhausted", entry_order=entry),
            emergency=ExchangeOrderClientError("exchange offline"),
        )
        use_case = PlaceOrderUseCase(order_client=client)

        with pytest.raises(PlaceOrderError) as exc:
            await use_case.execute(
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                entry_amount=Decimal("0.1"),
            )

        assert "sl_placement_failed_and_emergency_failed" in str(exc.value)

    @pytest.mark.asyncio
    async def test_entry_order_fails_outright_raises_error(self) -> None:
        client = _make_order_client(
            composite=ExchangeOrderClientError("insufficient margin"),
        )
        use_case = PlaceOrderUseCase(order_client=client)

        with pytest.raises(PlaceOrderError) as exc:
            await use_case.execute(
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                entry_amount=Decimal("0.1"),
            )

        assert "entry_order_failed" in str(exc.value)


class TestPlaceOrderWiringContract:
    """Regression: PlaceOrderUseCase does NOT accept is_halted.

    The is_halted check belongs to ExecutionAuthority, which wraps the
    order_client and gates place_composite_order. Passing is_halted to
    PlaceOrderUseCase directly was a wiring error (composition.py:624).
    """

    def test_constructor_does_not_accept_is_halted(self):
        """PlaceOrderUseCase.__init__ must accept only order_client."""
        from dataclasses import dataclass
        from decimal import Decimal
        from app.application.ports.exchange_order_client import ExchangeOrderClient

        @dataclass
        class FakeClient(ExchangeOrderClient):
            async def place_composite_order(self, order): ...
            async def close_position(self, symbol): ...
            async def close_all_positions(self): ...
            async def cancel_all_orders(self): ...
            async def cancel_order(self, order_id, symbol): ...
            async def fetch_open_orders(self, symbol): ...
            async def fetch_position(self, symbol): ...
            async def fetch_balance(self): ...
            async def place_stop_loss_order(self, symbol, side, amount, stop_price): ...
            async def place_emergency_market(self, symbol, side, amount): ...
            async def close_partial(self, symbol, quantity): ...

        uc = PlaceOrderUseCase(FakeClient())
        assert isinstance(uc, PlaceOrderUseCase)

        with pytest.raises(TypeError, match="is_halted"):
            PlaceOrderUseCase(FakeClient(), is_halted=lambda: False)

    def test_execution_authority_handles_is_halted(self):
        """ExecutionAuthority.place_composite_order gates with is_halted.

        This is the architectural home for circuit breaker checks.
        If this test fails, the CB protection may have been moved.
        """
        from app.infrastructure.trading.execution_authority import ExecutionAuthority
        import inspect

        sig = inspect.signature(ExecutionAuthority.__init__)
        params = list(sig.parameters.keys())
        assert "is_halted" in params, (
            f"ExecutionAuthority no longer gates with is_halted. "
            f"Params: {params}"
        )
