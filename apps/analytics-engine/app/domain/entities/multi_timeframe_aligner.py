"""MultiTimeframeAligner — domain entity for higher-TF indicator alignment.

Aligns higher-timeframe TA indicators to a base timeframe by
resampling OHLCV, computing indicators, and forward-filling.

This entity is placed in domain because it is a pure business
transformation: given the same OHLCV data, it must always produce
the same aligned indicators regardless of infrastructure.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import ta


class MultiTimeframeAlignmentError(RuntimeError):
    """Raised when alignment fails (e.g. insufficient data)."""


class MultiTimeframeAligner:
    """Compute higher-TF indicators aligned to base-TF candles.

    Usage:
        aligner = MultiTimeframeAligner()
        mtf_df = aligner.compute(ohlcv, base_tf="1h", higher_tfs=("4h", "1d"))
        # mtf_df has same index length as input, columns prefixed htf_{indicator}_{tf}
    """

    # The 15 TA indicators computed for each higher timeframe
    # (subset of the base 20 features, excluding raw OHLCV)
    INDICATOR_COLS: tuple[str, ...] = (
        "rsi", "ema_fast", "ema_medium", "ema_slow",
        "macd", "macd_signal", "macd_hist",
        "bb_upper", "bb_middle", "bb_lower",
        "atr", "adx", "obv", "volume_sma", "pct_change",
    )

    @classmethod
    def compute(
        cls,
        ohlcv: list[dict],
        base_tf: str = "1h",
        higher_tfs: tuple[str, ...] = ("4h", "1d"),
    ) -> pd.DataFrame:
        """Compute higher-TF indicators aligned to base-TF index.

        Args:
            ohlcv: list of dicts with keys timestamp, open, high, low, close, volume.
            base_tf: base timeframe string (pandas offset alias).
            higher_tfs: higher timeframes to compute (pandas offset aliases).

        Returns:
            DataFrame with same index length as input OHLCV, columns named
            ``htf_{indicator}_{tf}``. Forward-filled so every base candle
            has a value (no NaN for lookahead candles).

        Raises:
            MultiTimeframeAlignmentError: if OHLCV is empty or cannot be parsed.
        """
        if not ohlcv:
            raise MultiTimeframeAlignmentError("empty OHLCV input")

        try:
            df = pd.DataFrame(ohlcv)
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df = df.sort_values("timestamp").set_index("timestamp")
            df = df[["open", "high", "low", "close", "volume"]].astype(float)
            # Remove duplicate timestamps (cartesian product guard)
            df = df.loc[~df.index.duplicated(keep="last")]
        except Exception as e:
            raise MultiTimeframeAlignmentError(f"cannot parse OHLCV: {e}") from e

        result = pd.DataFrame(index=df.index)

        for tf in higher_tfs:
            resampled = df.resample(tf).agg({
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }).dropna()
            indicators = cls._compute_indicators(resampled)
            indicators = indicators.reindex(df.index, method="ffill")
            indicators.columns = [
                f"htf_{col}_{tf}" for col in indicators.columns
            ]
            result = result.join(indicators, how="left")

        # Fill any remaining NaN (first few rows before first higher-TF candle)
        result = result.bfill().ffill().fillna(0.0)

        return result

    @classmethod
    def expected_feature_names(
        cls,
        prefix: str = "htf",
        base_features: tuple[str, ...] | None = None,
        higher_tfs: tuple[str, ...] = ("4h", "1d"),
    ) -> tuple[str, ...]:
        """Return the expected feature names for a given MTF configuration.

        Useful for constructing ModelConfig.features when MTF is enabled.

        Args:
            prefix: column prefix (default "htf").
            base_features: subset of indicators to include (default all 15).
            higher_tfs: timeframes to include.

        Returns:
            Tuple of feature names like ``htf_rsi_4h``, ``htf_ema_fast_4h``, ...
        """
        indicators = base_features or cls.INDICATOR_COLS
        names: list[str] = []
        for tf in higher_tfs:
            for ind in indicators:
                names.append(f"{prefix}_{ind}_{tf}")
        return tuple(names)

    @classmethod
    def _compute_indicators(cls, df: pd.DataFrame) -> pd.DataFrame:
        """Compute 15 TA indicators on a resampled OHLCV DataFrame.

        Args:
            df: OHLCV DataFrame with columns open, high, low, close, volume.

        Returns:
            DataFrame with same index, columns matching INDICATOR_COLS.
        """
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        out = pd.DataFrame(index=df.index)

        out["rsi"] = ta.momentum.RSIIndicator(close, window=14).rsi()
        out["ema_fast"] = ta.trend.EMAIndicator(close, window=9).ema_indicator()
        out["ema_medium"] = ta.trend.EMAIndicator(close, window=21).ema_indicator()
        out["ema_slow"] = ta.trend.EMAIndicator(close, window=50).ema_indicator()

        macd = ta.trend.MACD(close)
        out["macd"] = macd.macd()
        out["macd_signal"] = macd.macd_signal()
        out["macd_hist"] = macd.macd_diff()

        bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
        out["bb_upper"] = bb.bollinger_hband()
        out["bb_middle"] = bb.bollinger_mavg()
        out["bb_lower"] = bb.bollinger_lband()

        out["atr"] = ta.volatility.AverageTrueRange(
            high, low, close, window=min(14, len(df) - 1)
        ).average_true_range()
        out["adx"] = ta.trend.ADXIndicator(
            high, low, close, window=min(14, len(df) - 1)
        ).adx()
        out["obv"] = ta.volume.OnBalanceVolumeIndicator(close, volume).on_balance_volume()
        out["volume_sma"] = volume.rolling(min(20, len(df))).mean()
        out["pct_change"] = close.pct_change() * 100.0

        out = out.bfill().ffill().fillna(0.0)
        return out
