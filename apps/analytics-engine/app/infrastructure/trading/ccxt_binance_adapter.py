"""CcxtBinanceTestnetAdapter: ExchangeOrderClient + PriceProvider para Binance Futures Demo.

Usa ccxt.async_support con enable_demo_trading(True).
El exchange ID se lee de EXCHANGE_ID (default: binanceusdm).
Las credenciales se leen de env vars BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_SECRET.
"""
from __future__ import annotations

import asyncio
import os

import structlog
import random
from decimal import Decimal
from typing import Any

import ccxt.async_support as ccxt

from ...application.ports.exchange_order_client import (
    ExchangeOrderClient,
    ExchangeOrderClientError,
    PositionSummary,
    SlPlacementError,
)
from ...domain.value_objects.order import (
    CompositeOrder,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
)

log = structlog.get_logger()


class CcxtBinanceTestnetAdapter:
    """Adaptador para Binance Futures Demo.

    Implementa ExchangeOrderClient para colocar, monitorear y cerrar
    posiciones en futuros. Tambien sirve como PriceProvider via get_price().

    Toda llamada que falle por red/timeout/auth levanta
    ExchangeOrderClientError.
    """

    SYMBOLS: tuple[str, ...] = ("BTC/USDT",)

    def __init__(
        self,
        exchange: ccxt.Exchange | None = None,
        symbols: tuple[str, ...] = SYMBOLS,
        max_sl_retries: int = 3,
        sl_retry_base_ms: float = 100.0,
    ) -> None:
        if exchange is None:
            ex_id = os.environ.get("EXCHANGE_ID", "binanceusdm")
            exchange_cls = getattr(ccxt, ex_id, ccxt.binanceusdm)
            api_key = os.environ["BINANCE_TESTNET_API_KEY"]
            api_secret = os.environ["BINANCE_TESTNET_SECRET"]
            exchange = exchange_cls({
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": True,
            })
            exchange.enable_demo_trading(True)
        self._exchange: ccxt.Exchange = exchange
        self._symbols = symbols
        self._max_sl_retries = max_sl_retries
        self._sl_retry_base_ms = sl_retry_base_ms
        self._price_cache: dict[str, tuple[Decimal, float]] = {}

    # ── PriceProvider (para MonitorPositionsUseCase) ───────────────

    async def get_price(self, symbol: str) -> Decimal:
        """Retorna el ultimo precio via fetch_ticker.

        Cachea el resultado por 1s para evitar rate limiting.
        """
        now = asyncio.get_event_loop().time()
        cached = self._price_cache.get(symbol)
        if cached is not None and (now - cached[1]) < 1.0:
            return cached[0]
        try:
            ticker = await self._exchange.fetch_ticker(symbol)
        except Exception as e:
            raise ExchangeOrderClientError(
                f"fetch_ticker_failed: {symbol}: {e}"
            ) from e
        last = ticker.get("last") or ticker.get("close") or 0.0
        price = Decimal(str(last))
        if price <= 0:
            raise ExchangeOrderClientError(
                f"invalid_ticker_price: {symbol}: {last}"
            )
        self._price_cache[symbol] = (price, now)
        return price

    # ── Close single position (Spot: sell the base balance) ───────

    async def close_position(self, symbol: str) -> PositionSummary:
        """Cierra una posicion Spot vendiendo todo el balance del base.

        En Spot no hay 'posiciones' como en futuros; el balance
        disponible del base se vende a mercado.
        """
        base = symbol.split("/")[0]
        try:
            balance = await self._exchange.fetch_balance()
        except Exception as e:
            raise ExchangeOrderClientError(
                f"fetch_balance_failed: {symbol}: {e}"
            ) from e
        free = Decimal(str(balance.get("free", {}).get(base, 0)))
        if free <= 0:
            raise ExchangeOrderClientError(
                f"no_balance_to_close: {symbol} free={free}"
            )
        try:
            raw = await self._exchange.create_order(
                symbol,
                type="market",
                side="sell",
                amount=abs(float(free)),
            )
        except Exception as e:
            raise ExchangeOrderClientError(
                f"close_position_failed: {symbol}: {e}"
            ) from e
        price = Decimal(str(raw.get("price") or raw.get("average") or 0))
        return PositionSummary(
            symbol=symbol,
            side="long",
            quantity=free,
            entry_price=price,
        )

    # ── ExchangeOrderClient ────────────────────────────────────────

    async def cancel_all_orders(self) -> int:
        """Cancela todas las ordenes abiertas en los symbols configurados."""
        cancelled = 0
        for symbol in self._symbols:
            try:
                orders = await self._exchange.fetch_open_orders(symbol)
            except Exception as e:
                raise ExchangeOrderClientError(
                    f"fetch_open_orders_failed: {symbol}: {e}"
                ) from e
            for o in orders:
                try:
                    await self._exchange.cancel_order(o["id"], symbol)
                    cancelled += 1
                except Exception as e:
                    raise ExchangeOrderClientError(
                        f"cancel_order_failed: {symbol} {o.get('id')}: {e}"
                    ) from e
        return cancelled

    async def close_all_positions(self) -> list[PositionSummary]:
        """Cierra todas las posiciones Spot vendiendo cada base."""
        closed: list[PositionSummary] = []
        for symbol in self._symbols:
            try:
                summary = await self.close_position(symbol)
                closed.append(summary)
            except ExchangeOrderClientError as e:
                log.warning("close_position_failed", symbol=symbol, error=str(e))
        return closed

    async def place_composite_order(
        self, order: CompositeOrder
    ) -> OrderResult:
        """Coloca orden bracket en Spot:
        1. Market entry
        2. STOP_LOSS_LIMIT (con retry exponencial, max 3 en ~500ms)
        3. TAKE_PROFIT_LIMIT (best-effort, sin retry)
        """
        side_str = order.side.value.lower()
        entry_amount = abs(float(order.entry_amount))

        # 1. Market entry
        try:
            raw = await self._exchange.create_order(
                order.symbol,
                type="market",
                side=side_str,
                amount=entry_amount,
            )
        except Exception as e:
            raise ExchangeOrderClientError(
                f"entry_order_failed: {order.symbol}: {e}"
            ) from e

        entry_result = _to_order_result(raw, order.side)

        sl_order_id: str | None = None
        tp_order_id: str | None = None

        # 2. Stop loss (STOP_LOSS_LIMIT) con retry exponencial
        if order.sl_price is not None and order.sl_price > 0:
            sl_side = "sell" if order.side is OrderSide.BUY else "buy"
            sl_error: Exception | None = None
            sl_price_f = float(order.sl_price)
            for attempt in range(self._max_sl_retries):
                try:
                    sl_raw = await self._exchange.create_order(
                        order.symbol,
                        type="STOP_LOSS_LIMIT",
                        side=sl_side,
                        amount=entry_amount,
                        price=sl_price_f * 0.99,
                        params={"stopPrice": sl_price_f},
                    )
                    sl_order_id = str(sl_raw.get("id", ""))
                    sl_error = None
                    break
                except Exception as e:
                    sl_error = e
                    if attempt < self._max_sl_retries - 1:
                        delay = (
                            self._sl_retry_base_ms
                            * (2 ** attempt)
                            + random.uniform(0, 20)
                        )
                        await asyncio.sleep(delay / 1000)
            if sl_error is not None:
                raise SlPlacementError(
                    entry_order=entry_result,
                    message=(
                        f"sl_placement_failed after {self._max_sl_retries} retries: "
                        f"{order.symbol}: {sl_error}. Entry {entry_result.id} is open."
                    ),
                ) from sl_error

        # 3. Take profit (TAKE_PROFIT_LIMIT, best-effort)
        if order.tp_price is not None and order.tp_price > 0:
            tp_side = "sell" if order.side is OrderSide.BUY else "buy"
            try:
                tp_raw = await self._exchange.create_order(
                    order.symbol,
                    type="TAKE_PROFIT_LIMIT",
                    side=tp_side,
                    amount=entry_amount,
                    price=float(order.tp_price),
                    params={"stopPrice": float(order.tp_price)},
                )
                tp_order_id = str(tp_raw.get("id", ""))
            except Exception as e:
                log.warning("take_profit_placement_failed", symbol=order.symbol, error=str(e))

        return OrderResult(
            id=entry_result.id,
            symbol=entry_result.symbol,
            side=entry_result.side,
            type=entry_result.type,
            filled_amount=entry_result.filled_amount,
            avg_price=entry_result.avg_price,
            status=entry_result.status,
            client_order_id=entry_result.client_order_id,
            sl_order_id=sl_order_id,
            tp_order_id=tp_order_id,
        )

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        try:
            await self._exchange.cancel_order(order_id, symbol)
            return True
        except Exception as e:
            err_str = str(e).lower()
            if "unknown order" in err_str or "does not exist" in err_str:
                return False
            raise ExchangeOrderClientError(
                f"cancel_order_failed: {symbol} {order_id}: {e}"
            ) from e

    async def place_stop_loss_order(
        self,
        symbol: str,
        side: OrderSide,
        amount: Decimal,
        stop_price: Decimal,
    ) -> OrderResult:
        try:
            raw = await self._exchange.create_order(
                symbol,
                type="STOP_LOSS_LIMIT",
                side=side.value.lower(),
                amount=abs(float(amount)),
                price=float(stop_price) * 0.99,
                params={"stopPrice": float(stop_price)},
            )
        except Exception as e:
            raise ExchangeOrderClientError(
                f"stop_loss_placement_failed: {symbol}: {e}"
            ) from e
        return _to_order_result(raw, side)

    async def place_emergency_market(
        self, symbol: str, side: OrderSide, amount: Decimal
    ) -> OrderResult:
        """Market order de emergencia para liquidar posicion."""
        try:
            raw = await self._exchange.create_order(
                symbol,
                type="market",
                side=side.value.lower(),
                amount=abs(float(amount)),
            )
        except Exception as e:
            raise ExchangeOrderClientError(
                f"emergency_order_failed: {symbol}: {e}"
            ) from e
        return _to_order_result(raw, side)


def _to_order_result(raw: dict[str, Any], side: OrderSide) -> OrderResult:
    """Convierte respuesta cruda de CCXT a OrderResult del dominio."""
    raw_id = raw.get("id") or ""
    status_raw = (raw.get("status") or "new").lower()
    status_map: dict[str, OrderStatus] = {
        "new": OrderStatus.NEW,
        "open": OrderStatus.NEW,
        "partially_filled": OrderStatus.PARTIALLY_FILLED,
        "filled": OrderStatus.FILLED,
        "canceled": OrderStatus.CANCELLED,
        "cancelled": OrderStatus.CANCELLED,
        "rejected": OrderStatus.REJECTED,
        "expired": OrderStatus.EXPIRED,
    }
    status = status_map.get(status_raw, OrderStatus.NEW)
    filled = Decimal(str(raw.get("filled") or 0))
    avg_price = raw.get("price") or raw.get("average") or None
    avg = Decimal(str(avg_price)) if avg_price else None
    return OrderResult(
        id=raw_id,
        symbol=raw.get("symbol", ""),
        side=side,
        type=OrderType.MARKET,
        filled_amount=filled,
        avg_price=avg,
        status=status,
        client_order_id=raw.get("clientOrderId") or "",
    )
