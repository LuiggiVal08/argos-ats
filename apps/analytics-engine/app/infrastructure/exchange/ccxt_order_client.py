"""CcxtOrderClient: implements ExchangeOrderClient via ccxt.async_support.

For each symbol in the configured universe:
  - cancel_all_orders: fetches open orders and cancels each one.
  - close_all_positions: fetches positions and submits a
    market order on the opposite side with reduce_only=True.
  - place_composite_order: places a market entry + stop loss +
    take profit bracket order. The SL leg has retry logic.
  - place_emergency_market: fire-and-forget liquidation.
"""
from __future__ import annotations

import asyncio
import random
from decimal import Decimal
from typing import Any

import ccxt.async_support as ccxt
import structlog

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


class CcxtOrderClient(ExchangeOrderClient):
    def __init__(
        self,
        exchange: ccxt.Exchange,
        symbols: tuple[str, ...] = ("BTC/USDT",),
        max_sl_retries: int = 3,
        sl_retry_base_ms: float = 100.0,
    ) -> None:
        self._exchange = exchange
        self._symbols = symbols
        self._max_sl_retries = max_sl_retries
        self._sl_retry_base_ms = sl_retry_base_ms
        self._price_cache: dict[str, tuple[Decimal, float]] = {}

    async def cancel_all_orders(self) -> int:
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
        closed: list[PositionSummary] = []
        for symbol in self._symbols:
            try:
                positions = await self._exchange.fetch_positions([symbol])
            except Exception as e:
                raise ExchangeOrderClientError(
                    f"fetch_positions_failed: {symbol}: {e}"
                ) from e
            for p in positions:
                amt = Decimal(str(p.get("contracts") or p.get("amount") or 0))
                if amt == 0:
                    continue
                side = "buy" if (p.get("side") or "").lower() == "short" else "sell"
                try:
                    await self._exchange.create_order(
                        symbol,
                        type="market",
                        side=side,
                        amount=abs(float(amt)),
                        params={"reduceOnly": True},
                    )
                except Exception as e:
                    raise ExchangeOrderClientError(
                        f"close_position_failed: {symbol} "
                        f"{p.get('side')} {amt}: {e}"
                    ) from e
                closed.append(
                    PositionSummary(
                        symbol=symbol,
                        side=str(p.get("side") or ""),
                        quantity=amt,
                        entry_price=Decimal(str(p.get("entryPrice") or 0)),
                    )
                )
        return closed

    async def close_position(self, symbol: str) -> PositionSummary:
        """Close the active position for a single symbol in futures.
        Fetches current position and issues a reduce-only market order
        on the opposite side."""
        try:
            positions = await self._exchange.fetch_positions([symbol])
        except Exception as e:
            raise ExchangeOrderClientError(
                f"fetch_positions_failed: {symbol}: {e}"
            ) from e
        for p in positions:
            amt = Decimal(str(p.get("contracts") or p.get("amount") or 0))
            if amt == 0:
                continue
            side = "buy" if (p.get("side") or "").lower() == "short" else "sell"
            try:
                await self._exchange.create_order(
                    symbol,
                    type="market",
                    side=side,
                    amount=abs(float(amt)),
                    params={"reduceOnly": True},
                )
            except Exception as e:
                raise ExchangeOrderClientError(
                    f"close_position_failed: {symbol} {p.get('side')} {amt}: {e}"
                ) from e
            return PositionSummary(
                symbol=symbol,
                side=str(p.get("side") or ""),
                quantity=amt,
                entry_price=Decimal(str(p.get("entryPrice") or 0)),
            )
        raise ExchangeOrderClientError(
            f"no_position_found: {symbol}"
        )

    def _round_amount(self, symbol: str, amount: Decimal) -> float:
        try:
            market = self._exchange.market(symbol)
            step = float(
                market.get("precision", {}).get("amount", 1e-8) or 1e-8
            )
            qty = float(amount)
            return round(qty / step) * step
        except Exception:
            return float(amount)

    async def place_composite_order(
        self, order: CompositeOrder
    ) -> OrderResult:
        # 1. Place market entry with amount quantized to exchange lot size.
        entry_amount = self._round_amount(order.symbol, order.entry_amount)
        try:
            raw = await self._exchange.create_order(
                order.symbol,
                type="market",
                side=order.side.value.lower(),
                amount=entry_amount,
            )
        except Exception as e:
            raise ExchangeOrderClientError(
                f"entry_order_failed: {order.symbol}: {e}"
            ) from e

        entry_result = _to_order_result(raw, order.side)
        # AUDIT: entry is live on exchange — SL not yet placed.
        # Window begins here. Next step attempts SL placement.
        log.info(
            "composite_entry_filled",
            entry_id=entry_result.id,
            symbol=order.symbol,
            side=order.side.value,
            filled=str(entry_result.filled_amount),
            avg_price=str(entry_result.avg_price),
            intended_sl=str(order.sl_price),
        )

        # 2. Place stop loss with retry.
        sl_order_id: str | None = None
        if order.sl_price is not None:
            sl_side = (
                OrderSide.SELL if order.side is OrderSide.BUY else OrderSide.BUY
            )
            sl_error: Exception | None = None
            sl_amount = self._round_amount(order.symbol, order.entry_amount)
            for attempt in range(self._max_sl_retries):
                try:
                    sl_raw = await self._exchange.create_order(
                        order.symbol,
                        type="stop_market",
                        side=sl_side.value.lower(),
                        amount=sl_amount,
                        params={
                            "stopPrice": float(order.sl_price),
                            "reduceOnly": True,
                        },
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
                # CRITICAL: entry is open with no SL — emergency market close.
                emergency_close: OrderResult | None = None
                close_side = (
                    OrderSide.SELL if order.side is OrderSide.BUY else OrderSide.BUY
                )
                close_amount = entry_result.filled_amount or order.entry_amount
                try:
                    emergency_close = await self.place_emergency_market(
                        order.symbol, close_side, close_amount
                    )
                except Exception as em:
                    log.critical(
                        "emergency_market_close_failed",
                        symbol=order.symbol,
                        entry_id=entry_result.id,
                        error=str(em),
                    )

                raise SlPlacementError(
                    entry_order=entry_result,
                    message=(
                        f"sl_placement_failed after {self._max_sl_retries} retries: "
                        f"{order.symbol}: {sl_error}. "
                        f"Entry order {entry_result.id} "
                        f"is open. Emergency market close "
                        f"{'succeeded' if emergency_close else 'FAILED'}."
                    ),
                ) from sl_error

        # 3. Place take profit (no retry; TP failure is non-critical).
        tp_order_id: str | None = None
        if order.tp_price is not None:
            tp_side = (
                OrderSide.SELL if order.side is OrderSide.BUY else OrderSide.BUY
            )
            tp_amount = self._round_amount(order.symbol, order.entry_amount)
            try:
                tp_raw = await self._exchange.create_order(
                    order.symbol,
                    type="take_profit_market",
                    side=tp_side.value.lower(),
                    amount=tp_amount,
                    params={
                        "stopPrice": float(order.tp_price),
                        "reduceOnly": True,
                    },
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

    async def get_price(self, symbol: str) -> Decimal:
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
        sl_error: Exception | None = None
        sl_amount = self._round_amount(symbol, amount)
        for attempt in range(self._max_sl_retries):
            try:
                raw = await self._exchange.create_order(
                    symbol,
                    type="stop_market",
                    side=side.value.lower(),
                    amount=sl_amount,
                    params={
                        "stopPrice": float(stop_price),
                        "reduceOnly": True,
                    },
                )
                return _to_order_result(raw, side)
            except Exception as e:
                sl_error = e
                if attempt < self._max_sl_retries - 1:
                    delay = (
                        self._sl_retry_base_ms
                        * (2 ** attempt)
                        + random.uniform(0, 20)
                    )
                    await asyncio.sleep(delay / 1000)
        raise ExchangeOrderClientError(
            f"stop_loss_placement_failed after {self._max_sl_retries} retries: "
            f"{symbol}: {sl_error}"
        ) from sl_error

    async def close_partial(self, symbol: str, quantity: Decimal) -> None:
        qty = self._round_amount(symbol, quantity)
        try:
            positions = await self._exchange.fetch_positions([symbol])
        except Exception as e:
            raise ExchangeOrderClientError(
                f"fetch_positions_for_partial_failed: {symbol}: {e}"
            ) from e
        pos_side = ""
        for p in positions:
            amt = Decimal(str(p.get("contracts") or p.get("amount") or 0))
            if amt != 0:
                pos_side = p.get("side", "").lower()
                break
        if not pos_side:
            log.warning("partial_close_no_position", symbol=symbol)
            return
        side = "buy" if pos_side == "short" else "sell"
        try:
            raw = await self._exchange.create_order(
                symbol,
                type="market",
                side=side,
                amount=qty,
                params={"reduceOnly": True},
            )
            log.info(
                "partial_close_executed",
                symbol=symbol,
                quantity=str(qty),
                order_id=raw.get("id"),
            )
        except Exception as e:
            raise ExchangeOrderClientError(
                f"partial_close_failed: {symbol}: {e}"
            ) from e

    async def place_emergency_market(
        self, symbol: str, side: OrderSide, amount: Decimal
    ) -> OrderResult:
        em_amount = self._round_amount(symbol, amount)
        try:
            raw = await self._exchange.create_order(
                symbol,
                type="market",
                side=side.value.lower(),
                amount=em_amount,
            )
        except Exception as e:
            raise ExchangeOrderClientError(
                f"emergency_order_failed: {symbol}: {e}"
            ) from e
        return _to_order_result(raw, side)


def _to_order_result(raw: dict[str, Any], side: OrderSide) -> OrderResult:
    raw_id = raw.get("id", "")
    order_type_raw = raw.get("type", "market") or "market"
    if "stop" in order_type_raw.lower():
        otype = OrderType.STOP_LOSS_MARKET
    elif "take_profit" in order_type_raw.lower() or "take profit" in order_type_raw.lower():
        otype = OrderType.TAKE_PROFIT_MARKET
    else:
        otype = OrderType.MARKET

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
    client_id = raw.get("clientOrderId") or ""

    return OrderResult(
        id=raw_id,
        symbol=raw.get("symbol", ""),
        side=side,
        type=otype,
        filled_amount=filled,
        avg_price=avg,
        status=status,
        client_order_id=client_id,
    )
