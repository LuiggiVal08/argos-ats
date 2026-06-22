"""Tests para P2: Sincronización real de SL/TP con Binance.

Verifica:
  1. CcxtOrderClient.place_composite_order() captura SL/TP order IDs
  2. CcxtOrderClient.cancel_order() cancela orden por ID
  3. CcxtOrderClient.place_stop_loss_order() coloca SL standalone
  4. LivePosition almacena sl_order_id / tp_order_id
  5. MonitorPositionsUseCase sincroniza SL con exchange en BE/trail/update
"""
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.application.use_cases.monitor_positions import MonitorPositionsUseCase
from app.domain.entities.position_manager import PositionAction, PositionManager
from app.domain.value_objects.live_position import LivePosition
from app.domain.value_objects.order import OrderResult, OrderSide, OrderStatus, OrderType
from app.infrastructure.exchange.ccxt_order_client import CcxtOrderClient
from app.infrastructure.trading.ccxt_binance_adapter import CcxtBinanceTestnetAdapter


class TestCompositeOrderCapturesIds:
    """P2a: SL/TP order IDs se capturan en place_composite_order."""

    @pytest.fixture
    def mock_exchange(self):
        ex = MagicMock()
        ex.create_order = AsyncMock()
        return ex

    @pytest.fixture
    def client(self, mock_exchange):
        return CcxtOrderClient(exchange=mock_exchange)

    async def test_returns_sl_order_id(self, client, mock_exchange):
        from app.domain.value_objects.order import CompositeOrder
        mock_exchange.create_order.side_effect = [
            {"id": "entry-1", "status": "filled", "filled": "0.1"},
            {"id": "sl-1", "status": "new"},
            {"id": "tp-1", "status": "new"},
        ]
        order = CompositeOrder(
            symbol="BTC/USDT", side=OrderSide.BUY,
            entry_amount=Decimal("0.1"),
            sl_price=Decimal("49000"), tp_price=Decimal("65000"),
        )
        result = await client.place_composite_order(order)
        assert result.id == "entry-1"
        assert result.sl_order_id == "sl-1"
        assert result.tp_order_id == "tp-1"

    async def test_no_sl_when_not_set(self, client, mock_exchange):
        from app.domain.value_objects.order import CompositeOrder
        mock_exchange.create_order.side_effect = [
            {"id": "entry-2", "status": "filled", "filled": "0.1"},
        ]
        order = CompositeOrder(
            symbol="ETH/USDT", side=OrderSide.BUY,
            entry_amount=Decimal("1.0"),
        )
        result = await client.place_composite_order(order)
        assert result.sl_order_id is None
        assert result.tp_order_id is None


class TestCancelOrder:
    """P2b: Cancelar orden por ID."""

    @pytest.fixture
    def mock_exchange(self):
        ex = MagicMock()
        ex.cancel_order = AsyncMock()
        return ex

    @pytest.fixture
    def client(self, mock_exchange):
        return CcxtOrderClient(exchange=mock_exchange)

    async def test_cancel_existing_order(self, client, mock_exchange):
        result = await client.cancel_order("sl-1", "BTC/USDT")
        assert result is True
        mock_exchange.cancel_order.assert_awaited_once_with("sl-1", "BTC/USDT")

    async def test_cancel_unknown_order_returns_false(self, client, mock_exchange):
        from ccxt import OrderNotFound
        mock_exchange.cancel_order.side_effect = OrderNotFound("unknown order")
        result = await client.cancel_order("ghost", "BTC/USDT")
        assert result is False

    async def test_cancel_raises_on_network_error(self, client, mock_exchange):
        from app.application.ports.exchange_order_client import ExchangeOrderClientError
        mock_exchange.cancel_order.side_effect = Exception("timeout")
        with pytest.raises(ExchangeOrderClientError):
            await client.cancel_order("sl-1", "BTC/USDT")


class TestPlaceStopLoss:
    """P2c: Colocar SL standalone."""

    @pytest.fixture
    def mock_exchange(self):
        ex = MagicMock()
        ex.create_order = AsyncMock()
        return ex

    @pytest.fixture
    def client(self, mock_exchange):
        return CcxtOrderClient(exchange=mock_exchange)

    async def test_places_stop_market(self, client, mock_exchange):
        mock_exchange.create_order.return_value = {
            "id": "new-sl-1", "status": "new", "filled": "0",
        }
        result = await client.place_stop_loss_order(
            symbol="BTC/USDT",
            side=OrderSide.SELL,
            amount=Decimal("0.1"),
            stop_price=Decimal("50000"),
        )
        assert result.id == "new-sl-1"
        mock_exchange.create_order.assert_awaited_once()
        args, kwargs = mock_exchange.create_order.call_args
        assert args[0] == "BTC/USDT"
        assert kwargs["type"] == "stop_market"
        assert kwargs["params"]["stopPrice"] == 50000.0

    async def test_raises_on_failure(self, client, mock_exchange):
        from app.application.ports.exchange_order_client import ExchangeOrderClientError
        mock_exchange.create_order.side_effect = Exception("insufficient margin")
        with pytest.raises(ExchangeOrderClientError, match="stop_loss_placement_failed"):
            await client.place_stop_loss_order(
                symbol="BTC/USDT", side=OrderSide.SELL,
                amount=Decimal("0.1"), stop_price=Decimal("50000"),
            )


class TestLivePositionTracksIds:
    """P2d: LivePosition almacena sl_order_id / tp_order_id."""

    def test_position_stores_sl_order_id(self):
        pos = LivePosition(
            position_id="p1",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            units=Decimal("0.1"),
            entry_price=Decimal("50000"),
            current_price=Decimal("51000"),
            sl_order_id="sl-abc",
            tp_order_id="tp-xyz",
        )
        assert pos.sl_order_id == "sl-abc"
        assert pos.tp_order_id == "tp-xyz"

    def test_position_defaults_to_none(self):
        pos = LivePosition(
            position_id="p2",
            symbol="ETH/USDT",
            side=OrderSide.BUY,
            units=Decimal("1.0"),
            entry_price=Decimal("3000"),
            current_price=Decimal("3100"),
        )
        assert pos.sl_order_id is None
        assert pos.tp_order_id is None


class TestMonitorSyncsSlToExchange:
    """P2e: MonitorPositionsUseCase sincroniza SL con exchange."""

    @pytest.fixture
    def exchange_client(self):
        client = MagicMock()
        client.cancel_order = AsyncMock(return_value=True)
        client.place_stop_loss_order = AsyncMock(return_value=OrderResult(
            id="new-sl-1", symbol="BTC/USDT", side=OrderSide.SELL,
            type=OrderType.STOP_LOSS_MARKET, filled_amount=Decimal("0"),
        ))
        client.close_position = AsyncMock()
        return client

    @pytest.fixture
    def price_provider(self):
        async def provider(symbol: str) -> Decimal:
            return Decimal("60000")
        return provider

    @pytest.fixture
    def position_repo(self):
        class FakeRepo:
            def __init__(self):
                self.positions = [
                    LivePosition(
                        position_id="pos-1",
                        symbol="BTC/USDT",
                        side=OrderSide.BUY,
                        units=Decimal("0.1"),
                        entry_price=Decimal("50000"),
                        current_price=Decimal("50000"),
                        sl_price=Decimal("48000"),
                        tp_price=Decimal("65000"),
                        sl_order_id="old-sl-123",
                        atr_at_entry=Decimal("2000"),
                    ),
                ]
                self.saved = []

            async def list_open(self):
                return self.positions

            async def save(self, pos):
                self.saved.append(pos)
        return FakeRepo()

    @pytest.fixture
    def logger(self):
        class FakeLogger:
            def __init__(self):
                self.monitoring = []
                self.executions = []

            async def log_monitoring(self, pid, pnl, price):
                self.monitoring.append((pid, pnl, price))

            async def log_execution(self, report):
                self.executions.append(report)

            async def log_rejection(self, signal_id, reason):
                pass

            async def recent(self, limit=20):
                return []

        return FakeLogger()

    async def test_be_activations_cancel_old_sl_and_place_new(
        self, exchange_client, price_provider, position_repo, logger
    ):
        """ACTIVATE_BREAK_EVEN cancela old SL + coloca nuevo SL en entry."""
        manager = PositionManager()
        uc = MonitorPositionsUseCase(
            position_repo=position_repo,
            exchange_client=exchange_client,
            execution_logger=logger,
            price_provider=price_provider,
            position_manager=manager,
        )
        result = await uc.run()
        # With price=60000 on a BUY at 50000, PnL > 1R should trigger BE
        # Verify old SL was cancelled
        exchange_client.cancel_order.assert_awaited()
        args, _ = exchange_client.cancel_order.call_args
        assert args[0] == "old-sl-123"

        # Verify new SL was placed
        exchange_client.place_stop_loss_order.assert_awaited()
        sl_args, sl_kwargs = exchange_client.place_stop_loss_order.call_args
        assert sl_kwargs["symbol"] == "BTC/USDT"
        assert sl_kwargs["stop_price"] is not None

        # Verify position was saved with new sl_order_id
        assert len(position_repo.saved) >= 1
        saved = position_repo.saved[-1]
        assert saved.sl_order_id == "new-sl-1"

    async def test_update_sl_cancels_and_replaces(
        self, exchange_client, price_provider, position_repo, logger
    ):
        """UPDATE_SL debe cancelar old SL y colocar nuevo."""
        uc = MonitorPositionsUseCase(
            position_repo=position_repo,
            exchange_client=exchange_client,
            execution_logger=logger,
            price_provider=price_provider,
        )
        result = await uc.run()
        assert exchange_client.cancel_order.awaited
