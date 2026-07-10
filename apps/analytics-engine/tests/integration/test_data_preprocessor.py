"""Integration tests for TaDataPreprocessor.

Uses static OHLCV data to verify feature computation,
normalization, windowing, and target generation.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.domain.value_objects.model_config import ModelConfig
from app.infrastructure.training.data_preprocessor import (
    TaDataPreprocessor,
)


@pytest.fixture
def preprocessor() -> TaDataPreprocessor:
    return TaDataPreprocessor()


@pytest.fixture
def sample_ohlcv() -> list[dict]:
    """200 velas simuladas de BTC/USDT."""
    np.random.seed(42)
    data = []
    price = 50000.0
    for i in range(200):
        price *= 1 + np.random.normal(0, 0.002)
        high = price * (1 + abs(np.random.normal(0, 0.001)))
        low = price * (1 - abs(np.random.normal(0, 0.001)))
        vol = np.random.uniform(100, 1000)
        data.append({
            "timestamp": i * 3600 * 1000,
            "open": round(price, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(price, 2),
            "volume": round(vol, 4),
        })
    return data


@pytest.fixture
def config() -> ModelConfig:
    return ModelConfig(lookback=30)


class TestBuildFeatures:
    def test_returns_correct_shape(self, preprocessor, sample_ohlcv, config):
        features = await_async(preprocessor.build_features(sample_ohlcv, config))
        n_candles = len(sample_ohlcv)
        n_expected_features = len(config.features)
        assert features.shape == (n_candles, n_expected_features)

    def test_no_nan_after_bfill(self, preprocessor, sample_ohlcv, config):
        features = await_async(preprocessor.build_features(sample_ohlcv, config))
        assert not np.isnan(features).any(), "NaN values remain after bfill"

    def test_values_are_finite(self, preprocessor, sample_ohlcv, config):
        features = await_async(preprocessor.build_features(sample_ohlcv, config))
        assert np.isfinite(features).all()

    def test_short_ohlcv_raises(self, preprocessor, config):
        """1 vela no es suficiente para calcular indicadores (RSI, ATR, etc)."""
        short_data = [{"timestamp": 0, "open": 100, "high": 101,
                       "low": 99, "close": 100, "volume": 500}]
        with pytest.raises(Exception):
            await_async(preprocessor.build_features(short_data, config))


class TestNormalize:
    def test_zscore_gives_mean_near_zero(self, preprocessor, sample_ohlcv, config):
        features = await_async(preprocessor.build_features(sample_ohlcv, config))
        normalized, means, stds = await_async(preprocessor.normalize(features))
        assert normalized.shape == features.shape
        # Mean de cada columna deberia estar cerca de 0
        col_means = np.mean(normalized, axis=0)
        assert np.all(np.abs(col_means) < 1e-10)

    def test_zscore_gives_std_near_one(self, preprocessor, sample_ohlcv, config):
        features = await_async(preprocessor.build_features(sample_ohlcv, config))
        normalized, means, stds = await_async(preprocessor.normalize(features))
        col_stds = np.std(normalized, axis=0)
        assert np.all(np.abs(col_stds - 1.0) < 1e-10)

    def test_provided_means_stds(self, preprocessor, sample_ohlcv, config):
        features = await_async(preprocessor.build_features(sample_ohlcv, config))
        _, means, stds = await_async(preprocessor.normalize(features))
        # Re-normalizar con las medias/std guardadas
        normalized2, m2, s2 = await_async(
            preprocessor.normalize(features, means=means, stds=stds)
        )
        assert np.allclose(normalized2, normalized2)


class TestCreateWindows:
    def test_correct_window_count(self, preprocessor, sample_ohlcv, config):
        features = await_async(preprocessor.build_features(sample_ohlcv, config))
        windows = await_async(preprocessor.create_windows(features, config.lookback))
        n_expected = len(sample_ohlcv) - config.lookback + 1
        assert windows.shape[0] == n_expected

    def test_correct_window_shape(self, preprocessor, sample_ohlcv, config):
        features = await_async(preprocessor.build_features(sample_ohlcv, config))
        n_features = features.shape[1]
        windows = await_async(preprocessor.create_windows(features, config.lookback))
        assert windows.shape[1] == config.lookback
        assert windows.shape[2] == n_features

    def test_insufficient_data(self, preprocessor, config):
        tiny = np.random.randn(5, 3)
        with pytest.raises(Exception):  # InsufficientDataError
            await_async(preprocessor.create_windows(tiny, config.lookback))


class TestCreateTargets:
    def test_targets_one_hot_shape(self, preprocessor, sample_ohlcv, config):
        targets = await_async(preprocessor.create_targets(sample_ohlcv, config))
        assert targets.shape == (len(sample_ohlcv), 3)

    def test_targets_sum_to_one(self, preprocessor, sample_ohlcv, config):
        targets = await_async(preprocessor.create_targets(sample_ohlcv, config))
        row_sums = targets.sum(axis=1)
        assert np.allclose(row_sums, 1.0)

    def test_targets_are_one_hot(self, preprocessor, sample_ohlcv, config):
        targets = await_async(preprocessor.create_targets(sample_ohlcv, config))
        for row in targets:
            assert np.isclose(row.sum(), 1.0)
            assert set(np.where(row > 0)[0].tolist()) | {0, 1, 2}  # es one-hot valido

    def test_last_lookahead_are_hold(self, preprocessor, sample_ohlcv, config):
        targets = await_async(preprocessor.create_targets(sample_ohlcv, config))
        # Los ultimos target_lookahead elementos deberian ser HOLD [0, 1, 0]
        for i in range(-config.target_lookahead, 0):
            assert targets[i][1] == 1.0, f"index {i} should be HOLD [0,1,0]"


# Helper: pytest-asyncio no esta disponible, usamos corrutina manual
def await_async(coro):
    """Run an async function synchronously for testing."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        # Ya hay un loop corriendo (pytest-asyncio auto mode)
        import asyncio
        return asyncio.run_coroutine_threadsafe(coro, loop).result()
    return asyncio.run(coro)
