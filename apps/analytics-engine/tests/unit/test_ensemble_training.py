"""Tests for EnsembleTrainingUseCase — Walk Forward + OOF + MetaModel."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import numpy as np
import pytest

from app.application.use_cases.ensemble_training import (
    EnsembleTrainingError,
    EnsembleTrainingUseCase,
)
from app.domain.entities.nova_quant_model import NovaQuantModel
from app.domain.value_objects.model_config import ModelConfig
from app.domain.value_objects.trading_signal import TradingSignal
from app.domain.value_objects.signal_side import SignalSide


# ── Mock adapters ──────────────────────────────────────────────────────

N_FEATURES = 20  # matches ModelConfig default


class _MockOhlcvSource:
    def __init__(self, count: int = 10000):
        self._count = count

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> list[dict]:
        n = min(limit, self._count)
        return [
            {
                "timestamp": i * 3600 * 1000,
                "open": 50000.0, "high": 50100.0, "low": 49900.0,
                "close": 50050.0, "volume": 100.0,
            }
            for i in range(n)
        ]


class _MockPreprocessor:
    async def build_features(self, ohlcv: list, cfg: ModelConfig) -> np.ndarray:
        return np.random.randn(len(ohlcv), len(cfg.features)).astype(np.float32)

    async def create_targets(self, ohlcv: list, cfg: ModelConfig) -> np.ndarray:
        n = len(ohlcv)
        arr = np.zeros((n, 3), dtype=np.float32)
        arr[:, 0] = 0.4
        arr[:, 1] = 0.3
        arr[:, 2] = 0.3
        return arr

    async def normalize(self, features: np.ndarray) -> tuple:
        means = tuple(float(np.mean(features[:, i])) for i in range(features.shape[1]))
        stds = tuple(float(np.std(features[:, i]) + 1e-6) for i in range(features.shape[1]))
        normed = (features - np.array(means)) / np.array(stds)
        return normed, means, stds

    async def create_windows(self, features: np.ndarray, lookback: int) -> np.ndarray:
        n = features.shape[0]
        windows = np.lib.stride_tricks.sliding_window_view(features, lookback, axis=0)
        return windows.transpose(0, 2, 1)


class _MockAnalyzer:
    async def compute_correlations(self, features, targets, feature_names):
        return {f: float(np.random.rand()) for f in feature_names}

    async def filter_features(self, features, targets, feature_names, min_correlation=0.05):
        return features, list(feature_names)


class _MockTrainer:
    def __init__(self, fail: bool = False):
        self._fail = fail
        self.trained_count = 0

    async def train(self, cfg, x_train, y_train, x_val, y_val) -> dict[str, Any]:
        self.trained_count += 1
        if self._fail:
            from app.application.ports.model_trainer import TrainingError
            raise TrainingError("training_failed")
        return {
            "val_loss": 0.5,
            "val_accuracy": 0.65,
            "epochs_trained": 50,
            "best_epoch": 45,
            "version": f"mock/v1.{self.trained_count}",
        }


class _MockPredictor:
    def __init__(self, side: SignalSide = SignalSide.BUY, confidence: float = 0.75):
        self._side = side
        self._confidence = confidence
        self.predict_count = 0

    async def predict(self, window: np.ndarray, confidence_threshold: float = 0.0) -> TradingSignal:
        self.predict_count += 1
        return TradingSignal(
            side=self._side,
            confidence=self._confidence,
            timestamp=datetime.now(timezone.utc),
        )


class _MockMetaModel:
    def __init__(self, fail: bool = False):
        self._fail = fail
        self.train_count = 0
        self.predict_count = 0

    async def predict(self, input_data) -> np.ndarray:
        self.predict_count += 1
        return np.array([0.5, 0.25, 0.25], dtype=np.float32)

    async def train(self, features: np.ndarray, targets: np.ndarray) -> dict[str, Any]:
        self.train_count += 1
        if self._fail:
            from app.application.ports.meta_model import MetaModelTrainError
            raise MetaModelTrainError("meta_train_failed")
        return {"train_accuracy": 0.72}


class _MockCalibrator:
    def __init__(self, fail: bool = False):
        self._fail = fail
        self.fit_count = 0

    async def fit(self, probs: np.ndarray, labels: np.ndarray) -> None:
        self.fit_count += 1
        if self._fail:
            from app.application.ports.probability_calibrator import CalibrationError
            raise CalibrationError("calibration_failed")


class _MockCheckpointRepo:
    def __init__(self):
        self.saved = []

    async def save(self, model: NovaQuantModel, weights_bytes: bytes, symbol: str = "") -> str:
        self.saved.append((model, weights_bytes))
        return f"checkpoint/{model.model_version}"

    async def load_latest(self, symbol: str = ""):
        raise NotImplementedError

    async def load_version(self, version: str, symbol: str = ""):
        raise NotImplementedError

    async def list_versions(self, symbol: str = ""):
        return []


# ── Test class ────────────────────────────────────────────────────────


class TestEnsembleTrainingUseCase:
    @pytest.fixture
    def use_case(self):
        return EnsembleTrainingUseCase(
            ohlcv_source=_MockOhlcvSource(count=5000),
            preprocessor=_MockPreprocessor(),
            analyzer=_MockAnalyzer(),
            lstm_trainer=_MockTrainer(),
            xgb_trainer=_MockTrainer(),
            lstm_predictor=_MockPredictor(),
            xgb_predictor=_MockPredictor(),
            meta_model=_MockMetaModel(),
            calibrator=_MockCalibrator(),
            repo=_MockCheckpointRepo(),
        )

    async def test_happy_path(self, use_case):
        result = await use_case.execute(symbol="BTC/USDT")
        assert result.n_windows == 5
        assert result.windows_passed == 5
        assert result.oof_size > 0
        assert result.n_features > 0
        assert result.meta_train_accuracy is not None
        assert len(result.feature_means) == N_FEATURES
        assert len(result.feature_stds) == N_FEATURES

    async def test_insufficient_data_raises_error(self):
        uc = EnsembleTrainingUseCase(
            ohlcv_source=_MockOhlcvSource(count=10),
            preprocessor=_MockPreprocessor(),
            analyzer=_MockAnalyzer(),
            lstm_trainer=_MockTrainer(),
            xgb_trainer=_MockTrainer(),
            lstm_predictor=_MockPredictor(),
            xgb_predictor=_MockPredictor(),
            meta_model=_MockMetaModel(),
            calibrator=_MockCalibrator(),
            repo=_MockCheckpointRepo(),
        )
        with pytest.raises(EnsembleTrainingError, match="insufficient_data"):
            await uc.execute(symbol="BTC/USDT")

    async def test_lstm_training_failure_raises_error(self):
        uc = EnsembleTrainingUseCase(
            ohlcv_source=_MockOhlcvSource(count=5000),
            preprocessor=_MockPreprocessor(),
            analyzer=_MockAnalyzer(),
            lstm_trainer=_MockTrainer(fail=True),
            xgb_trainer=_MockTrainer(),
            lstm_predictor=_MockPredictor(),
            xgb_predictor=_MockPredictor(),
            meta_model=_MockMetaModel(),
            calibrator=_MockCalibrator(),
            repo=_MockCheckpointRepo(),
        )
        with pytest.raises(EnsembleTrainingError, match="lstm_training_failed"):
            await uc.execute(symbol="BTC/USDT")

    async def test_meta_model_failure_raises_error(self):
        uc = EnsembleTrainingUseCase(
            ohlcv_source=_MockOhlcvSource(count=5000),
            preprocessor=_MockPreprocessor(),
            analyzer=_MockAnalyzer(),
            lstm_trainer=_MockTrainer(),
            xgb_trainer=_MockTrainer(),
            lstm_predictor=_MockPredictor(),
            xgb_predictor=_MockPredictor(),
            meta_model=_MockMetaModel(fail=True),
            calibrator=_MockCalibrator(),
            repo=_MockCheckpointRepo(),
        )
        with pytest.raises(EnsembleTrainingError, match="meta_model_training_failed"):
            await uc.execute(symbol="BTC/USDT")

    async def test_calibrator_failure_returns_none_version(self):
        uc = EnsembleTrainingUseCase(
            ohlcv_source=_MockOhlcvSource(count=5000),
            preprocessor=_MockPreprocessor(),
            analyzer=_MockAnalyzer(),
            lstm_trainer=_MockTrainer(),
            xgb_trainer=_MockTrainer(),
            lstm_predictor=_MockPredictor(),
            xgb_predictor=_MockPredictor(),
            meta_model=_MockMetaModel(),
            calibrator=_MockCalibrator(fail=True),
            repo=_MockCheckpointRepo(),
        )
        result = await uc.execute(symbol="BTC/USDT")
        assert result.calibrator_version is None

    async def test_custom_config(self, use_case):
        cfg = ModelConfig(
            lookback=20,
            confidence_threshold=0.6,
            layers=(64, 32, 16),
        )
        result = await use_case.execute(symbol="ETH/USDT", config=cfg)
        assert result.windows_passed == 5
        assert result.oof_size > 0

    async def test_predictors_invoked_per_window(self):
        lstm_pred = _MockPredictor()
        xgb_pred = _MockPredictor()
        uc = EnsembleTrainingUseCase(
            ohlcv_source=_MockOhlcvSource(count=5000),
            preprocessor=_MockPreprocessor(),
            analyzer=_MockAnalyzer(),
            lstm_trainer=_MockTrainer(),
            xgb_trainer=_MockTrainer(),
            lstm_predictor=lstm_pred,
            xgb_predictor=xgb_pred,
            meta_model=_MockMetaModel(),
            calibrator=_MockCalibrator(),
            repo=_MockCheckpointRepo(),
        )
        await uc.execute(symbol="BTC/USDT")
        # Each window predicts on val set → at least 1 per window
        assert lstm_pred.predict_count >= 5
        assert xgb_pred.predict_count >= 5
