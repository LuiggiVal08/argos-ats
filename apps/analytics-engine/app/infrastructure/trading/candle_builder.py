"""CandleBuilder — stateful candle construction from ticks, plus CandleBuffer.

Pure domain: no I/O, no external dependencies. Receives ticks,
maintains current candle state, emits completed candles.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

import structlog


@dataclass
class Candle:
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    open_ts: int
    close_ts: int
    is_complete: bool = False

    def snapshot(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "open_ts": self.open_ts,
            "close_ts": self.close_ts,
            "is_complete": self.is_complete,
        }


@dataclass
class CandleUpdate:
    completed: Candle | None = None
    current: Candle | None = None
    is_new_window: bool = False


log = structlog.get_logger()


class CandleBuilder:
    """Stateful candle builder from tick stream.

    Args:
        symbol: Trading pair, e.g. BTC/USDT.
        timeframe_seconds: Candle duration in seconds (default 60 for 1m).
    """

    def __init__(self, symbol: str, timeframe_seconds: int = 60) -> None:
        self._symbol = symbol
        self._tf_ms = timeframe_seconds * 1000
        self._current: Candle | None = None
        self._last_tick_ts: int = 0
        self._desync_count: int = 0
        self._gap_count: int = 0

    def update(self, ts_ms: int, price: float, volume: float) -> CandleUpdate:
        """Process one tick and return CandleUpdate.

        Includes desync protection:
        - ticks with ts < close_ts of last completed candle → discarded + log.
        - gap > 2× timeframe → logged as candle_gap_detected.

        Args:
            ts_ms: Tick timestamp in milliseconds.
            price: Tick price as float.
            volume: Tick volume as float.

        Returns:
            CandleUpdate with completed candle if a window just closed.
        """

        if self._current is not None and ts_ms < self._current.close_ts - self._tf_ms:
            self._desync_count += 1
            log.warning(
                "tick_out_of_order",
                current_ts=ts_ms,
                last_close_ts=self._current.close_ts,
                desync_count=self._desync_count,
            )
            if self._current is not None and ts_ms >= self._current.open_ts:
                pass
            else:
                return CandleUpdate(current=self._current or None)

        if self._last_tick_ts > 0:
            elapsed = ts_ms - self._last_tick_ts
            if elapsed > self._tf_ms * 2:
                self._gap_count += 1
                expected_window = (self._last_tick_ts // self._tf_ms) * self._tf_ms
                actual_window = (ts_ms // self._tf_ms) * self._tf_ms
                log.warning(
                    "candle_gap_detected",
                    expected_window_ms=expected_window,
                    actual_window_ms=actual_window,
                    gap_ms=elapsed,
                    gap_count=self._gap_count,
                )

        self._last_tick_ts = ts_ms
        window_start = (ts_ms // self._tf_ms) * self._tf_ms
        window_end = window_start + self._tf_ms

        if self._current is None or window_start >= self._current.close_ts:
            prev = self._current
            self._current = Candle(
                symbol=self._symbol,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=volume,
                open_ts=window_start,
                close_ts=window_end,
            )
            if prev is not None:
                closed = Candle(
                    symbol=prev.symbol,
                    open=prev.open,
                    high=prev.high,
                    low=prev.low,
                    close=prev.close,
                    volume=prev.volume,
                    open_ts=prev.open_ts,
                    close_ts=prev.close_ts,
                    is_complete=True,
                )
                return CandleUpdate(
                    completed=closed,
                    current=self._current,
                    is_new_window=True,
                )
            return CandleUpdate(
                current=self._current,
                is_new_window=True,
            )

        self._current = Candle(
            symbol=self._symbol,
            open=self._current.open,
            high=max(self._current.high, price),
            low=min(self._current.low, price),
            close=price,
            volume=self._current.volume + volume,
            open_ts=self._current.open_ts,
            close_ts=self._current.close_ts,
        )

        return CandleUpdate(current=self._current)

    @property
    def current(self) -> Candle | None:
        return self._current

    @property
    def desync_count(self) -> int:
        return self._desync_count

    @property
    def gap_count(self) -> int:
        return self._gap_count

    def snapshot(self) -> dict[str, Any]:
        return {
            "symbol": self._symbol,
            "timeframe_ms": self._tf_ms,
            "current": self._current.snapshot() if self._current else None,
        }


class CandleBuffer:
    """Bounded rolling buffer of completed candles.

    Args:
        maxlen: Maximum number of candles to keep (default 200).
    """

    def __init__(self, maxlen: int = 200) -> None:
        self._candles: deque[Candle] = deque(maxlen=maxlen)
        self._maxlen = maxlen

    def append(self, candle: Candle) -> None:
        if not candle.is_complete:
            return
        self._candles.append(candle)

    def get_all(self) -> list[Candle]:
        return list(self._candles)

    def get_last(self, n: int) -> list[Candle]:
        return list(self._candles)[-n:]

    def to_ohlcv_dicts(self) -> list[dict[str, float | int]]:
        return [
            {
                "timestamp": c.close_ts,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
            }
            for c in self._candles
        ]

    def aggregate_to_timeframe(self, target_tf_ms: int) -> list[dict[str, float | int]]:
        """Aggregate 1m candles into a larger timeframe (e.g. 5m).

        Args:
            target_tf_ms: Target timeframe in milliseconds (e.g. 300000 for 5m).

        Returns:
            List of OHLCV dicts at the aggregated timeframe.
        """
        if not self._candles:
            return []

        grouped: dict[int, list[Candle]] = {}
        for c in self._candles:
            bucket = (c.close_ts // target_tf_ms) * target_tf_ms
            if bucket not in grouped:
                grouped[bucket] = []
            grouped[bucket].append(c)

        result = []
        for bucket_start in sorted(grouped):
            candles = grouped[bucket_start]
            result.append({
                "timestamp": bucket_start + target_tf_ms,
                "open": candles[0].open,
                "high": max(c.high for c in candles),
                "low": min(c.low for c in candles),
                "close": candles[-1].close,
                "volume": sum(c.volume for c in candles),
            })
        return result

    def snapshot(self) -> dict[str, Any]:
        return {
            "count": len(self._candles),
            "maxlen": self._maxlen,
            "oldest_ts": self._candles[0].close_ts if self._candles else None,
            "newest_ts": self._candles[-1].close_ts if self._candles else None,
        }

    def __len__(self) -> int:
        return len(self._candles)

    def __bool__(self) -> bool:
        return bool(self._candles)
