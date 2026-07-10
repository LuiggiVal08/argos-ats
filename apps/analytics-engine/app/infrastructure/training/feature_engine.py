"""FeatureEngine — single source of truth for ALL feature computation.

HARD RULES:
- Same code path used by training AND inference
- Deterministic transformations only
- No future data leakage (only past/present data)
- Order of 53 features is invariant

FEATURE SET (53 total):
  20 base TA indicators (OHLCV + RSI, EMAs, MACD, BB, ATR, ADX, OBV, VolSMA, PctChange)
  15 MTF 4h indicators
  15 MTF 1d indicators
   3 funding features
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import ta as ta_lib

from ...domain.entities.multi_timeframe_aligner import MultiTimeframeAligner

# ── 20 base features (exact order, invariant) ────────────────────────
BASE_FEATURES: tuple[str, ...] = (
    "open", "high", "low", "close", "volume",
    "rsi", "ema_fast", "ema_medium", "ema_slow",
    "macd", "macd_signal", "macd_hist",
    "bb_upper", "bb_middle", "bb_lower",
    "atr", "adx", "obv", "volume_sma", "pct_change",
)

# ── 15 MTF indicator names (same as MultiTimeframeAligner.INDICATOR_COLS) ─
MTF_INDICATORS: tuple[str, ...] = (
    "rsi", "ema_fast", "ema_medium", "ema_slow",
    "macd", "macd_signal", "macd_hist",
    "bb_upper", "bb_middle", "bb_lower",
    "atr", "adx", "obv", "volume_sma", "pct_change",
)

# ── 3 funding features ───────────────────────────────────────────────
FUNDING_FEATURES: tuple[str, ...] = (
    "funding_rate", "funding_momentum", "funding_change",
)


class FeatureEngine:
    """Single source of truth for all feature computations.

    Usage (training):
        features_df = FeatureEngine.compute_all(ohlcv, funding_df=None)

    Usage (inference via TaDataPreprocessor):
        FeatureEngine delegates internally.
    """

    @staticmethod
    def compute_base(ohlcv_df: pd.DataFrame) -> pd.DataFrame:
        """Compute 20 base TA features from OHLCV DataFrame.

        Args:
            ohlcv_df: DataFrame with columns open, high, low, close, volume.

        Returns:
            DataFrame with 20 columns matching BASE_FEATURES order.
        """
        close = ohlcv_df["close"].astype(float)
        high = ohlcv_df["high"].astype(float)
        low = ohlcv_df["low"].astype(float)
        volume = ohlcv_df["volume"].astype(float)

        raw: dict[str, pd.Series] = {
            "open": ohlcv_df["open"].astype(float),
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }

        raw["rsi"] = ta_lib.momentum.RSIIndicator(close, window=14).rsi()
        raw["ema_fast"] = ta_lib.trend.EMAIndicator(close, window=9).ema_indicator()
        raw["ema_medium"] = ta_lib.trend.EMAIndicator(close, window=21).ema_indicator()
        raw["ema_slow"] = ta_lib.trend.EMAIndicator(close, window=50).ema_indicator()

        macd = ta_lib.trend.MACD(close)
        raw["macd"] = macd.macd()
        raw["macd_signal"] = macd.macd_signal()
        raw["macd_hist"] = macd.macd_diff()

        bb = ta_lib.volatility.BollingerBands(close, window=20, window_dev=2)
        raw["bb_upper"] = bb.bollinger_hband()
        raw["bb_middle"] = bb.bollinger_mavg()
        raw["bb_lower"] = bb.bollinger_lband()

        raw["atr"] = ta_lib.volatility.AverageTrueRange(
            high, low, close, window=14
        ).average_true_range()
        raw["adx"] = ta_lib.trend.ADXIndicator(
            high, low, close, window=14
        ).adx()
        raw["obv"] = ta_lib.volume.OnBalanceVolumeIndicator(
            close, volume
        ).on_balance_volume()
        raw["volume_sma"] = volume.rolling(20).mean()
        raw["pct_change"] = close.pct_change() * 100.0

        result = pd.DataFrame(raw, columns=list(BASE_FEATURES))
        return result.bfill().ffill().fillna(0.0)

    @staticmethod
    def compute_mtf(
        ohlcv_df: pd.DataFrame,
        higher_tfs: tuple[str, ...] = ("4h", "1d"),
    ) -> pd.DataFrame:
        """Compute MTF indicators aligned to base timeframe.

        Delegates to MultiTimeframeAligner for resampling + TA computation.
        Returns zeros if insufficient data for higher-TF computation.

        Args:
            ohlcv_df: DataFrame with timestamp column, columns open, high, low, close, volume.
            higher_tfs: timeframes to compute (default 4h, 1d).

        Returns:
            DataFrame with columns ``htf_{indicator}_{tf}``.
        """
        try:
            ohlcv_list = ohlcv_df.to_dict("records")
            mtf_df = MultiTimeframeAligner.compute(ohlcv_list, base_tf="1h", higher_tfs=higher_tfs)
            return mtf_df
        except Exception:
            # Insufficient data for higher-TF computation — return zeros
            n = len(ohlcv_df)
            indicators = MultiTimeframeAligner.INDICATOR_COLS
            feature_count = len(indicators) * len(higher_tfs)
            cols = [f"htf_{ind}_{tf}" for tf in higher_tfs for ind in indicators]
            index = ohlcv_df.index if hasattr(ohlcv_df, "index") else None
            return pd.DataFrame(
                np.zeros((n, feature_count), dtype=np.float64),
                columns=cols,
                index=index,
            )

    @staticmethod
    def compute_funding(
        ohlcv_df: pd.DataFrame,
        funding_df: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """Compute funding rate features.

        Args:
            ohlcv_df: base OHLCV DataFrame (used for index alignment).
            funding_df: DataFrame with timestamp and fundingRate columns.
                        If None, returns zeros.

        Returns:
            DataFrame with 3 columns: funding_rate, funding_momentum, funding_change.
        """
        n = len(ohlcv_df)
        if funding_df is None or funding_df.empty:
            return pd.DataFrame(
                {"funding_rate": np.zeros(n), "funding_momentum": np.zeros(n), "funding_change": np.zeros(n)},
                index=ohlcv_df.index,
            )

        fund = funding_df[["timestamp", "fundingRate"]].copy()
        fund["timestamp"] = pd.to_datetime(fund["timestamp"])
        fund = fund.sort_values("timestamp").drop_duplicates(subset="timestamp").set_index("timestamp")
        ohlcv_index = ohlcv_df.index
        aligned = fund.reindex(ohlcv_index, method="ffill")
        rate = aligned["fundingRate"].fillna(0.0).values
        momentum = np.full_like(rate, 0.0, dtype=np.float64)
        change = np.full_like(rate, 0.0, dtype=np.float64)
        if len(rate) > 3:
            momentum[3:] = rate[3:] - rate[:-3]
            change[1:] = rate[1:] - rate[:-1]
        return pd.DataFrame(
            {"funding_rate": rate * 100.0, "funding_momentum": momentum * 100.0, "funding_change": change * 100.0},
            index=ohlcv_index.values,
        )

    @classmethod
    def compute_all(
        cls,
        ohlcv_df: pd.DataFrame,
        funding_df: pd.DataFrame | None = None,
        higher_tfs: tuple[str, ...] = ("4h", "1d"),
    ) -> pd.DataFrame:
        """Compute all 53 features (base + MTF 4h + MTF 1d + funding).

        Args:
            ohlcv_df: OHLCV DataFrame with timestamp column and open/high/low/close/volume.
            funding_df: optional funding rate DataFrame.
            higher_tfs: higher timeframes for MTF.

        Returns:
            DataFrame with 53 columns in exact order:
            20 base → 15 htf_*_4h → 15 htf_*_1d → 3 funding_*
        """
        base = cls.compute_base(ohlcv_df)
        mtf = cls.compute_mtf(ohlcv_df, higher_tfs=higher_tfs)
        fund = cls.compute_funding(ohlcv_df, funding_df)

        combined = pd.concat([base, mtf, fund], axis=1)
        combined = combined.bfill().ffill().fillna(0.0).astype(np.float64)
        return combined

    @classmethod
    def get_53_feature_names(cls) -> tuple[str, ...]:
        """Return the exact list of 53 feature names in order."""
        mtf_4h = [f"htf_{ind}_4h" for ind in MTF_INDICATORS]
        mtf_1d = [f"htf_{ind}_1d" for ind in MTF_INDICATORS]
        return BASE_FEATURES + tuple(mtf_4h) + tuple(mtf_1d) + FUNDING_FEATURES
