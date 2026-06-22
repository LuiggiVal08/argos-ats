"""CandlePersister — Redis-backed candle persistence with CCXT seed.

Persists completed candles to a Redis stream for cold-start survival.
On first boot (Redis empty), seeds from exchange historical OHLCV.
Three-tier architecture:

    exchange fetch_ohlcv (seed on cold start)
        ↓
    Redis Stream (AOF-persistent, survives restarts)
        ↓
    CandleBuffer (in-memory, maxlen=2000)

Candle format matches Candle.snapshot() JSON.
Stream key: ``candles:{symbol_sanitized}:{timeframe_s}s``
"""
from __future__ import annotations

import json
from typing import Any

import structlog

from .candle_builder import Candle

log = structlog.get_logger()

_SANITIZE = str.maketrans({"/": "_", "-": "_"})


class CandlePersister:
    """Persist completed candles to Redis; seed from exchange on cold boot.

    Args:
        redis_client: redis.asyncio.Redis instance.
        symbol: Trading pair, e.g. "BTC/USDT".
        timeframe_s: Candle duration in seconds (default 3600 for 1h).
        maxlen: Maximum candles kept in Redis stream (default 2000).
    """

    def __init__(
        self,
        redis_client: Any,
        symbol: str,
        timeframe_s: int = 3600,
        maxlen: int = 2000,
    ) -> None:
        self._redis = redis_client
        self._symbol = symbol
        self._sanitized = symbol.translate(_SANITIZE).lower()
        self._stream = f"candles:{self._sanitized}:{timeframe_s}s"
        self._maxlen = maxlen
        self._tf_s = timeframe_s

    async def save(self, candle: Candle) -> None:
        """XADD completed candle to Redis stream, trim to maxlen.

        Errors are logged but not raised (degradation safety):
        a failed persist does not block the tick→candle pipeline.
        """
        try:
            data = candle.snapshot()
            await self._redis.xadd(self._stream, {"p": json.dumps(data)})
            await self._redis.xtrim(
                self._stream, maxlen=self._maxlen, approximate=True
            )
        except Exception as exc:
            log.warning(
                "candle_persist_failed",
                stream=self._stream,
                open_ts=candle.open_ts,
                error=str(exc),
            )

    async def load_latest(self, count: int = 2000) -> list[Candle]:
        """Load last N candles from Redis stream.

        Returns candles in chronological order (oldest first).
        """
        entries = await self._redis.xrevrange(
            self._stream, "+", "-", count=count
        )
        candles: list[Candle] = []
        for entry_id, fields in entries:
            payload = fields.get(b"p", fields.get("p"))
            if not payload:
                continue
            try:
                raw = payload if isinstance(payload, str) else payload.decode()
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError, AttributeError):
                continue

            candles.append(
                Candle(
                    symbol=data.get("symbol", self._symbol),
                    open=float(data["open"]),
                    high=float(data["high"]),
                    low=float(data["low"]),
                    close=float(data["close"]),
                    volume=float(data["volume"]),
                    open_ts=int(data.get("open_ts", 0)),
                    close_ts=int(data.get("close_ts", 0)),
                    is_complete=bool(data.get("is_complete", True)),
                )
            )

        # xrevrange returns newest first — revert to chronological
        candles.reverse()
        return candles

    async def warmup(
        self,
        buffer: Any,
        exchange: Any | None = None,
        min_bars: int = 500,
    ) -> None:
        """Warm up the buffer from Redis; seed from exchange if needed.

        Strategy:
        1. Load existing candles from Redis stream.
        2. If count >= min_bars: done, just fill buffer.
        3. If count < min_bars and exchange is available:
           fetch historical 1h OHLCV, save each to Redis, fill buffer.
        4. If exchange is None: load what Redis has and warn.

        Args:
            buffer: CandleBuffer instance to fill.
            exchange: CCXT exchange instance (optional).
            min_bars: Minimum bars before considering warmup complete.
        """
        existing = await self.load_latest(self._maxlen)

        # Deduplicate by close_ts: keep last candle for each timestamp
        seen: set[int] = set()
        deduped: list[Candle] = []
        for c in existing:
            if c.close_ts not in seen:
                seen.add(c.close_ts)
                deduped.append(c)
        existing = deduped

        if exchange is not None and len(existing) < min_bars:
            # Seed from exchange — fetch more than needed to account for
            # possible overlap between Redis and exchange data
            fetch_limit = max(self._maxlen, min_bars * 2)
            try:
                raw = await exchange.fetch_ohlcv(
                    self._symbol, timeframe="1h", limit=fetch_limit
                )
            except Exception as exc:
                log.warning(
                    "candle_seed_fetch_failed",
                    symbol=self._symbol,
                    error=str(exc),
                )
                raw = []

            if raw:
                tf_ms = self._tf_s * 1000
                count = 0
                for row in raw:
                    ts, o, h, l, c, v = row
                    candle = Candle(
                        symbol=self._symbol,
                        open=float(o),
                        high=float(h),
                        low=float(l),
                        close=float(c),
                        volume=float(v),
                        open_ts=int(ts),
                        close_ts=int(ts) + tf_ms,
                        is_complete=True,
                    )
                    buffer.append(candle)
                    await self.save(candle)
                    count += 1

                log.info(
                    "candle_buffer_seeded_from_exchange",
                    count=count,
                    stream=self._stream,
                )
                return

        # Load from Redis (either we had enough or exchange fetch failed)
        for candle in existing:
            buffer.append(candle)

        log.info(
            "candle_buffer_warmed_from_redis",
            count=len(existing),
            stream=self._stream,
            target=min_bars,
        )
