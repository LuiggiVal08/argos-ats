"""EnsembleTrainingUseCase — entrenamiento del ensemble completo.

Pipeline:
  1. Fetch OHLCV histórico
  2. Build features + targets + normalizar
  3. Walk Forward de 5 ventanas:
     - Cada ventana: train LSTM + XGBoost, predecir val (OOF)
  4. Colectar OOF predictions de todas las ventanas
  5. Entrenar MetaModel (XGBoost stacking) con OOF
  6. Calibrar probabilidades con Platt Scaling
  7. Guardar todos los componentes versionados
  8. Retornar métricas agregadas

Sad paths:
  - OhlcvSourceError: datos insuficientes
  - TrainingError: fallo en entrenamiento de algún componente
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
import structlog

log = structlog.get_logger()

from ...domain.entities.model_registry import ComponentRecord
from ...domain.value_objects.model_config import ModelConfig
from ...domain.value_objects.signal_side import SignalSide
from ...domain.value_objects.trading_signal import TradingSignal
from ..ports.checkpoint_repository import CheckpointRepository
from ..ports.data_preprocessor import DataPreprocessor, InsufficientDataError, PreprocessingError
from ..ports.feature_analyzer import AnalysisError, FeatureAnalyzer
from ..ports.meta_model import MetaModel, MetaModelError, MetaModelInput, MetaModelTrainError
from ..ports.model_predictor import ModelPredictor
from ..ports.model_trainer import ModelTrainer, TrainingError
from ..ports.ohlcv_source import OhlcvSource, OhlcvSourceError
from ..ports.probability_calibrator import CalibrationError, ProbabilityCalibrator


class EnsembleTrainingError(RuntimeError):
    """Raised when ensemble training fails."""


@dataclass
class _WindowResult:
    window_idx: int
    start: int
    lstm_metrics: dict
    xgb_metrics: dict
    oof_lstm: np.ndarray
    oof_xgb: np.ndarray
    oof_lstm_probs: np.ndarray
    oof_xgb_probs: np.ndarray
    oof_targets: np.ndarray
    meta_features: np.ndarray
    train_size: int
    val_size: int
    norm_means: np.ndarray | None = None
    norm_stds: np.ndarray | None = None


@dataclass(frozen=True)
class EnsembleTrainingResult:
    lstm_version: str
    xgb_version: str
    meta_version: str | None = None
    calibrator_version: str | None = None
    n_windows: int = 0
    windows_passed: int = 0
    avg_val_accuracy: float = 0.0
    avg_val_loss: float = 0.0
    oof_size: int = 0
    n_features: int = 0
    meta_train_accuracy: float | None = None
    feature_means: tuple[float, ...] = ()
    feature_stds: tuple[float, ...] = ()
    trained_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class EnsembleTrainingUseCase:
    """Entrena el ensemble completo con Walk Forward + OOF + MetaModel.

    Uso:
        uc = EnsembleTrainingUseCase(ohlcv_source, preprocessor, analyzer,
                                     lstm_trainer, xgb_trainer, repo)
        result = await uc.execute(symbol="BTC/USDT")
    """

    N_WINDOWS = 5
    WINDOW_TRAIN_PCT = 0.7
    WINDOW_VAL_PCT = 0.15

    def __init__(
        self,
        ohlcv_source: OhlcvSource,
        preprocessor: DataPreprocessor,
        analyzer: FeatureAnalyzer,
        lstm_trainer: ModelTrainer,
        xgb_trainer: ModelTrainer,
        lstm_predictor: ModelPredictor,
        xgb_predictor: ModelPredictor,
        meta_model: MetaModel,
        calibrator: ProbabilityCalibrator,
        repo: CheckpointRepository,
    ) -> None:
        self._ohlcv = ohlcv_source
        self._preprocessor = preprocessor
        self._analyzer = analyzer
        self._lstm = lstm_trainer
        self._xgb = xgb_trainer
        self._lstm_predict = lstm_predictor
        self._xgb_predict = xgb_predictor
        self._meta_model = meta_model
        self._calibrator = calibrator
        self._repo = repo

    async def execute(
        self,
        symbol: str,
        config: ModelConfig | None = None,
        timeframe: str = "1h",
        limit: int = 10000,
    ) -> EnsembleTrainingResult:
        cfg = config or ModelConfig()

        ohlcv = await self._fetch_data(symbol, timeframe, limit, cfg)
        features_raw, targets = await self._preprocess(ohlcv, cfg)  # raw, unnormalized
        windows = await self._create_windows(features_raw, cfg)

        aligned_targets = targets[cfg.lookback - 1:]
        aligned_targets = aligned_targets[:len(windows)]

        feature_indices = {name: i for i, name in enumerate(cfg.features)}

        window_results = await self._walk_forward(
            windows, aligned_targets, cfg, features_raw, feature_indices,
        )

        oof_lstm, oof_xgb, oof_targets_combined, meta_features = self._build_oof(
            window_results
        )

        meta_version, meta_metrics = await self._train_meta(
            meta_features, oof_targets_combined, cfg
        )

        calibrator_version = await self._train_calibrator(
            meta_features, oof_targets_combined, cfg
        )

        # Retrain champion on full dataset with single scaler
        lstm_version, xgb_version, full_means, full_stds = await self._retrain_champion(
            features_raw, targets, cfg
        )

        return EnsembleTrainingResult(
            lstm_version=lstm_version,
            xgb_version=xgb_version,
            meta_version=meta_version,
            calibrator_version=calibrator_version,
            n_windows=len(window_results),
            windows_passed=len(window_results),
            avg_val_accuracy=float(np.mean([r.lstm_metrics.get("val_accuracy", 0) for r in window_results])),
            avg_val_loss=float(np.mean([r.lstm_metrics.get("val_loss", 0) for r in window_results])),
            oof_size=len(oof_targets_combined),
            n_features=features_raw.shape[1],
            meta_train_accuracy=meta_metrics.get("train_accuracy"),
            feature_means=tuple(float(m) for m in full_means),
            feature_stds=tuple(float(s) for s in full_stds),
        )

    async def _fetch_data(
        self, symbol: str, timeframe: str, limit: int, cfg: ModelConfig
    ) -> list[dict]:
        try:
            ohlcv = await self._ohlcv.fetch_ohlcv(
                symbol=symbol, timeframe=timeframe, limit=limit
            )
        except OhlcvSourceError as e:
            raise EnsembleTrainingError(f"ohlcv_fetch_failed: {e}") from e

        min_needed = cfg.lookback + cfg.target_lookahead + 10 + self.N_WINDOWS * 200
        if len(ohlcv) < min_needed:
            raise EnsembleTrainingError(
                f"insufficient_data: got {len(ohlcv)}, need at least {min_needed}"
            )
        return ohlcv

    async def _preprocess(
        self, ohlcv: list[dict], cfg: ModelConfig
    ) -> tuple[np.ndarray, np.ndarray]:
        """Build raw features and targets (NO global normalisation)."""
        try:
            features_raw = await self._preprocessor.build_features(ohlcv, cfg)
            targets = await self._preprocessor.create_targets(ohlcv, cfg)
        except PreprocessingError as e:
            raise EnsembleTrainingError(f"preprocessing_failed: {e}") from e

        min_len = min(len(features_raw), len(targets))
        features_raw = features_raw[-min_len:]
        targets = targets[-min_len:]

        try:
            correlations = await self._analyzer.compute_correlations(
                features_raw, targets, cfg.features
            )
            features_filtered, _feature_names = await self._analyzer.filter_features(
                features_raw, targets, cfg.features, min_correlation=0.05
            )
        except AnalysisError as e:
            raise EnsembleTrainingError(f"feature_analysis_failed: {e}") from e

        return features_filtered, targets

    async def _create_windows(
        self, features_norm: np.ndarray, cfg: ModelConfig
    ) -> np.ndarray:
        try:
            windows = await self._preprocessor.create_windows(features_norm, cfg.lookback)
        except InsufficientDataError as e:
            raise EnsembleTrainingError(f"windowing_failed: {e}") from e
        return windows

    async def _walk_forward(
        self, windows: np.ndarray, targets: np.ndarray, cfg: ModelConfig,
        features_raw: np.ndarray, feat_idx: dict[str, int],
    ) -> list[_WindowResult]:
        n = len(windows)
        window_size = n // self.N_WINDOWS
        results: list[_WindowResult] = []

        for w in range(self.N_WINDOWS):
            start = w * window_size
            end = n if w == self.N_WINDOWS - 1 else (w + 1) * window_size

            w_data_raw = windows[start:end]
            w_targets = targets[start:end]

            n_total = len(w_data_raw)
            n_val = int(n_total * self.WINDOW_VAL_PCT)
            n_train = n_total - n_val

            # Split BEFORE normalisation
            x_train_raw, y_train = w_data_raw[:n_train], w_targets[:n_train]
            x_val_raw, y_val = w_data_raw[n_train:], w_targets[n_train:]

            if len(x_train_raw) < cfg.lookback + 10:
                continue

            # Per-window z-score: compute from train ONLY
            w_means = np.mean(x_train_raw, axis=(0, 1))
            w_stds = np.std(x_train_raw, axis=(0, 1)) + 1e-6

            x_train = (x_train_raw - w_means) / w_stds
            x_val = (x_val_raw - w_means) / w_stds

            try:
                lstm_metrics = await self._lstm.train(cfg, x_train, y_train, x_val, y_val)
            except TrainingError as e:
                raise EnsembleTrainingError(
                    f"lstm_training_failed_window_{w}: {e}"
                ) from e

            try:
                xgb_metrics = await self._xgb.train(cfg, x_train, y_train, x_val, y_val)
            except TrainingError as e:
                raise EnsembleTrainingError(
                    f"xgb_training_failed_window_{w}: {e}"
                ) from e

            lstm_probs_list = []
            for i in range(len(x_val)):
                signal = await self._lstm_predict.predict(x_val[i])
                lstm_probs_list.append(_signal_to_probs(signal))
            xgb_probs_list = []
            for i in range(len(x_val)):
                signal = await self._xgb_predict.predict(x_val[i])
                xgb_probs_list.append(_signal_to_probs(signal))

            # Meta features: OOF + market context at correct global_idx
            meta_features_list = []
            for i in range(len(x_val)):
                global_idx = start + n_train + i
                mf = _extract_market_features_by_idx(features_raw, global_idx, feat_idx)
                mf_arr = [*lstm_probs_list[i], *xgb_probs_list[i], *mf]
                meta_features_list.append(mf_arr)

            is_last = (w == self.N_WINDOWS - 1)
            results.append(_WindowResult(
                window_idx=w,
                start=start,
                lstm_metrics=lstm_metrics,
                xgb_metrics=xgb_metrics,
                oof_lstm=x_val,
                oof_xgb=x_val,
                oof_lstm_probs=np.array(lstm_probs_list),
                oof_xgb_probs=np.array(xgb_probs_list),
                oof_targets=y_val,
                meta_features=np.array(meta_features_list),
                train_size=len(x_train),
                val_size=len(x_val),
                norm_means=w_means if is_last else None,
                norm_stds=w_stds if is_last else None,
            ))

        if not results:
            raise EnsembleTrainingError("no windows passed minimum size check")

        return results

    @staticmethod
    def _build_oof(
        window_results: list[_WindowResult],
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        oof_lstm = np.concatenate([wr.oof_lstm_probs for wr in window_results], axis=0)
        oof_xgb = np.concatenate([wr.oof_xgb_probs for wr in window_results], axis=0)
        oof_targets = np.concatenate([wr.oof_targets for wr in window_results], axis=0)
        meta_features = np.concatenate([wr.meta_features for wr in window_results], axis=0)
        return oof_lstm, oof_xgb, oof_targets, meta_features

    async def _train_meta(
        self,
        meta_features: np.ndarray,
        targets: np.ndarray,
        cfg: ModelConfig,
    ) -> tuple[str, dict]:
        try:
            metrics = await self._meta_model.train(meta_features, targets)
        except MetaModelTrainError as e:
            raise EnsembleTrainingError(f"meta_model_training_failed: {e}") from e
        version = f"meta/v1.0.{int(datetime.now(timezone.utc).timestamp())}"
        log.info("meta_model_trained", version=version, metrics=metrics)
        return version, metrics

    async def _train_calibrator(
        self,
        meta_features: np.ndarray,
        targets: np.ndarray,
        cfg: ModelConfig,
    ) -> str | None:
        try:
            meta_probs_list = []
            for i in range(len(meta_features)):
                row = meta_features[i]
                meta_input = MetaModelInput(
                    lstm_probs=np.array(row[0:3]),
                    xgb_probs=np.array(row[3:6]),
                    context={
                        "adx": float(row[6]),
                        "bbw": float(row[7]),
                        "atr": float(row[8]),
                        "rsi": float(row[9]),
                        "volume": float(row[10]),
                    },
                )
                meta_probs = await self._meta_model.predict(meta_input)
                meta_probs_list.append(meta_probs)
            meta_oof = np.array(meta_probs_list)
            labels = np.argmax(targets, axis=1)
            await self._calibrator.fit(meta_oof, labels)
        except CalibrationError as e:
            log.warning("calibrator_fit_failed", error=str(e))
            return None
        version = f"cal/v1.0.{int(datetime.now(timezone.utc).timestamp())}"
        log.info("calibrator_trained", version=version)
        return version


    async def _retrain_champion(
        self,
        features_raw: np.ndarray,
        targets: np.ndarray,
        cfg: ModelConfig,
    ) -> tuple[str, str, np.ndarray, np.ndarray]:
        """Retrain final champion models on full dataset with single scaler.

        After walk-forward OOF generation, this trains the production
        LSTM and XGBoost on ALL data with a single z-score scaler,
        eliminating train-serving skew.
        """
        full_means = np.mean(features_raw, axis=0)
        full_stds = np.std(features_raw, axis=0) + 1e-6
        features_norm = (features_raw - full_means) / full_stds

        full_windows = await self._create_windows(features_norm, cfg)
        aligned = targets[cfg.lookback - 1:]
        aligned = aligned[:len(full_windows)]

        n_total = len(full_windows)
        n_val = int(n_total * self.WINDOW_VAL_PCT)
        x_train, y_train = full_windows[:-n_val], aligned[:-n_val]
        x_val, y_val = full_windows[-n_val:], aligned[-n_val:]

        lstm_metrics = await self._lstm.train(cfg, x_train, y_train, x_val, y_val)
        xgb_metrics = await self._xgb.train(cfg, x_train, y_train, x_val, y_val)

        ts = int(datetime.now(timezone.utc).timestamp())
        lstm_version = lstm_metrics.get("version", f"lstm-champion/v1.0.{ts}")
        xgb_version = xgb_metrics.get("version", f"xgb-champion/v1.0.{ts}")

        log.info("champion_retrained", lstm_version=lstm_version, xgb_version=xgb_version)

        return lstm_version, xgb_version, full_means, full_stds


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


def _extract_market_features_by_idx(
    features_raw: np.ndarray, idx: int, feat_idx: dict[str, int],
) -> list[float]:
    """Extract market context features using dynamic indices.

    Returns [adx, bbw, atr, rsi, volume] in that order.
    BBW is computed as (bb_upper - bb_lower) / bb_middle.
    """
    n = features_raw.shape[1]
    idx_safe = min(idx, features_raw.shape[0] - 1)
    last = features_raw[idx_safe]

    bb_u = float(last[feat_idx['bb_upper']]) if feat_idx['bb_upper'] < n else 0.0
    bb_m = float(last[feat_idx['bb_middle']]) if feat_idx['bb_middle'] < n else 1.0
    bb_l = float(last[feat_idx['bb_lower']]) if feat_idx['bb_lower'] < n else 0.0
    bbw = (bb_u - bb_l) / (bb_m + 1e-10)

    adx = float(last[feat_idx['adx']]) if feat_idx['adx'] < n else 25.0
    atr = float(last[feat_idx['atr']]) if feat_idx['atr'] < n else 100.0
    rsi = float(last[feat_idx['rsi']]) if feat_idx['rsi'] < n else 50.0
    volume = float(last[feat_idx['volume']]) if feat_idx['volume'] < n else 1000.0
    return [adx, bbw, atr, rsi, volume]
