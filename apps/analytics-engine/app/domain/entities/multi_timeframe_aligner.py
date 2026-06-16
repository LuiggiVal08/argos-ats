from __future__ import annotations

import pandas as pd
import ta


class MultiTimeframeAligner:
    """Align higher-timeframe indicators to the 1h base timeframe.

    Takes 1h OHLCV, downsamples to 4h and 1d, computes a set of
    technical indicators on each, then forward-fills back to the
    1h alignment.

    The aligned higher-TF features can be concatenated to the base
    1h feature vector.
    """

    TF_MAP = {
        "4h": 4,
        "1d": 24,
    }

    @staticmethod
    def compute(
        ohlcv_1h: pd.DataFrame,
        include_timeframes: tuple[str, ...] = ("4h", "1d"),
    ) -> dict[str, pd.DataFrame]:
        """Compute higher-timeframe indicators aligned to 1h.

        Args:
            ohlcv_1h: DataFrame with columns
                [ts, open, high, low, close, volume], 1h frequency.
            include_timeframes: Which TFs to compute. Default both.

        Returns:
            Dict mapping TF label -> DataFrame with indicator columns,
            index-aligned to the input 1h DataFrame.
        """
        result: dict[str, pd.DataFrame] = {}
        ohlcv_1h = ohlcv_1h.sort_values("ts").reset_index(drop=True)

        for tf_label in include_timeframes:
            periods = MultiTimeframeAligner.TF_MAP[tf_label]
            df_higher = MultiTimeframeAligner._downsample(ohlcv_1h, periods)

            indicators = MultiTimeframeAligner._compute_indicators(df_higher)

            # Forward-fill from higher TF to 1h alignment
            aligned = MultiTimeframeAligner._align_to_1h(
                indicators, periods, len(ohlcv_1h)
            )
            result[tf_label] = aligned

        return result

    @staticmethod
    def _downsample(ohlcv_1h: pd.DataFrame, periods: int) -> pd.DataFrame:
        """Aggregate 1h candles to higher timeframe."""
        df = ohlcv_1h.copy()
        df["group"] = df.index // periods
        grouped = df.groupby("group")
        result = pd.DataFrame()
        result["open"] = grouped["open"].first()
        result["high"] = grouped["high"].max()
        result["low"] = grouped["low"].min()
        result["close"] = grouped["close"].last()
        result["volume"] = grouped["volume"].sum()
        return result

    @staticmethod
    def _compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """Compute TA indicators on a higher-TF DataFrame."""
        result = pd.DataFrame(index=df.index)

        result["rsi"] = ta.momentum.RSIIndicator(df["close"], window=14).rsi()
        result["ema_fast"] = ta.trend.EMAIndicator(df["close"], window=9).ema_indicator()
        result["ema_medium"] = ta.trend.EMAIndicator(
            df["close"], window=21
        ).ema_indicator()
        result["ema_slow"] = ta.trend.EMAIndicator(
            df["close"], window=50
        ).ema_indicator()

        macd = ta.trend.MACD(df["close"], window_slow=26, window_fast=12, window_sign=9)
        result["macd"] = macd.macd()
        result["macd_signal"] = macd.macd_signal()
        result["macd_hist"] = macd.macd_diff()

        bb = ta.volatility.BollingerBands(df["close"], window=20, window_dev=2)
        result["bb_upper"] = bb.bollinger_hband()
        result["bb_middle"] = bb.bollinger_mavg()
        result["bb_lower"] = bb.bollinger_lband()

        atr_indicator = ta.volatility.AverageTrueRange(
            df["high"], df["low"], df["close"], window=14
        )
        result["atr"] = atr_indicator.average_true_range()

        adx_indicator = ta.trend.ADXIndicator(
            df["high"], df["low"], df["close"], window=14
        )
        result["adx"] = adx_indicator.adx()

        result["obv"] = ta.volume.OnBalanceVolumeIndicator(
            df["close"], df["volume"]
        ).on_balance_volume()

        result["volume_sma"] = df["volume"].rolling(20).mean()
        result["pct_change"] = df["close"].pct_change() * 100

        return result

    @staticmethod
    def _align_to_1h(
        indicators: pd.DataFrame,
        periods: int,
        target_len: int,
    ) -> pd.DataFrame:
        """Forward-fill higher-TF indicators to 1h length."""
        repeated = indicators.reindex(
            range(len(indicators) * periods), method="ffill"
        )
        aligned = repeated.iloc[:target_len]
        # Prefix columns to avoid collision with base-TF features
        aligned.columns = [f"htf_{c}" for c in aligned.columns]
        return aligned
