"""Unit tests for CcxtBinanceTestnetAdapter.

All CCXT calls are mocked to avoid network I/O.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.application.ports.exchange_order_client import (
    ExchangeOrderClientError,
    SlPlacementError,
)
from app.domain.value_objects.order import (
    CompositeOrder,
    OrderSide,
    OrderStatus,
    OrderType,
)
from app.infrastructure.trading.ccxt_binance_adapter import (
    CcxtBinanceTestnetAdapter,
)


@pytest.fixture
def mock_exchange() -> MagicMock:
    """Return a mocked ccxt.Exchange with async methods."""
    ex = MagicMock()
    ex.create_order = AsyncMock()
    ex.fetch_balance = AsyncMock()
    ex.fetch_ticker = AsyncMock()
    ex.fetch_open_orders = AsyncMock()
    ex.cancel_order = AsyncMock()
    ex.fetch_positions = AsyncMock()
    ex.set_sandbox_mode = MagicMock()
    return ex


@pytest.fixture
def adapter(mock_exchange: MagicMock) -> CcxtBinanceTestnetAdapter:
    return CcxtBinanceTestnetAdapter(exchange=mock_exchange, symbols=("BTC/USDT",))


# ── get_price ──────────────────────────────────────────────────────────


class TestGetPrice:
    async def test_returns_decimal_from_ticker_last(
        self, adapter: CcxtBinanceTestnetAdapter, mock_exchange: MagicMock,
    ) -> None:
        mock_exchange.fetch_ticker.return_value = {"last": 65432.10}
        price = await adapter.get_price("BTC/USDT")
        assert price == Decimal("65432.10")
        mock_exchange.fetch_ticker.assert_awaited_once_with("BTC/USDT")

    async def test_falls_back_to_close_when_last_missing(
        self, adapter: CcxtBinanceTestnetAdapter, mock_exchange: MagicMock,
    ) -> None:
        mock_exchange.fetch_ticker.return_value = {"close": 60000}
        price = await adapter.get_price("BTC/USDT")
        assert price == Decimal("60000")

    async def test_raises_on_invalid_price(
        self, adapter: CcxtBinanceTestnetAdapter, mock_exchange: MagicMock,
    ) -> None:
        mock_exchange.fetch_ticker.return_value = {"last": 0}
        with pytest.raises(ExchangeOrderClientError, match="invalid_ticker_price"):
            await adapter.get_price("BTC/USDT")

    async def test_raises_on_network_error(
        self, adapter: CcxtBinanceTestnetAdapter, mock_exchange: MagicMock,
    ) -> None:
        mock_exchange.fetch_ticker.side_effect = ConnectionError("reset")
        with pytest.raises(ExchangeOrderClientError, match="fetch_ticker_failed"):
            await adapter.get_price("BTC/USDT")

    async def test_cache_hit_skips_second_fetch(
        self, adapter: CcxtBinanceTestnetAdapter, mock_exchange: MagicMock,
    ) -> None:
        mock_exchange.fetch_ticker.return_value = {"last": 50000.00}
        p1 = await adapter.get_price("BTC/USDT")
        assert p1 == Decimal("50000.00")
        p2 = await adapter.get_price("BTC/USDT")
        assert p2 == Decimal("50000.00")
        mock_exchange.fetch_ticker.assert_awaited_once()


# ── close_position ─────────────────────────────────────────────────────


class TestClosePosition:
    async def test_sells_base_balance(
        self, adapter: CcxtBinanceTestnetAdapter, mock_exchange: MagicMock,
    ) -> None:
        mock_exchange.fetch_balance.return_value = {
            "free": {"BTC": 0.5},
            "total": {"BTC": 0.5},
        }
        mock_exchange.create_order.return_value = {
            "id": "close-1",
            "symbol": "BTC/USDT",
            "price": 60000.00,
            "average": 60000.00,
            "filled": 0.5,
            "status": "FILLED",
        }
        summary = await adapter.close_position("BTC/USDT")
        assert summary.quantity == Decimal("0.5")
        assert summary.symbol == "BTC/USDT"
        mock_exchange.create_order.assert_awaited_once_with(
            "BTC/USDT", type="market", side="sell", amount=0.5,
        )

    async def test_raises_when_no_balance(
        self, adapter: CcxtBinanceTestnetAdapter, mock_exchange: MagicMock,
    ) -> None:
        mock_exchange.fetch_balance.return_value = {
            "free": {"BTC": 0},
            "total": {"BTC": 0},
        }
        with pytest.raises(ExchangeOrderClientError, match="no_balance_to_close"):
            await adapter.close_position("BTC/USDT")

    async def test_raises_on_balance_fetch_error(
        self, adapter: CcxtBinanceTestnetAdapter, mock_exchange: MagicMock,
    ) -> None:
        mock_exchange.fetch_balance.side_effect = ConnectionError("timeout")
        with pytest.raises(ExchangeOrderClientError, match="fetch_balance_failed"):
            await adapter.close_position("BTC/USDT")


# ── cancel_all_orders ─────────────────────────────────────────────────


class TestCancelAllOrders:
    async def test_cancels_open_orders(
        self, adapter: CcxtBinanceTestnetAdapter, mock_exchange: MagicMock,
    ) -> None:
        mock_exchange.fetch_open_orders.return_value = [
            {"id": "o1"}, {"id": "o2"},
        ]
        count = await adapter.cancel_all_orders()
        assert count == 2
        assert mock_exchange.cancel_order.await_count == 2

    async def test_raises_on_fetch_failure(
        self, adapter: CcxtBinanceTestnetAdapter, mock_exchange: MagicMock,
    ) -> None:
        mock_exchange.fetch_open_orders.side_effect = TimeoutError("slow")
        with pytest.raises(ExchangeOrderClientError, match="fetch_open_orders_failed"):
            await adapter.cancel_all_orders()

    async def test_returns_zero_when_no_orders(
        self, adapter: CcxtBinanceTestnetAdapter, mock_exchange: MagicMock,
    ) -> None:
        mock_exchange.fetch_open_orders.return_value = []
        count = await adapter.cancel_all_orders()
        assert count == 0


# ── place_composite_order ─────────────────────────────────────────────


class TestPlaceCompositeOrder:
    async def test_happy_path_entry_only(
        self, adapter: CcxtBinanceTestnetAdapter, mock_exchange: MagicMock,
    ) -> None:
        mock_exchange.create_order.return_value = {
            "id": "entry-1",
            "symbol": "BTC/USDT",
            "filled": 0.1,
            "price": 60000.00,
            "status": "FILLED",
        }
        order = CompositeOrder(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            entry_amount=Decimal("0.1"),
        )
        result = await adapter.place_composite_order(order)
        assert result.id == "entry-1"
        assert result.filled_amount == Decimal("0.1")
        assert result.status == OrderStatus.FILLED
        mock_exchange.create_order.assert_awaited_once_with(
            "BTC/USDT", type="market", side="buy", amount=0.1,
        )

    async def test_entry_failure_raises(
        self, adapter: CcxtBinanceTestnetAdapter, mock_exchange: MagicMock,
    ) -> None:
        mock_exchange.create_order.side_effect = RuntimeError("insufficient balance")
        order = CompositeOrder(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            entry_amount=Decimal("0.1"),
        )
        with pytest.raises(ExchangeOrderClientError, match="entry_order_failed"):
            await adapter.place_composite_order(order)

    async def test_sl_retry_then_success(
        self, adapter: CcxtBinanceTestnetAdapter, mock_exchange: MagicMock,
    ) -> None:
        mock_exchange.create_order.side_effect = [
            {"id": "entry-1", "symbol": "BTC/USDT", "filled": 0.1, "price": 60000, "status": "FILLED"},
            RuntimeError("timeout"),
            RuntimeError("timeout"),
            {"id": "sl-1", "filled": 0.1, "status": "NEW"},
        ]
        order = CompositeOrder(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            entry_amount=Decimal("0.1"),
            sl_price=Decimal("59000"),
        )
        result = await adapter.place_composite_order(order)
        assert result.id == "entry-1"
        assert mock_exchange.create_order.await_count == 4

    async def test_sl_retry_exhausted_raises_sl_placement_error(
        self, adapter: CcxtBinanceTestnetAdapter, mock_exchange: MagicMock,
    ) -> None:
        mock_exchange.create_order.side_effect = [
            {"id": "entry-1", "symbol": "BTC/USDT", "filled": 0.1, "price": 60000, "status": "FILLED"},
            RuntimeError("fail1"),
            RuntimeError("fail2"),
            RuntimeError("fail3"),
        ]
        order = CompositeOrder(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            entry_amount=Decimal("0.1"),
            sl_price=Decimal("59000"),
        )
        with pytest.raises(SlPlacementError) as exc:
            await adapter.place_composite_order(order)
        assert exc.value.entry_order is not None
        assert exc.value.entry_order.id == "entry-1"

    async def test_tp_best_effort_does_not_raise(
        self, adapter: CcxtBinanceTestnetAdapter, mock_exchange: MagicMock,
    ) -> None:
        mock_exchange.create_order.side_effect = [
            {"id": "entry-1", "symbol": "BTC/USDT", "filled": 0.1, "price": 60000, "status": "FILLED"},
            {"id": "sl-1", "filled": 0.1, "status": "NEW"},
            RuntimeError("tp_failed"),
        ]
        order = CompositeOrder(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            entry_amount=Decimal("0.1"),
            sl_price=Decimal("59000"),
            tp_price=Decimal("65000"),
        )
        result = await adapter.place_composite_order(order)
        assert result.id == "entry-1"


# ── place_emergency_market ────────────────────────────────────────────


class TestPlaceEmergencyMarket:
    async def test_places_market_order(
        self, adapter: CcxtBinanceTestnetAdapter, mock_exchange: MagicMock,
    ) -> None:
        mock_exchange.create_order.return_value = {
            "id": "emergency-1",
            "symbol": "BTC/USDT",
            "filled": 0.1,
            "price": 59000,
            "status": "FILLED",
        }
        result = await adapter.place_emergency_market(
            "BTC/USDT", OrderSide.SELL, Decimal("0.1"),
        )
        assert result.id == "emergency-1"
        assert result.side == OrderSide.SELL
        mock_exchange.create_order.assert_awaited_once_with(
            "BTC/USDT", type="market", side="sell", amount=0.1,
        )

    async def test_failure_raises(
        self, adapter: CcxtBinanceTestnetAdapter, mock_exchange: MagicMock,
    ) -> None:
        mock_exchange.create_order.side_effect = RuntimeError("offline")
        with pytest.raises(ExchangeOrderClientError, match="emergency_order_failed"):
            await adapter.place_emergency_market(
                "BTC/USDT", OrderSide.SELL, Decimal("0.1"),
            )


# ── close_all_positions ───────────────────────────────────────────────


class TestCloseAllPositions:
    async def test_closes_all_symbols(
        self, adapter: CcxtBinanceTestnetAdapter, mock_exchange: MagicMock,
    ) -> None:
        mock_exchange.fetch_balance.return_value = {
            "free": {"BTC": 1.0},
            "total": {"BTC": 1.0},
        }
        mock_exchange.create_order.return_value = {
            "id": "close-1",
            "symbol": "BTC/USDT",
            "price": 60000,
            "average": 60000,
            "filled": 1.0,
            "status": "FILLED",
        }
        results = await adapter.close_all_positions()
        assert len(results) == 1

    async def test_ignores_zero_balance_symbols(
        self, adapter: CcxtBinanceTestnetAdapter, mock_exchange: MagicMock,
    ) -> None:
        mock_exchange.fetch_balance.return_value = {
            "free": {"BTC": 0},
            "total": {"BTC": 0},
        }
        results = await adapter.close_all_positions()
        assert len(results) == 0


# ── close_partial ──────────────────────────────────────────────────────


class TestClosePartial:
    async def test_long_sends_sell_reduce_only(
        self, adapter: CcxtBinanceTestnetAdapter, mock_exchange: MagicMock,
    ) -> None:
        mock_exchange.fetch_positions.return_value = [
            {"symbol": "BTC/USDT", "contracts": 0.1, "side": "long"},
        ]
        mock_exchange.create_order.return_value = {
            "id": "partial-1", "symbol": "BTC/USDT",
        }
        await adapter.close_partial("BTC/USDT", Decimal("0.05"))
        mock_exchange.fetch_positions.assert_awaited_once_with(["BTC/USDT"])
        mock_exchange.create_order.assert_awaited_once_with(
            "BTC/USDT", type="market", side="sell",
            amount=0.05, params={"reduceOnly": True},
        )

    async def test_short_sends_buy_reduce_only(
        self, adapter: CcxtBinanceTestnetAdapter, mock_exchange: MagicMock,
    ) -> None:
        mock_exchange.fetch_positions.return_value = [
            {"symbol": "BTC/USDT", "contracts": -0.1, "side": "short"},
        ]
        mock_exchange.create_order.return_value = {
            "id": "partial-2", "symbol": "BTC/USDT",
        }
        await adapter.close_partial("BTC/USDT", Decimal("0.05"))
        mock_exchange.create_order.assert_awaited_once_with(
            "BTC/USDT", type="market", side="buy",
            amount=0.05, params={"reduceOnly": True},
        )

    async def test_unknown_side_raises(
        self, adapter: CcxtBinanceTestnetAdapter, mock_exchange: MagicMock,
    ) -> None:
        mock_exchange.fetch_positions.return_value = [
            {"symbol": "BTC/USDT", "contracts": 0.1, "side": "both"},
        ]
        with pytest.raises(ExchangeOrderClientError, match="partial_close_unknown_side"):
            await adapter.close_partial("BTC/USDT", Decimal("0.05"))

    async def test_zero_qty_returns_early(
        self, adapter: CcxtBinanceTestnetAdapter, mock_exchange: MagicMock,
    ) -> None:
        await adapter.close_partial("BTC/USDT", Decimal("0"))
        mock_exchange.fetch_positions.assert_not_called()
        mock_exchange.create_order.assert_not_called()

    async def test_no_open_position_returns_early(
        self, adapter: CcxtBinanceTestnetAdapter, mock_exchange: MagicMock,
    ) -> None:
        mock_exchange.fetch_positions.return_value = [
            {"symbol": "BTC/USDT", "contracts": 0, "side": "long"},
        ]
        await adapter.close_partial("BTC/USDT", Decimal("0.05"))
        mock_exchange.create_order.assert_not_called()

    async def test_fetch_positions_failure_raises(
        self, adapter: CcxtBinanceTestnetAdapter, mock_exchange: MagicMock,
    ) -> None:
        mock_exchange.fetch_positions.side_effect = TimeoutError("slow")
        with pytest.raises(
            ExchangeOrderClientError, match="partial_close_fetch_positions_failed",
        ):
            await adapter.close_partial("BTC/USDT", Decimal("0.05"))

    async def test_create_order_failure_raises(
        self, adapter: CcxtBinanceTestnetAdapter, mock_exchange: MagicMock,
    ) -> None:
        mock_exchange.fetch_positions.return_value = [
            {"symbol": "BTC/USDT", "contracts": 0.1, "side": "long"},
        ]
        mock_exchange.create_order.side_effect = RuntimeError("margin insufficient")
        with pytest.raises(ExchangeOrderClientError, match="partial_close_failed"):
            await adapter.close_partial("BTC/USDT", Decimal("0.05"))
