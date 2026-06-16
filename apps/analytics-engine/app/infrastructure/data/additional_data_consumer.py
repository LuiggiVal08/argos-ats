from __future__ import annotations

import asyncio
import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncIterator

import redis.asyncio as redis
import structlog

from ...domain.value_objects.agg_trade import AggTrade
from ...domain.value_objects.funding_rate import FundingRate
from ...domain.value_objects.open_interest import OpenInterest

log = structlog.get_logger()


@dataclass
class OrderFlowSnapshot:
    buy_volume: Decimal = Decimal("0")
    sell_volume: Decimal = Decimal("0")
    trade_count: int = 0
    window_start: int = 0

    @property
    def imbalance(self) -> Decimal:
        total = self.buy_volume + self.sell_volume
        if total == 0:
            return Decimal("0")
        return (self.buy_volume - self.sell_volume) / total


@dataclass
class AdditionalDataState:
    funding_rates: dict[str, FundingRate] = field(default_factory=dict)
    open_interest: dict[str, OpenInterest] = field(default_factory=dict)
    funding_rate_history: dict[str, list[FundingRate]] = field(
        default_factory=lambda: defaultdict(list)
    )
    oi_history: dict[str, list[OpenInterest]] = field(
        default_factory=lambda: defaultdict(list)
    )
    orderflow: dict[str, OrderFlowSnapshot] = field(default_factory=dict)


class AdditionalDataConsumer:
    def __init__(self, symbols: list[str], broker_url: str | None = None):
        self._symbols = symbols
        self._url = broker_url or os.environ.get(
            "ARGOS_BROKER_URL", "redis://localhost:6379"
        )
        self._state = AdditionalDataState()
        self._client: redis.Redis | None = None
        self._tasks: list[asyncio.Task] = []
        self._orderflow_lock = asyncio.Lock()

    @property
    def state(self) -> AdditionalDataState:
        return self._state

    async def start(self) -> None:
        self._client = redis.from_url(self._url)
        log.info("additional_data_consumer_started", symbols=self._symbols)
        for sym in self._symbols:
            clean = sym.replace("/", "").lower()
            self._tasks.append(
                asyncio.create_task(
                    self._consume_funding(clean), name=f"funding:{clean}"
                )
            )
            self._tasks.append(
                asyncio.create_task(
                    self._consume_oi(clean), name=f"oi:{clean}"
                )
            )
            self._tasks.append(
                asyncio.create_task(
                    self._consume_orderflow(clean), name=f"orderflow:{clean}"
                )
            )

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._client:
            await self._client.aclose()

    async def _consume_funding(self, symbol: str) -> None:
        stream = f"funding:{symbol}"
        last_id = "$"
        while True:
            try:
                res = await self._client.xread(  # type: ignore[union-attr]
                    {stream: last_id}, block=1000, count=100
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("funding_xread_error", symbol=symbol, error=str(e))
                await asyncio.sleep(1)
                continue
            if not res:
                continue
            for _s, entries in res:
                for entry_id, fields in entries:
                    last_id = (
                        entry_id.decode()
                        if isinstance(entry_id, (bytes, bytearray))
                        else entry_id
                    )
                    decoded = {
                        (k.decode() if isinstance(k, (bytes, bytearray)) else k): (
                            v.decode() if isinstance(v, (bytes, bytearray)) else v
                        )
                        for k, v in fields
                    }
                    payload = decoded.get("p")
                    if not payload:
                        continue
                    try:
                        data = json.loads(payload)
                        fr = FundingRate(
                            symbol=data["symbol"],
                            funding_rate=Decimal(str(data["fundingRate"])),
                            mark_price=Decimal(str(data["markPrice"])),
                            index_price=Decimal(str(data["indexPrice"])),
                            next_funding_time=data["nextFundingTime"],
                            ts=data["ts"],
                        )
                        self._state.funding_rates[fr.symbol] = fr
                        hist = self._state.funding_rate_history.get(fr.symbol, [])
                        hist.append(fr)
                        if len(hist) > 100:
                            hist.pop(0)
                        self._state.funding_rate_history[fr.symbol] = hist
                    except Exception as e:
                        log.warning("funding_parse_error", symbol=symbol, error=str(e))

    async def _consume_oi(self, symbol: str) -> None:
        stream = f"oi:{symbol}"
        last_id = "$"
        while True:
            try:
                res = await self._client.xread(  # type: ignore[union-attr]
                    {stream: last_id}, block=1000, count=100
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("oi_xread_error", symbol=symbol, error=str(e))
                await asyncio.sleep(1)
                continue
            if not res:
                continue
            for _s, entries in res:
                for entry_id, fields in entries:
                    last_id = (
                        entry_id.decode()
                        if isinstance(entry_id, (bytes, bytearray))
                        else entry_id
                    )
                    decoded = {
                        (k.decode() if isinstance(k, (bytes, bytearray)) else k): (
                            v.decode() if isinstance(v, (bytes, bytearray)) else v
                        )
                        for k, v in fields
                    }
                    payload = decoded.get("p")
                    if not payload:
                        continue
                    try:
                        data = json.loads(payload)
                        oi = OpenInterest(
                            symbol=data["symbol"],
                            open_interest=Decimal(str(data["openInterest"])),
                            ts=data["ts"],
                        )
                        self._state.open_interest[oi.symbol] = oi
                        hist = self._state.oi_history.get(oi.symbol, [])
                        hist.append(oi)
                        if len(hist) > 10:
                            hist.pop(0)
                        self._state.oi_history[oi.symbol] = hist
                    except Exception as e:
                        log.warning("oi_parse_error", symbol=symbol, error=str(e))

    async def _consume_orderflow(self, symbol: str) -> None:
        stream = f"orderflow:{symbol}"
        last_id = "$"
        while True:
            try:
                res = await self._client.xread(  # type: ignore[union-attr]
                    {stream: last_id}, block=1000, count=100
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("orderflow_xread_error", symbol=symbol, error=str(e))
                await asyncio.sleep(1)
                continue
            if not res:
                continue
            for _s, entries in res:
                for entry_id, fields in entries:
                    last_id = (
                        entry_id.decode()
                        if isinstance(entry_id, (bytes, bytearray))
                        else entry_id
                    )
                    decoded = {
                        (k.decode() if isinstance(k, (bytes, bytearray)) else k): (
                            v.decode() if isinstance(v, (bytes, bytearray)) else v
                        )
                        for k, v in fields
                    }
                    payload = decoded.get("p")
                    if not payload:
                        continue
                    try:
                        data = json.loads(payload)
                        trade = AggTrade(
                            symbol=data["symbol"],
                            trade_id=data["tradeId"],
                            price=Decimal(str(data["price"])),
                            quantity=Decimal(str(data["quantity"])),
                            is_buyer_maker=data["isBuyerMaker"],
                            ts=data["ts"],
                        )
                        await self._aggregate_orderflow(trade)
                    except Exception as e:
                        log.warning(
                            "orderflow_parse_error", symbol=symbol, error=str(e)
                        )

    async def _aggregate_orderflow(self, trade: AggTrade) -> None:
        async with self._orderflow_lock:
            minute = (trade.ts // 60_000) * 60_000
            snap = self._state.orderflow.get(trade.symbol)
            if snap is None or snap.window_start != minute:
                snap = OrderFlowSnapshot(window_start=minute)
                self._state.orderflow[trade.symbol] = snap
            if trade.is_buyer_maker:
                snap.sell_volume += trade.quantity
            else:
                snap.buy_volume += trade.quantity
            snap.trade_count += 1

    def get_funding_momentum(self, symbol: str, lookback: int = 5) -> Decimal | None:
        hist = self._state.funding_rate_history.get(symbol, [])
        if len(hist) < 2:
            return None
        recent = hist[-lookback:] if len(hist) >= lookback else hist
        return recent[-1].funding_rate - recent[0].funding_rate

    def get_oi_change_pct(self, symbol: str) -> Decimal | None:
        hist = self._state.oi_history.get(symbol, [])
        if len(hist) < 2:
            return None
        prev = hist[0].open_interest
        curr = hist[-1].open_interest
        if prev == 0:
            return None
        return (curr - prev) / prev * 100

    def get_orderflow_imbalance(self, symbol: str) -> Decimal | None:
        snap = self._state.orderflow.get(symbol)
        if snap is None or snap.trade_count == 0:
            return None
        return snap.imbalance

    def get_orderflow_snapshot(
        self, symbol: str
    ) -> OrderFlowSnapshot | None:
        return self._state.orderflow.get(symbol)

    def get_latest_funding_rate(self, symbol: str) -> FundingRate | None:
        return self._state.funding_rates.get(symbol)

    def get_latest_open_interest(self, symbol: str) -> OpenInterest | None:
        return self._state.open_interest.get(symbol)

    async def get_additional_features(self, symbol: str) -> dict[str, float]:
        features: dict[str, float] = {}
        fr = self.get_latest_funding_rate(symbol)
        if fr:
            features["funding_rate"] = float(fr.funding_rate)
        mom = self.get_funding_momentum(symbol)
        if mom is not None:
            features["funding_momentum"] = float(mom)
        oi_chg = self.get_oi_change_pct(symbol)
        if oi_chg is not None:
            features["oi_change_pct"] = float(oi_chg)
        imb = self.get_orderflow_imbalance(symbol)
        if imb is not None:
            features["orderflow_imbalance"] = float(imb)
        snap = self.get_orderflow_snapshot(symbol)
        if snap:
            features["orderflow_buy_vol"] = float(snap.buy_volume)
            features["orderflow_sell_vol"] = float(snap.sell_volume)
            features["orderflow_trade_count"] = snap.trade_count
        return features
