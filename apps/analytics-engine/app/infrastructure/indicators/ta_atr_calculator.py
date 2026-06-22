"""TaAtrCalculator: implements AtrCalculator port using `ta`.

Reads OHLCV from a CCXT async exchange (or any callable returning
a DataFrame) and computes the Average True Range using
`ta.volatility.average_true_range`. Pure infrastructure: the only
domain it touches is the Atr value object returned.

Sad path: insufficient candles (< window) raises
AtrCalculatorError. Network errors from the candle source are
propagated (the use case catches them).
"""
from __future__ import annotations

from typing import Awaitable, Callable

import pandas as pd
from ta.volatility import average_true_range

from ...application.ports.atr_calculator import (
    AtrCalculator,
    AtrCalculatorError,
)
from ...domain.value_objects.atr import Atr, InvalidAtrError


OhlcvSource = Callable[[str, str, int], Awaitable[pd.DataFrame]]


class TaAtrCalculator(AtrCalculator):
    """Default ATR adapter. Decoupled from any specific candle
    source via the `source` callable, so unit tests can inject a
    static DataFrame and integration can inject a CCXT fetcher."""

    def __init__(self, source: OhlcvSource) -> None:
        self._source = source

    async def get_atr(
        self, symbol: str, timeframe: str = "1h", window: int = 14
    ) -> Atr:
        try:
            df = await self._source(symbol, timeframe, window)
        except Exception as e:
            raise AtrCalculatorError(f"candle_source_failed: {e}") from e

        if df is None or len(df) < window:
            raise AtrCalculatorError(
                f"insufficient_candles: need >= {window}, got "
                f"{0 if df is None else len(df)}"
            )

        try:
            series = average_true_range(
                high=df["high"], low=df["low"], close=df["close"], window=window
            )
        except Exception as e:
            raise AtrCalculatorError(f"ta_compute_failed: {e}") from e

        last = series.iloc[-1]
        if last is None or pd.isna(last):
            raise AtrCalculatorError(
                f"ta_returned_nan for {symbol} {timeframe} window={window}"
            )

        try:
            return Atr(float(last))
        except InvalidAtrError as e:
            raise AtrCalculatorError(f"invalid_atr: {e}") from e
