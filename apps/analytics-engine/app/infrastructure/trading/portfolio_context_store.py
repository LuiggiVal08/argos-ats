from __future__ import annotations

import json
import time
from typing import Any

from redis.asyncio import Redis

from ...application.ports.portfolio_context_store import PortfolioContextStore
from ...domain.value_objects.portfolio_context_snapshot import (
    PortfolioContextSnapshot,
    SignalCluster,
    TradeRecord,
)

_CLUSTER_WINDOW_S = 21_600


def _key(symbol: str, suffix: str) -> str:
    return f"portfolio_context:{symbol}:{suffix}"


def _encode_trade(r: TradeRecord) -> str:
    return json.dumps({
        "direction": r.direction,
        "outcome": r.outcome,
        "regime": r.regime,
        "timestamp": r.timestamp,
        "confidence": r.confidence,
    })


def _decode_trade(raw: str | None) -> TradeRecord | None:
    if not raw:
        return None
    try:
        d = json.loads(raw)
        return TradeRecord(
            direction=d["direction"],
            outcome=d.get("outcome"),
            regime=d["regime"],
            timestamp=d["timestamp"],
            confidence=d["confidence"],
        )
    except (json.JSONDecodeError, KeyError):
        return None


def _encode_cluster(c: SignalCluster) -> str:
    return json.dumps({
        "cluster_id": c.cluster_id,
        "direction": c.direction,
        "first_timestamp": c.first_timestamp,
        "last_timestamp": c.last_timestamp,
        "signal_count": c.signal_count,
    })


def _decode_cluster(raw: str | None) -> SignalCluster | None:
    if not raw:
        return None
    try:
        d = json.loads(raw)
        return SignalCluster(
            cluster_id=d["cluster_id"],
            direction=d["direction"],
            first_timestamp=d["first_timestamp"],
            last_timestamp=d["last_timestamp"],
            signal_count=d["signal_count"],
        )
    except (json.JSONDecodeError, KeyError):
        return None


class RedisPortfolioContextStore(PortfolioContextStore):
    def __init__(self, redis: Redis) -> None:
        self._r = redis

    async def load_context(self, symbol: str) -> PortfolioContextSnapshot:
        pipe = self._r.pipeline()
        pipe.get(_key(symbol, "last_trade"))
        pipe.get(_key(symbol, "cluster"))
        pipe.zcount(_key(symbol, "signals_6h"), time.time() - _CLUSTER_WINDOW_S, time.time())
        pipe.zrevrange(_key(symbol, "signals_6h"), 0, 0, withscores=True)
        results = await pipe.execute()

        last_trade_raw, cluster_raw, count_6h, last_same = results
        last_trade = _decode_trade(last_trade_raw)
        cluster = _decode_cluster(cluster_raw)
        last_ts = float(last_same[0][1]) if last_same else 0.0 if isinstance(last_same, list) and last_same else 0.0

        return PortfolioContextSnapshot(
            symbol=symbol,
            last_trade=last_trade,
            active_cluster=cluster,
            same_direction_count_6h=count_6h,
            last_same_direction_ts=last_ts,
        )

    async def record_signal(
        self, symbol: str, direction: str, timestamp: float
    ) -> None:
        pipe = self._r.pipeline()
        pipe.zadd(_key(symbol, "signals_6h"), {f"{direction}_{timestamp}": timestamp})
        pipe.zremrangebyscore(_key(symbol, "signals_6h"), 0, timestamp - _CLUSTER_WINDOW_S)
        pipe.expire(_key(symbol, "signals_6h"), _CLUSTER_WINDOW_S * 2)
        await pipe.execute()

    async def save_trade_record(
        self, symbol: str, record: TradeRecord
    ) -> None:
        pipe = self._r.pipeline()
        pipe.set(_key(symbol, "last_trade"), _encode_trade(record))
        pipe.expire(_key(symbol, "last_trade"), 86_400)
        await pipe.execute()

    async def clear_cluster(self, symbol: str) -> None:
        await self._r.delete(_key(symbol, "cluster"))
