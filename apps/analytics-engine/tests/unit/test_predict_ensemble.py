"""Tests for PredictEnsembleSignalUseCase — ensemble prediction pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import numpy as np
import pytest

from app.application.use_cases.predict_ensemble import (
    PredictEnsembleError,
    PredictEnsembleResult,
    PredictEnsembleSignalUseCase,
)
from app.domain.entities.market_context import MarketContext
from app.domain.entities.nova_quant_model import NovaQuantModel, StaleModelError
from app.domain.value_objects.market_regime import RegimeType
from app.domain.value_objects.model_config import ModelConfig
from app.domain.value_objects.signal_side import SignalSide
from app.domain.value_objects.trading_signal import TradingSignal


# ── Helpers ───────────────────────────────────────────────────────────

N_FEATURES = 20
DEFAULT_FEATURE_MEANS = tuple(float(i) for i in range(N_FEATURES))
DEFAULT_FEATURE_STDS = tuple(float(i + 1) for i in range(N_FEATURES))


def _make_model(config: ModelConfig | None = None) -> NovaQuantModel:
    cfg = config or ModelConfig()
    return NovaQuantModel(
        config=cfg,
        model_version="test/v1.0",
        trained_at=datetime.now(timezone.utc),
        weights_hash="abc123",
        feature_means=DEFAULT_FEATURE_MEANS,
        feature_stds=DEFAULT_FEATURE_STDS,
        metrics={"val_loss": 0.5, "val_accuracy": 0.65},
    )


# ── Mock adapters ─────────────────────────────────────────────────────


class _MockOhlcvSource:
    def __init__(self, count: int = 500):
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

    async def normalize(self, features: np.ndarray, means=None, stds=None) -> tuple:
        m = np.mean(features, axis=0)
        s = np.std(features, axis=0) + 1e-6
        normed = (features - m) / s
        return normed, tuple(float(x) for x in m), tuple(float(x) for x in s)

    async def create_windows(self, features: np.ndarray, lookback: int) -> np.ndarray:
        n = features.shape[0]
        windows = np.lib.stride_tricks.sliding_window_view(features, lookback, axis=0)
        return windows.transpose(0, 2, 1)


class _MockPredictor:
    def __init__(self, side: SignalSide = SignalSide.BUY, confidence: float = 0.8):
        self._side = side
        self._confidence = confidence

    async def predict(self, window: np.ndarray, confidence_threshold: float = 0.0) -> TradingSignal:
        return TradingSignal(
            side=self._side,
            confidence=self._confidence,
            timestamp=datetime.now(timezone.utc),
        )


class _MockMetaModel:
    def __init__(self, fail: bool = False):
        self._fail = fail

    async def predict(self, meta_input) -> np.ndarray:
        if self._fail:
            from app.application.ports.meta_model import MetaModelError
            raise MetaModelError("meta_prediction_failed")
        return np.array([0.8, 0.1, 0.1])


class _MockCalibrator:
    def __init__(self, fail: bool = False):
        self._fail = fail

    async def calibrate(self, probs: np.ndarray) -> np.ndarray:
        if self._fail:
            from app.application.ports.probability_calibrator import CalibrationError
            raise CalibrationError("calibration_failed")
        return probs


class _MockUncertaintyEstimator:
    def __init__(self, max_std: float = 0.05):
        self._max_std = max_std

    async def estimate(self, window: np.ndarray, n_samples: int = 30) -> Any:
        return _UncertaintyResult(max_std=self._max_std)


@dataclass
class _UncertaintyResult:
    max_std: float


class _MockConfidenceFilter:
    def __init__(self, decision: str = "EXECUTE"):
        self._decision = decision

    async def evaluate(
        self, probability: float, uncertainty: float, regime_ok: bool,
        threshold: float = 0.7, max_uncertainty: float = 0.15,
    ) -> Any:
        from app.application.ports.confidence_filter import (
            ConfidenceResult,
            FilterDecision,
        )
        ok = self._decision == "EXECUTE"
        return ConfidenceResult(
            decision=FilterDecision.EXECUTE if ok else FilterDecision.HOLD,
            reason="" if ok else "mock_filtered",
            probability=probability,
            uncertainty=uncertainty,
            regime_ok=regime_ok,
            probability_ok=probability >= threshold,
            uncertainty_ok=uncertainty <= max_uncertainty,
        )


class _MockCheckpointRepo:
    def __init__(self, model: NovaQuantModel | None = None, fail: bool = False):
        self._model = model
        self._fail = fail

    async def load_latest(self, symbol: str = "") -> tuple[NovaQuantModel, bytes]:
        if self._fail:
            from app.application.ports.checkpoint_repository import CheckpointNotFoundError
            raise CheckpointNotFoundError("no_checkpoint")
        if self._model is None:
            self._model = _make_model()
        return self._model, b"mock_weights"

    async def save(self, model, weights, symbol: str = ""):
        return "mock_save"

    async def load_version(self, version: str, symbol: str = ""):
        return _make_model(), b"mock_weights"

    async def list_versions(self, symbol: str = ""):
        return ["v1"]


class _MockRegimeDetector:
    def __init__(self, regime: RegimeType = RegimeType.TRENDING):
        self._regime = regime

    def detect(self, features: dict[str, float]) -> MarketContext:
        return MarketContext(
            regime=self._regime,
            adx=features.get("adx", 25.0),
            bbw=features.get("bbw", 0.1),
            atr=features.get("atr", 100.0),
            ema_slope=features.get("ema_slope", 0.0),
        )


# ── Tests ─────────────────────────────────────────────────────────────


class TestPredictEnsembleSignalUseCase:
    @pytest.fixture
    def use_case(self):
        return PredictEnsembleSignalUseCase(
            ohlcv_source=_MockOhlcvSource(),
            preprocessor=_MockPreprocessor(),
            lstm_predictor=_MockPredictor(),
            xgb_predictor=_MockPredictor(),
            repo=_MockCheckpointRepo(),
        )

    async def test_basic_predict_no_meta(self, use_case):
        result = await use_case.execute(symbol="BTC/USDT")
        assert isinstance(result, PredictEnsembleResult)
        assert result.signal.side in (SignalSide.BUY, SignalSide.SELL, SignalSide.HOLD)
        assert result.regime in ("TRENDING", "RANGING")

    async def test_with_meta_model(self):
        uc = PredictEnsembleSignalUseCase(
            ohlcv_source=_MockOhlcvSource(),
            preprocessor=_MockPreprocessor(),
            lstm_predictor=_MockPredictor(),
            xgb_predictor=_MockPredictor(),
            repo=_MockCheckpointRepo(),
            meta_model=_MockMetaModel(),
        )
        result = await uc.execute(symbol="BTC/USDT")
        assert result.metadata is not None

    async def test_with_calibrator(self):
        uc = PredictEnsembleSignalUseCase(
            ohlcv_source=_MockOhlcvSource(),
            preprocessor=_MockPreprocessor(),
            lstm_predictor=_MockPredictor(),
            xgb_predictor=_MockPredictor(),
            repo=_MockCheckpointRepo(),
            calibrator=_MockCalibrator(),
        )
        result = await uc.execute(symbol="BTC/USDT")
        assert result.probability_calibrated is not None

    async def test_with_confidence_filter(self):
        uc = PredictEnsembleSignalUseCase(
            ohlcv_source=_MockOhlcvSource(),
            preprocessor=_MockPreprocessor(),
            lstm_predictor=_MockPredictor(),
            xgb_predictor=_MockPredictor(),
            repo=_MockCheckpointRepo(),
            confidence_filter=_MockConfidenceFilter(decision="EXECUTE"),
        )
        result = await uc.execute(symbol="BTC/USDT")
        assert result.filtered is False

    async def test_confidence_filter_holds(self):
        uc = PredictEnsembleSignalUseCase(
            ohlcv_source=_MockOhlcvSource(),
            preprocessor=_MockPreprocessor(),
            lstm_predictor=_MockPredictor(),
            xgb_predictor=_MockPredictor(),
            repo=_MockCheckpointRepo(),
            confidence_filter=_MockConfidenceFilter(decision="HOLD"),
        )
        result = await uc.execute(symbol="BTC/USDT")
        assert result.filtered is True
        assert result.filter_reason == "mock_filtered"

    async def test_with_uncertainty_estimator(self):
        uc = PredictEnsembleSignalUseCase(
            ohlcv_source=_MockOhlcvSource(),
            preprocessor=_MockPreprocessor(),
            lstm_predictor=_MockPredictor(),
            xgb_predictor=_MockPredictor(),
            repo=_MockCheckpointRepo(),
            uncertainty_estimator=_MockUncertaintyEstimator(max_std=0.05),
        )
        result = await uc.execute(symbol="BTC/USDT")
        assert result.uncertainty is not None

    async def test_with_regime_detector(self):
        uc = PredictEnsembleSignalUseCase(
            ohlcv_source=_MockOhlcvSource(),
            preprocessor=_MockPreprocessor(),
            lstm_predictor=_MockPredictor(),
            xgb_predictor=_MockPredictor(),
            repo=_MockCheckpointRepo(),
            regime_detector=_MockRegimeDetector(regime=RegimeType.RANGING),
        )
        result = await uc.execute(symbol="BTC/USDT")
        assert result.regime == "RANGING"

    async def test_full_pipeline(self):
        uc = PredictEnsembleSignalUseCase(
            ohlcv_source=_MockOhlcvSource(),
            preprocessor=_MockPreprocessor(),
            lstm_predictor=_MockPredictor(),
            xgb_predictor=_MockPredictor(),
            repo=_MockCheckpointRepo(),
            meta_model=_MockMetaModel(),
            calibrator=_MockCalibrator(),
            uncertainty_estimator=_MockUncertaintyEstimator(max_std=0.05),
            confidence_filter=_MockConfidenceFilter(decision="EXECUTE"),
            regime_detector=_MockRegimeDetector(regime=RegimeType.TRENDING),
        )
        result = await uc.execute(symbol="BTC/USDT")
        assert result.probability_calibrated is not None
        assert result.uncertainty is not None
        assert result.filtered is False
        assert result.regime == "TRENDING"

    async def test_no_checkpoint_raises_error(self):
        uc = PredictEnsembleSignalUseCase(
            ohlcv_source=_MockOhlcvSource(),
            preprocessor=_MockPreprocessor(),
            lstm_predictor=_MockPredictor(),
            xgb_predictor=_MockPredictor(),
            repo=_MockCheckpointRepo(fail=True),
        )
        with pytest.raises(PredictEnsembleError, match="no_trained_model"):
            await uc.execute(symbol="BTC/USDT")

    async def test_stale_model_raises_error(self):
        old = datetime(2020, 1, 1, tzinfo=timezone.utc)
        cfg = ModelConfig()
        stale = NovaQuantModel(
            config=cfg,
            model_version="stale/v1",
            trained_at=old,
            weights_hash="def456",
            feature_means=DEFAULT_FEATURE_MEANS,
            feature_stds=DEFAULT_FEATURE_STDS,
        )
        uc = PredictEnsembleSignalUseCase(
            ohlcv_source=_MockOhlcvSource(),
            preprocessor=_MockPreprocessor(),
            lstm_predictor=_MockPredictor(),
            xgb_predictor=_MockPredictor(),
            repo=_MockCheckpointRepo(model=stale),
        )
        with pytest.raises(PredictEnsembleError, match="stale_model"):
            await uc.execute(symbol="BTC/USDT")

    async def test_skip_stale_check(self):
        old = datetime(2020, 1, 1, tzinfo=timezone.utc)
        cfg = ModelConfig()
        stale = NovaQuantModel(
            config=cfg,
            model_version="stale/v1",
            trained_at=old,
            weights_hash="def456",
            feature_means=DEFAULT_FEATURE_MEANS,
            feature_stds=DEFAULT_FEATURE_STDS,
        )
        uc = PredictEnsembleSignalUseCase(
            ohlcv_source=_MockOhlcvSource(),
            preprocessor=_MockPreprocessor(),
            lstm_predictor=_MockPredictor(),
            xgb_predictor=_MockPredictor(),
            repo=_MockCheckpointRepo(model=stale),
        )
        result = await uc.execute(symbol="BTC/USDT", skip_stale_check=True)
        assert isinstance(result, PredictEnsembleResult)

    async def test_insufficient_ohlcv_raises_error(self):
        uc = PredictEnsembleSignalUseCase(
            ohlcv_source=_MockOhlcvSource(count=5),
            preprocessor=_MockPreprocessor(),
            lstm_predictor=_MockPredictor(),
            xgb_predictor=_MockPredictor(),
            repo=_MockCheckpointRepo(),
        )
        with pytest.raises(PredictEnsembleError, match="insufficient_data"):
            await uc.execute(symbol="BTC/USDT")

    async def test_calibrator_failure_falls_back(self):
        uc = PredictEnsembleSignalUseCase(
            ohlcv_source=_MockOhlcvSource(),
            preprocessor=_MockPreprocessor(),
            lstm_predictor=_MockPredictor(),
            xgb_predictor=_MockPredictor(),
            repo=_MockCheckpointRepo(),
            calibrator=_MockCalibrator(fail=True),
        )
        result = await uc.execute(symbol="BTC/USDT")
        assert result.probability_calibrated is None

    async def test_meta_model_failure_raises_error(self):
        uc = PredictEnsembleSignalUseCase(
            ohlcv_source=_MockOhlcvSource(),
            preprocessor=_MockPreprocessor(),
            lstm_predictor=_MockPredictor(),
            xgb_predictor=_MockPredictor(),
            repo=_MockCheckpointRepo(),
            meta_model=_MockMetaModel(fail=True),
        )
        with pytest.raises(PredictEnsembleError, match="meta_model_failed"):
            await uc.execute(symbol="BTC/USDT")
