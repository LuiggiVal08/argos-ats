"""PredictEnsembleSignalUseCase — predicción completa del ensemble.

Pipeline:
  1. Cargar último checkpoint LSTM + XGBoost + MetaModel
  2. Fetch OHLCV reciente
  3. Build features + normalizar + ventanear
  4. Detectar régimen de mercado (RegimeDetector)
  5. LSTM predict → probs_lstm
  6. XGBoost predict → probs_xgb
  7. Construir input del MetaModel y predecir
  8. (opcional) MC Dropout Uncertainty
  9. Calibrar probabilidades
  10. ConfidenceFilter: probability >= threshold, uncertainty <= max, regime_ok
  → TradingSignal

Sad paths:
  - OhlcvSourceError: no hay datos
  - CheckpointNotFoundError: no hay modelo entrenado
  - StaleModelError: modelo vencido
  - PredictionError: fallo en inferencia
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np

from ...domain.entities.ensemble_pipeline import EnsemblePipeline
from ...domain.entities.market_context import MarketContext
from ...domain.entities.nova_quant_model import NovaQuantModel, StaleModelError
from ...domain.value_objects.model_config import ModelConfig
from ...domain.value_objects.market_regime import RegimeType
from ...domain.value_objects.signal_side import SignalSide
from ...domain.value_objects.trading_signal import TradingSignal
from ..ports.checkpoint_repository import CheckpointNotFoundError, CheckpointRepository
from ..ports.confidence_filter import ConfidenceFilter, ConfidenceResult, FilterDecision
from ..ports.data_preprocessor import DataPreprocessor, InsufficientDataError, PreprocessingError
from ..ports.meta_model import MetaModel, MetaModelError, MetaModelInput
from ..ports.model_predictor import ModelPredictor, PredictionError
from ..ports.ohlcv_source import OhlcvSource, OhlcvSourceError
from ..ports.probability_calibrator import CalibrationError, ProbabilityCalibrator
from ..ports.regime_detector import RegimeDetectionError, RegimeDetector
from ..ports.uncertainty_estimator import UncertaintyEstimator, UncertaintyResult


class PredictEnsembleError(RuntimeError):
    """Raised when ensemble prediction fails."""


@dataclass(frozen=True)
class PredictEnsembleResult:
    signal: TradingSignal
    probability_raw: float
    probability_calibrated: float | None = None
    uncertainty: float | None = None
    regime: str = "UNKNOWN"
    adx: float = 0.0
    filtered: bool = False
    filter_reason: str = ""
    model_version: str = ""
    predicted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


class PredictEnsembleSignalUseCase:
    """Orquesta la predicción completa del ensemble.

    Uso:
        uc = PredictEnsembleSignalUseCase(
            ohlcv_source, preprocessor,
            lstm_predictor, xgb_predictor,
            repo
        )
        result = await uc.execute(symbol="BTC/USDT")
    """

    def __init__(
        self,
        ohlcv_source: OhlcvSource,
        preprocessor: DataPreprocessor,
        lstm_predictor: ModelPredictor,
        xgb_predictor: ModelPredictor,
        repo: CheckpointRepository,
        meta_model: MetaModel | None = None,
        calibrator: ProbabilityCalibrator | None = None,
        uncertainty_estimator: UncertaintyEstimator | None = None,
        confidence_filter: ConfidenceFilter | None = None,
        regime_detector: RegimeDetector | None = None,
    ) -> None:
        self._ohlcv = ohlcv_source
        self._preprocessor = preprocessor
        self._lstm = lstm_predictor
        self._xgb = xgb_predictor
        self._repo = repo
        self._meta_model = meta_model
        self._calibrator = calibrator
        self._uncertainty = uncertainty_estimator
        self._confidence_filter = confidence_filter
        self._regime_detector = regime_detector

    async def execute(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 200,
        skip_stale_check: bool = False,
    ) -> PredictEnsembleResult:
        model, _weights = await self._load_model(skip_stale_check, symbol=symbol)
        cfg = model.config

        ohlcv = await self._fetch_ohlcv(symbol, timeframe, limit, cfg)
        features_raw = await self._build_features(ohlcv, cfg)
        model.validate_input(features_raw.shape[1])
        features_norm, _means, _stds = await self._normalize(features_raw, model)
        last_window = await self._create_window(features_norm, cfg)

        lstm_signal = await self._lstm.predict(last_window, confidence_threshold=0.0)
        xgb_signal = await self._xgb.predict(last_window, confidence_threshold=0.0)

        lstm_probs = self._signal_to_probs(lstm_signal)
        xgb_probs = self._signal_to_probs(xgb_signal)

        market_features = self._extract_market(features_raw, cfg.features)
        market_context = await self._detect_regime(market_features)

        # Step 1: if MetaModel is available, use it for final decision
        side: SignalSide
        confidence: float
        metadata: dict[str, Any]

        if self._meta_model is not None:
            try:
                meta_input = MetaModelInput(
                    lstm_probs=np.array(lstm_probs),
                    xgb_probs=np.array(xgb_probs),
                    context=market_features,
                )
                meta_probs = await self._meta_model.predict(meta_input)
                probs_arr = meta_probs
            except MetaModelError as e:
                raise PredictEnsembleError(f"meta_model_failed: {e}") from e
        else:
            # Fallback: simple averaging
            try:
                meta_input = EnsemblePipeline.build_meta_input(
                    lstm_probs, xgb_probs, market_features
                )
            except ValueError as e:
                raise PredictEnsembleError(f"meta_input_failed: {e}") from e
            avg_probs = self._average_probs(lstm_probs, xgb_probs)
            probs_arr = np.array(avg_probs)

        # Step 2: Calibrate probabilities
        if self._calibrator is not None:
            try:
                calibrated = await self._calibrator.calibrate(probs_arr)
                probability_calibrated = float(np.max(calibrated))
            except CalibrationError:
                probability_calibrated = None
                calibrated = probs_arr
        else:
            probability_calibrated = None
            calibrated = probs_arr

        # Step 3: Ensemble decision
        side, confidence, metadata = self._ensemble_decision_raw(calibrated, model, lstm_probs, xgb_probs)

        # Step 4: Uncertainty estimation
        uncertainty: float | None = None
        if self._uncertainty is not None:
            try:
                uncertainty_result = await self._uncertainty.estimate(last_window, n_samples=30)
                uncertainty = uncertainty_result.max_std
            except Exception:
                uncertainty = None

        # Step 5: Confidence filter
        filtered = False
        filter_reason = ""
        if self._confidence_filter is not None:
            regime_ok = market_context.regime in (RegimeType.TRENDING, RegimeType.HIGH_VOLATILITY)
            threshold = cfg.confidence_threshold
            max_uncertainty = cfg.confidence_threshold * 0.2
            result = await self._confidence_filter.evaluate(
                probability=confidence,
                uncertainty=uncertainty or 0.0,
                regime_ok=regime_ok,
                threshold=threshold,
                max_uncertainty=max_uncertainty,
            )
            filtered = result.decision == FilterDecision.HOLD
            filter_reason = result.reason

        trading_signal = TradingSignal(
            side=side,
            confidence=confidence,
            model_version=model.model_version,
            metadata=metadata,
        )

        return PredictEnsembleResult(
            signal=trading_signal,
            probability_raw=confidence,
            probability_calibrated=probability_calibrated,
            uncertainty=uncertainty,
            regime=market_context.regime.value,
            adx=market_context.adx,
            filtered=filtered,
            filter_reason=filter_reason,
            model_version=model.model_version,
            metadata=metadata,
        )

    async def _load_model(self, skip_stale_check: bool, symbol: str = "") -> tuple[NovaQuantModel, bytes]:
        try:
            model, weights = await self._repo.load_latest(symbol=symbol)
        except CheckpointNotFoundError as e:
            raise PredictEnsembleError("no_trained_model: train first via POST /training/train-ensemble") from e

        if not skip_stale_check:
            try:
                model.assert_not_stale()
            except StaleModelError as e:
                raise PredictEnsembleError(f"stale_model: {e}") from e

        return model, weights

    async def _fetch_ohlcv(
        self, symbol: str, timeframe: str, limit: int, cfg: ModelConfig
    ) -> list[dict]:
        try:
            ohlcv = await self._ohlcv.fetch_ohlcv(
                symbol=symbol, timeframe=timeframe, limit=limit
            )
        except OhlcvSourceError as e:
            raise PredictEnsembleError(f"ohlcv_fetch_failed: {e}") from e

        if len(ohlcv) < cfg.lookback + 1:
            raise PredictEnsembleError(
                f"insufficient_data: got {len(ohlcv)}, need at least {cfg.lookback + 1}"
            )
        return ohlcv

    async def _build_features(
        self, ohlcv: list[dict], cfg: ModelConfig
    ) -> np.ndarray:
        try:
            return await self._preprocessor.build_features(ohlcv, cfg)
        except PreprocessingError as e:
            raise PredictEnsembleError(f"feature_build_failed: {e}") from e

    async def _normalize(
        self, features_raw: np.ndarray, model: NovaQuantModel
    ) -> tuple[np.ndarray, tuple[float, ...], tuple[float, ...]]:
        return await self._preprocessor.normalize(
            features_raw,
            means=model.feature_means,
            stds=model.feature_stds,
        )

    async def _create_window(
        self, features_norm: np.ndarray, cfg: ModelConfig
    ) -> np.ndarray:
        try:
            windows = await self._preprocessor.create_windows(features_norm, cfg.lookback)
        except InsufficientDataError as e:
            raise PredictEnsembleError(f"windowing_failed: {e}") from e
        return windows[-1]

    @staticmethod
    def _signal_to_probs(signal: TradingSignal) -> tuple[float, float, float]:
        buy = signal.confidence if signal.side == SignalSide.BUY else 0.0
        sell = signal.confidence if signal.side == SignalSide.SELL else 0.0
        hold = signal.confidence if signal.side == SignalSide.HOLD else 0.0
        total = buy + sell + hold
        if total > 0:
            buy /= total
            sell /= total
            hold /= total
        else:
            buy = sell = hold = 1.0 / 3.0
        return (buy, sell, hold)

    @staticmethod
    def _extract_market(
        features_raw: np.ndarray, feature_names: tuple[str, ...],
    ) -> dict[str, float]:
        last = features_raw[-1]
        n = features_raw.shape[1]
        def _idx(name: str) -> int:
            return feature_names.index(name)

        bb_u = float(last[_idx('bb_upper')]) if _idx('bb_upper') < n else 0.0
        bb_m = float(last[_idx('bb_middle')]) if _idx('bb_middle') < n else 1.0
        bb_l = float(last[_idx('bb_lower')]) if _idx('bb_lower') < n else 0.0
        bbw = (bb_u - bb_l) / (bb_m + 1e-10)

        adx = float(last[_idx('adx')]) if _idx('adx') < n else 25.0
        atr = float(last[_idx('atr')]) if _idx('atr') < n else 100.0
        volume = float(last[_idx('volume')]) if _idx('volume') < n else 1000.0
        rsi = float(last[_idx('rsi')]) if _idx('rsi') < n else 50.0
        return {"adx": adx, "bbw": bbw, "atr": atr, "volume": volume, "rsi": rsi}

    async def _detect_regime(
        self, market_features: dict[str, float]
    ) -> MarketContext:
        if self._regime_detector is not None:
            try:
                return self._regime_detector.detect(market_features)
            except RegimeDetectionError:
                pass
        # Fallback: inline ADX heuristic
        adx = market_features.get("adx", 25.0)
        regime = RegimeType.TRENDING if adx >= 25 else RegimeType.RANGING
        return MarketContext(
            regime=regime,
            adx=market_features.get("adx", 0.0),
            bbw=market_features.get("bbw", 0.1),
            atr=market_features.get("atr", 100.0),
            ema_slope=market_features.get("ema_slope", 0.0),
        )

    @staticmethod
    def _average_probs(
        lstm_probs: tuple[float, float, float],
        xgb_probs: tuple[float, float, float],
    ) -> tuple[float, float, float]:
        avg_buy = (lstm_probs[0] + xgb_probs[0]) / 2.0
        avg_sell = (lstm_probs[1] + xgb_probs[1]) / 2.0
        avg_hold = (lstm_probs[2] + xgb_probs[2]) / 2.0
        total = avg_buy + avg_sell + avg_hold
        if total > 0:
            avg_buy /= total
            avg_sell /= total
            avg_hold /= total
        return (avg_buy, avg_sell, avg_hold)

    @staticmethod
    def _ensemble_decision_raw(
        probs: np.ndarray,
        model: NovaQuantModel,
        lstm_probs: tuple[float, float, float],
        xgb_probs: tuple[float, float, float],
    ) -> tuple[SignalSide, float, dict[str, Any]]:
        buy_p = float(probs[0])
        sell_p = float(probs[1])
        hold_p = float(probs[2])
        threshold = model.config.confidence_threshold

        if buy_p > sell_p and buy_p > hold_p and buy_p >= threshold:
            side = SignalSide.BUY
            confidence = buy_p
        elif sell_p > buy_p and sell_p > hold_p and sell_p >= threshold:
            side = SignalSide.SELL
            confidence = sell_p
        else:
            side = SignalSide.HOLD
            confidence = hold_p

        metadata = {
            "lstm_buy": lstm_probs[0],
            "lstm_sell": lstm_probs[1],
            "lstm_hold": lstm_probs[2],
            "xgb_buy": xgb_probs[0],
            "xgb_sell": xgb_probs[1],
            "xgb_hold": xgb_probs[2],
        }
        return side, confidence, metadata

    @staticmethod
    def _ensemble_decision(
        lstm_probs: tuple[float, float, float],
        xgb_probs: tuple[float, float, float],
        meta_input: list[float],
        model: NovaQuantModel,
    ) -> tuple[SignalSide, float, dict[str, Any]]:
        avg_buy, avg_sell, avg_hold = PredictEnsembleSignalUseCase._average_probs(lstm_probs, xgb_probs)
        threshold = model.config.confidence_threshold

        if avg_buy > avg_sell and avg_buy > avg_hold and avg_buy >= threshold:
            side = SignalSide.BUY
            confidence = avg_buy
        elif avg_sell > avg_buy and avg_sell > avg_hold and avg_sell >= threshold:
            side = SignalSide.SELL
            confidence = avg_sell
        else:
            side = SignalSide.HOLD
            confidence = avg_hold

        metadata = {
            "lstm_buy": lstm_probs[0],
            "lstm_sell": lstm_probs[1],
            "lstm_hold": lstm_probs[2],
            "xgb_buy": xgb_probs[0],
            "xgb_sell": xgb_probs[1],
            "xgb_hold": xgb_probs[2],
        }
        return side, confidence, metadata
