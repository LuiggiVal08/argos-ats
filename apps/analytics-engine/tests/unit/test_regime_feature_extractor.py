"""Tests for RegimeFeatureExtractor (H23 bridge)."""
from __future__ import annotations

import numpy as np
import pytest

from app.application.ports.regime_detector import RegimeDetectionError
from app.infrastructure.analysis.regime_feature_extractor import (
    RegimeFeatureExtractor,
)

# Standard 20-feature TaDataPreprocessor layout
# open, high, low, close, volume, rsi, ema_fast, ema_medium, ema_slow,
# macd, macd_signal, macd_hist, bb_upper, bb_middle, bb_lower,
# atr, adx, obv, volume_sma, pct_change


def _make_ohlcv(n: int) -> list[dict]:
    """Generate N OHLCV candles."""
    data = []
    price = 50000.0
    for i in range(n):
        price *= 1.001
        data.append({
            "timestamp": i * 3600 * 1000,
            "open": round(price, 2),
            "high": round(price * 1.01, 2),
            "low": round(price * 0.99, 2),
            "close": round(price, 2),
            "volume": round(500.0 + i * 0.1, 4),
        })
    return data


def _make_features(n: int) -> np.ndarray:
    """Generate mock feature array with 20 columns."""
    # Populate with sensible defaults for BB/ATR/EMA/ADX columns
    np.random.seed(42)
    feats = np.random.randn(n, 20)

    # bb_middle (13) = SMA(close) > 0, bb_upper > bb_middle > bb_lower
    feats[:, 13] = np.abs(feats[:, 13]) + 100.0  # bb_middle positive (like a price)
    feats[:, 12] = feats[:, 13] + 0.5             # bb_upper = bb_middle + 0.5
    feats[:, 14] = feats[:, 13] - 0.5             # bb_lower = bb_middle - 0.5

    # atr (15) > 0
    feats[:, 15] = np.abs(feats[:, 15]) + 10.0

    # adx (16) in [0, 100]
    feats[:, 16] = np.abs(feats[:, 16]) + 20.0

    # ema_fast (6) > 0
    feats[:, 6] = np.abs(feats[:, 6]) + 100.0

    return feats


class TestRegimeFeatureExtractor:
    @pytest.fixture
    def extractor(self):
        return RegimeFeatureExtractor()

    def test_extract_returns_required_keys(self, extractor):
        ohlcv = _make_ohlcv(50)
        features = _make_features(50)
        result = extractor.extract(ohlcv, features)

        assert "adx" in result
        assert "bbw" in result
        assert "atr" in result
        assert "ema_slope" in result

    def test_extract_values_are_finite(self, extractor):
        ohlcv = _make_ohlcv(50)
        features = _make_features(50)
        result = extractor.extract(ohlcv, features)

        for k, v in result.items():
            assert np.isfinite(v), f"{k} is not finite: {v}"

    def test_extract_bbw_is_positive(self, extractor):
        ohlcv = _make_ohlcv(50)
        features = _make_features(50)
        result = extractor.extract(ohlcv, features)
        assert result["bbw"] > 0

    def test_extract_atr_is_positive(self, extractor):
        ohlcv = _make_ohlcv(50)
        features = _make_features(50)
        result = extractor.extract(ohlcv, features)
        assert result["atr"] > 0

    def test_insufficient_ohlcv_raises(self, extractor):
        ohlcv = _make_ohlcv(10)
        features = _make_features(10)
        with pytest.raises(RegimeDetectionError, match="ADX"):
            extractor.extract(ohlcv, features)

    def test_insufficient_features_raises(self, extractor):
        ohlcv = _make_ohlcv(50)
        features = _make_features(1)
        with pytest.raises(RegimeDetectionError, match="at least 2"):
            extractor.extract(ohlcv, features)
