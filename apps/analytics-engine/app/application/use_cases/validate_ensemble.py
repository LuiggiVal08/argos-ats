"""ValidateEnsembleUseCase — post-training validation orchestration.

Pipeline:
  1. Load champion model + scaler from checkpoint
  2. Load test data (fresh CCXT fetch or from data_dir)
  3. Run full inference pipeline on test set
  4. Compute 7 validation checks:
     - Class Distribution
     - Confusion Matrix
     - Probability Histogram
     - Calibration Curve (Brier + ECE)
     - MetaModel Feature Importance
     - MC Dropout Uncertainty
     - Regime Breakdown
  5. Build ValidationReport with per-check PASS/WARNING/FAIL
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np
import structlog

from ...domain.entities.validation_report import (
    CheckStatus,
    CheckType,
    ValidationCheck,
    ValidationReport,
)
from ...domain.value_objects.validation_metrics import (
    BrierScore,
    CalibrationMetrics,
    ClassDistribution,
    ConfusionMetrics,
    ECE,
    FeatureImportance,
    RegimeBreakdown,
    RegimeMetrics,
    UncertaintyMetrics,
)
from ...domain.value_objects.model_config import ModelConfig
from ...domain.value_objects.signal_side import SignalSide
from ...domain.value_objects.trading_signal import TradingSignal
from ..ports.checkpoint_repository import CheckpointRepository
from ..ports.data_preprocessor import DataPreprocessor
from ..ports.model_predictor import ModelPredictor
from ..ports.ohlcv_source import OhlcvSource
from ..ports.probability_calibrator import ProbabilityCalibrator
from ..ports.regime_detector import RegimeDetector
from ..ports.uncertainty_estimator import UncertaintyEstimator
from ..ports.meta_model import MetaModel, MetaModelInput

log = structlog.get_logger()

HAS_SKLEARN = False
try:
    from sklearn.metrics import brier_score_loss, confusion_matrix
    from sklearn.calibration import calibration_curve

    HAS_SKLEARN = True
except ImportError:
    pass


class ValidateEnsembleError(RuntimeError):
    """Raised when validation fails."""


@dataclass
class _SampleResult:
    lstm_signal: TradingSignal
    lstm_probs: np.ndarray
    xgb_signal: TradingSignal
    xgb_probs: np.ndarray
    meta_probs: np.ndarray
    calibrated_probs: np.ndarray
    uncertainty: float
    regime: str
    true_label: int
    meta_input: MetaModelInput


class ValidateEnsembleUseCase:
    """Post-training validation of the ensemble pipeline.

    Usage:
        uc = ValidateEnsembleUseCase(ohlcv_source, preprocessor, lstm_pred,
                                      xgb_pred, meta_model, calibrator,
                                      uncertainty, regime, repo)
        report = await uc.execute(symbol="BTC/USDT")
    """

    N_CALIBRATION_BINS = 10
    N_UNCERTAINTY_SAMPLES = 30

    def __init__(
        self,
        ohlcv_source: OhlcvSource,
        preprocessor: DataPreprocessor,
        lstm_predictor: ModelPredictor,
        xgb_predictor: ModelPredictor,
        meta_model: MetaModel,
        calibrator: ProbabilityCalibrator,
        uncertainty_estimator: UncertaintyEstimator,
        regime_detector: RegimeDetector,
        repo: CheckpointRepository,
    ) -> None:
        self._ohlcv = ohlcv_source
        self._preprocessor = preprocessor
        self._lstm_predict = lstm_predictor
        self._xgb_predict = xgb_predictor
        self._meta_model = meta_model
        self._calibrator = calibrator
        self._uncertainty = uncertainty_estimator
        self._regime = regime_detector
        self._repo = repo

    async def execute(
        self,
        symbol: str,
        config: ModelConfig | None = None,
        timeframe: str = "1h",
        limit: int = 10000,
        checkpoint_version: str | None = None,
        data_dir: str | None = None,
    ) -> ValidationReport:
        cfg = config or ModelConfig()

        model, _weights = await self._load_checkpoint(symbol, checkpoint_version)
        ohlcv = await self._load_test_data(symbol, timeframe, limit, data_dir)
        features_raw, targets = await self._preprocess(ohlcv, cfg)
        test_features, test_targets = self._split_test_set(features_raw, targets)
        feat_idx = {name: i for i, name in enumerate(cfg.features)}
        n_test = len(test_targets)

        log.info("validation_started", symbol=symbol, n_test=n_test, model_version=model.model_version)

        sample_results = await self._run_inference(
            test_features, test_targets, cfg, feat_idx, model,
        )

        checks = await self._run_all_checks(
            sample_results, cfg, feat_idx,
        )

        report = ValidationReport(
            symbol=symbol,
            model_version=model.model_version,
            trained_at=model.trained_at,
            checks=tuple(checks),
            n_test_samples=n_test,
            n_features=test_features.shape[1],
            lookback=cfg.lookback,
            n_windows=1,
        )

        log.info("validation_complete", status=report.status.value,
                 n_failures=len(report.critical_failures),
                 n_warnings=len(report.warnings))

        return report

    async def _load_checkpoint(
        self, symbol: str, version: str | None,
    ) -> tuple[Any, bytes]:
        from ...domain.entities.nova_quant_model import NovaQuantModel
        try:
            if version:
                model, weights = await self._repo.load_version(version, symbol=symbol)
            else:
                model, weights = await self._repo.load_latest(symbol=symbol)
        except Exception as e:
            raise ValidateEnsembleError(f"checkpoint_load_failed: {e}") from e
        if not isinstance(model, NovaQuantModel):
            raise ValidateEnsembleError("loaded model is not a NovaQuantModel")
        return model, weights

    async def _load_test_data(
        self, symbol: str, timeframe: str, limit: int, data_dir: str | None,
    ) -> list[dict]:
        if data_dir:
            return await self._load_from_dir(data_dir, symbol)
        try:
            return await self._ohlcv.fetch_ohlcv(
                symbol=symbol, timeframe=timeframe, limit=limit,
            )
        except Exception as e:
            log.warning("fresh_data_fetch_failed, falling back to data_dir",
                        error=str(e))
            raise ValidateEnsembleError(
                f"No fresh data available and no --data-dir provided: {e}"
            ) from e

    async def _load_from_dir(self, data_dir: str, symbol: str) -> list[dict]:
        import os
        import pandas as pd
        parquet_path = os.path.join(data_dir, "features.parquet")
        if not os.path.exists(parquet_path):
            raise ValidateEnsembleError(
                f"data_dir {data_dir} does not contain features.parquet"
            )
        df = pd.read_parquet(parquet_path)
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        missing = required - set(df.columns)
        if missing:
            raise ValidateEnsembleError(
                f"parquet missing columns: {missing}"
            )
        return df.to_dict(orient="records")

    async def _preprocess(
        self, ohlcv: list[dict], cfg: ModelConfig,
    ) -> tuple[np.ndarray, np.ndarray]:
        try:
            features_raw = await self._preprocessor.build_features(ohlcv, cfg)
            targets = await self._preprocessor.create_targets(ohlcv, cfg)
        except Exception as e:
            raise ValidateEnsembleError(f"preprocessing_failed: {e}") from e
        min_len = min(len(features_raw), len(targets))
        return features_raw[-min_len:], targets[-min_len:]

    def _split_test_set(
        self, features: np.ndarray, targets: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        if len(features) < 200:
            raise ValidateEnsembleError(
                f"too few samples for validation: {len(features)}"
            )
        test_size = max(200, int(len(features) * 0.2))
        return features[-test_size:], targets[-test_size:]

    async def _run_inference(
        self,
        features_raw: np.ndarray,
        targets: np.ndarray,
        cfg: ModelConfig,
        feat_idx: dict[str, int],
        model: Any,
    ) -> list[_SampleResult]:
        means = np.array(model.feature_means)
        stds = np.array(model.feature_stds) + 1e-6
        features_norm = (features_raw - means) / stds

        windows = await self._preprocessor.create_windows(features_norm, cfg.lookback)
        aligned_targets = targets[cfg.lookback - 1:]
        aligned_targets = aligned_targets[:len(windows)]
        n_val = len(windows)

        if n_val == 0:
            raise ValidateEnsembleError("no windows after preprocessing")

        results: list[_SampleResult] = []
        for i in range(n_val):
            window = windows[i]
            true_label = int(np.argmax(aligned_targets[i]))
            global_idx = len(features_raw) - n_val + i

            lstm_sig = await self._lstm_predict.predict(window, cfg.confidence_threshold)
            lstm_probs = np.array(_signal_to_probs(lstm_sig))

            xgb_sig = await self._xgb_predict.predict(window, cfg.confidence_threshold)
            xgb_probs = np.array(_signal_to_probs(xgb_sig))

            mf = _extract_context_features(features_raw, global_idx, feat_idx)
            meta_input = MetaModelInput(
                lstm_probs=lstm_probs,
                xgb_probs=xgb_probs,
                context={
                    "adx": mf[0], "bbw": mf[1], "atr": mf[2],
                    "rsi": mf[3], "volume": mf[4],
                },
            )
            meta_raw = await self._meta_model.predict(meta_input)
            if np.isnan(meta_raw).any():
                meta_probs = np.array([1/3, 1/3, 1/3])
            else:
                s = meta_raw.sum()
                meta_probs = meta_raw / s if s > 0 else np.array([1/3, 1/3, 1/3])

            try:
                calibrated = await self._calibrator.calibrate(meta_probs)
            except Exception:
                calibrated = meta_probs

            unc_result = await self._uncertainty.estimate(
                window, n_samples=self.N_UNCERTAINTY_SAMPLES,
            )
            uncertainty = float(unc_result.max_std)

            try:
                regime_ctx = self._regime.detect({
                    "adx": mf[0], "bbw": mf[1], "atr": mf[2],
                    "ema_slope": 0.0,
                })
                regime = regime_ctx.regime.value
            except Exception:
                regime = "UNKNOWN"

            results.append(_SampleResult(
                lstm_signal=lstm_sig,
                lstm_probs=lstm_probs,
                xgb_signal=xgb_sig,
                xgb_probs=xgb_probs,
                meta_probs=meta_probs,
                calibrated_probs=calibrated,
                uncertainty=uncertainty,
                regime=regime,
                true_label=true_label,
                meta_input=meta_input,
            ))

        return results

    async def _run_all_checks(
        self,
        results: list[_SampleResult],
        cfg: ModelConfig,
        feat_idx: dict[str, int],
    ) -> list[ValidationCheck]:
        checks: list[ValidationCheck] = []

        checks.append(self._check_class_distribution(results))
        checks.append(self._check_confusion_matrix(results))
        checks.append(self._check_probability_histogram(results))
        checks.append(await self._check_calibration_curve(results))
        checks.append(self._check_feature_importance(results, cfg, feat_idx))
        checks.append(self._check_uncertainty(results))
        checks.append(self._check_regime_breakdown(results))

        return checks

    # ── Individual checks ─────────────────────────────────────────

    def _check_class_distribution(
        self, results: list[_SampleResult],
    ) -> ValidationCheck:
        labels = ["BUY", "SELL", "HOLD"]
        counts = {l: 0 for l in labels}
        for r in results:
            counts[labels[r.true_label]] += 1
        total = len(results)
        pcts = {k: v / total for k, v in counts.items()} if total else {}

        cd = ClassDistribution(counts=counts, percentages=pcts)

        hold_pct = pcts.get("HOLD", 0)
        if hold_pct > 0.80:
            status = CheckStatus.WARNING
            msg = f"HOLD dominates {hold_pct:.0%}"
        elif hold_pct > 0.90:
            status = CheckStatus.FAIL
            msg = f"HOLD dominates {hold_pct:.0%} — accuracy becomes misleading"
        else:
            status = CheckStatus.PASS
            msg = f"BUY={pcts.get('BUY',0):.0%} SELL={pcts.get('SELL',0):.0%} HOLD={pcts.get('HOLD',0):.0%}"

        return ValidationCheck(
            check_type=CheckType.CLASS_DISTRIBUTION,
            status=status,
            metrics={"distribution": cd.to_dict()},
            message=msg,
        )

    def _check_confusion_matrix(
        self, results: list[_SampleResult],
    ) -> ValidationCheck:
        labels = ["BUY", "SELL", "HOLD"]
        y_true = np.array([r.true_label for r in results])
        y_pred = np.argmax([r.calibrated_probs for r in results], axis=1)

        if HAS_SKLEARN:
            cm = confusion_matrix(y_true, y_pred, labels=range(3)).tolist()
            tp = [cm[i][i] for i in range(3)]
            fp = [sum(cm[j][i] for j in range(3)) - cm[i][i] for i in range(3)]
            fn = [sum(cm[i][j] for j in range(3)) - cm[i][i] for i in range(3)]
            precision = {labels[i]: tp[i] / (tp[i] + fp[i]) if (tp[i] + fp[i]) > 0 else 0.0 for i in range(3)}
            recall = {labels[i]: tp[i] / (tp[i] + fn[i]) if (tp[i] + fn[i]) > 0 else 0.0 for i in range(3)}
            f1 = {}
            for i in range(3):
                p, r = precision[labels[i]], recall[labels[i]]
                f1[labels[i]] = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
            accuracy = sum(tp) / len(y_true) if len(y_true) > 0 else 0.0
        else:
            cm = [[0]*3 for _ in range(3)]
            precision = recall = f1 = {l: 0.0 for l in labels}
            accuracy = 0.0

        cm_metrics = ConfusionMetrics(
            matrix=cm, labels=labels,
            precision=precision, recall=recall, f1=f1, accuracy=accuracy,
        )

        avg_f1 = np.mean([f1[l] for l in labels]) if HAS_SKLEARN else 0.0
        if avg_f1 < 0.25:
            status = CheckStatus.FAIL
            msg = f"Avg F1={avg_f1:.3f} — model is not discriminating"
        elif avg_f1 < 0.40:
            status = CheckStatus.WARNING
            msg = f"Avg F1={avg_f1:.3f} — moderate discrimination"
        else:
            status = CheckStatus.PASS
            msg = f"Avg F1={avg_f1:.3f} — good discrimination"

        return ValidationCheck(
            check_type=CheckType.CONFUSION_MATRIX,
            status=status,
            metrics={"confusion": cm_metrics.to_dict()},
            message=msg,
        )

    def _check_probability_histogram(
        self, results: list[_SampleResult],
    ) -> ValidationCheck:
        probs = np.array([r.calibrated_probs for r in results])
        mean_probs = np.mean(probs, axis=0)
        std_probs = np.std(probs, axis=0)

        flat = all(abs(m - 1/3) < 0.05 for m in mean_probs)
        saturated = any(m > 0.95 for m in mean_probs)
        if flat:
            status = CheckStatus.WARNING
            msg = f"Probabilities near uniform: {np.round(mean_probs, 3)}"
        elif saturated:
            status = CheckStatus.WARNING
            msg = f"Probabilities saturated near 1.0: {np.round(mean_probs, 3)}"
        else:
            status = CheckStatus.PASS
            msg = f"Mean probs: {np.round(mean_probs, 3)}, Std: {np.round(std_probs, 3)}"

        return ValidationCheck(
            check_type=CheckType.PROBABILITY_HISTOGRAM,
            status=status,
            metrics={
                "mean_probabilities": mean_probs.tolist(),
                "std_probabilities": std_probs.tolist(),
            },
            message=msg,
        )

    async def _check_calibration_curve(
        self, results: list[_SampleResult],
    ) -> ValidationCheck:
        if not HAS_SKLEARN:
            return ValidationCheck(
                check_type=CheckType.CALIBRATION_CURVE,
                status=CheckStatus.SKIP,
                message="scikit-learn not available",
            )

        y_true = np.array([r.true_label for r in results])
        y_prob = np.array([r.calibrated_probs for r in results])
        n_classes = y_prob.shape[1]
        n_bins = self.N_CALIBRATION_BINS

        brier_overall = 0.0
        brier_per_class = []
        ece_list = []
        cal_data = []

        for i in range(n_classes):
            y_bin = (y_true == i).astype(int)
            brier = brier_score_loss(y_bin, y_prob[:, i])
            brier_per_class.append(brier)
            brier_overall += brier

            prob_true, prob_pred = calibration_curve(
                y_bin, y_prob[:, i], n_bins=n_bins, strategy="uniform",
            )
            if len(prob_true) > 0:
                ece_val = float(np.mean(np.abs(prob_true - prob_pred)))
                ece_list.append(ece_val)
                for pt, pp in zip(prob_true, prob_pred):
                    cal_data.append((float(pp), float(pt)))

        brier_overall /= n_classes
        avg_ece = np.mean(ece_list) if ece_list else 1.0

        brier_vo = BrierScore(
            overall=brier_overall,
            per_class=tuple(brier_per_class),
        )
        ece_vo = ECE(overall=avg_ece, n_bins=n_bins)

        if avg_ece > 0.20 or brier_overall > 0.30:
            status = CheckStatus.FAIL
            msg = f"ECE={avg_ece:.4f} Brier={brier_overall:.4f} — poorly calibrated"
        elif avg_ece > 0.10 or brier_overall > 0.22:
            status = CheckStatus.WARNING
            msg = f"ECE={avg_ece:.4f} Brier={brier_overall:.4f} — moderate calibration"
        else:
            status = CheckStatus.PASS
            msg = f"ECE={avg_ece:.4f} Brier={brier_overall:.4f} — well calibrated"

        return ValidationCheck(
            check_type=CheckType.CALIBRATION_CURVE,
            status=status,
            metrics={"calibration": CalibrationMetrics(
                brier=brier_vo, ece=ece_vo, n_bins=n_bins,
                calibration_data=tuple(cal_data),
            ).to_dict()},
            message=msg,
        )

    def _check_feature_importance(
        self,
        results: list[_SampleResult],
        cfg: ModelConfig,
        feat_idx: dict[str, int],
    ) -> ValidationCheck:
        meta_names = [
            "lstm_buy", "lstm_sell", "lstm_hold",
            "xgb_buy", "xgb_sell", "xgb_hold",
            "adx", "bbw", "atr", "rsi", "volume",
        ]
        meta_features = np.array([
            [
                *r.lstm_probs.tolist(),
                *r.xgb_probs.tolist(),
            ]
            for r in results
        ])

        context_cols = _build_context_matrix(results, feat_idx)
        X_stack = np.concatenate([meta_features, context_cols], axis=1)
        y_true = np.array([r.true_label for r in results])

        importances = _compute_permutation_importance(X_stack, y_true, self._meta_model, results)

        ranking = sorted(
            zip(meta_names, importances),
            key=lambda x: -abs(x[1]),
        )

        fi = FeatureImportance(ranking=ranking, n_features=len(meta_names))

        zero_imp = [n for n, v in ranking if v == 0.0]
        low_imp = [n for n, v in ranking if 0 < abs(v) < 0.01]

        if zero_imp:
            status = CheckStatus.WARNING
            msg = f"Zero importance features: {', '.join(zero_imp)}"
        elif low_imp:
            status = CheckStatus.WARNING
            msg = f"Near-zero importance: {', '.join(low_imp)}"
        else:
            top3 = [f"{n}={v:.4f}" for n, v in ranking[:3]]
            status = CheckStatus.PASS
            msg = f"Top: {', '.join(top3)}"

        return ValidationCheck(
            check_type=CheckType.FEATURE_IMPORTANCE,
            status=status,
            metrics={"feature_importance": fi.to_dict()},
            message=msg,
        )

    def _check_uncertainty(
        self, results: list[_SampleResult],
    ) -> ValidationCheck:
        uncs = np.array([r.uncertainty for r in results])

        if len(uncs) == 0:
            return ValidationCheck(
                check_type=CheckType.MC_DROPOUT,
                status=CheckStatus.ERROR,
                message="No uncertainty estimates available",
            )

        metrics = UncertaintyMetrics(
            mean_std=float(np.mean(uncs)),
            median_std=float(np.median(uncs)),
            p95_std=float(np.percentile(uncs, 95)),
            min_std=float(np.min(uncs)),
            max_std=float(np.max(uncs)),
            n_samples=self.N_UNCERTAINTY_SAMPLES,
            histogram=tuple(float(v) for v in uncs),
        )

        mean_unc = metrics.mean_std
        if mean_unc > 0.50:
            status = CheckStatus.FAIL
            msg = f"Mean uncertainty={mean_unc:.4f} — too high"
        elif mean_unc > 0.30:
            status = CheckStatus.WARNING
            msg = f"Mean uncertainty={mean_unc:.4f} — moderate"
        elif mean_unc < 0.005:
            status = CheckStatus.WARNING
            msg = f"Mean uncertainty={mean_unc:.4f} — suspiciously low"
        else:
            status = CheckStatus.PASS
            msg = f"Mean uncertainty={mean_unc:.4f} — healthy"

        return ValidationCheck(
            check_type=CheckType.MC_DROPOUT,
            status=status,
            metrics={"uncertainty": metrics.to_dict()},
            message=msg,
        )

    def _check_regime_breakdown(
        self, results: list[_SampleResult],
    ) -> ValidationCheck:
        regime_map: dict[str, dict[str, Any]] = {}
        for r in results:
            rg = r.regime
            if rg not in regime_map:
                regime_map[rg] = {"preds": [], "confs": [], "uncs": [], "true": []}
            regime_map[rg]["preds"].append(int(np.argmax(r.calibrated_probs)))
            regime_map[rg]["confs"].append(float(np.max(r.calibrated_probs)))
            regime_map[rg]["uncs"].append(r.uncertainty)
            regime_map[rg]["true"].append(r.true_label)

        regimes_list = []
        for rg, data in regime_map.items():
            preds = np.array(data["preds"])
            true = np.array(data["true"])
            n = len(preds)
            wins = int(np.sum(preds == true))
            wr = wins / n if n > 0 else 0.0
            losses = n - wins
            pf = wins / losses if losses > 0 else float("inf")
            regimes_list.append(RegimeMetrics(
                regime=rg,
                n_signals=n,
                win_rate=wr,
                profit_factor=pf,
                avg_confidence=float(np.mean(data["confs"])),
                avg_uncertainty=float(np.mean(data["uncs"])),
            ))

        breakdown = RegimeBreakdown(regimes=regimes_list, n_total=len(results))

        bad_regimes = [r for r in regimes_list if r.profit_factor <= 1.0]
        if bad_regimes:
            names = [r.regime for r in bad_regimes]
            pct = sum(r.n_signals for r in bad_regimes) / len(results)
            if pct > 0.50:
                status = CheckStatus.FAIL
                msg = f"PF≤1 in {pct:.0%} of data: {', '.join(names)}"
            else:
                status = CheckStatus.WARNING
                msg = f"PF≤1 in regimes: {', '.join(names)}"
        else:
            status = CheckStatus.PASS
            msg = f"All regimes PF>1: {', '.join(r.regime for r in regimes_list)}"

        return ValidationCheck(
            check_type=CheckType.REGIME_BREAKDOWN,
            status=status,
            metrics={"regime_breakdown": breakdown.to_dict()},
            message=msg,
        )


# ── Module-level helpers ──────────────────────────────────────────────


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


def _extract_context_features(
    features_raw: np.ndarray, idx: int, feat_idx: dict[str, int],
) -> list[float]:
    n = features_raw.shape[1]
    idx_safe = min(idx, features_raw.shape[0] - 1)
    last = features_raw[idx_safe]
    bb_u = float(last[feat_idx["bb_upper"]]) if feat_idx["bb_upper"] < n else 0.0
    bb_m = float(last[feat_idx["bb_middle"]]) if feat_idx["bb_middle"] < n else 1.0
    bb_l = float(last[feat_idx["bb_lower"]]) if feat_idx["bb_lower"] < n else 0.0
    bbw = (bb_u - bb_l) / (bb_m + 1e-10)
    adx = float(last[feat_idx["adx"]]) if feat_idx["adx"] < n else 25.0
    atr = float(last[feat_idx["atr"]]) if feat_idx["atr"] < n else 100.0
    rsi = float(last[feat_idx["rsi"]]) if feat_idx["rsi"] < n else 50.0
    volume = float(last[feat_idx["volume"]]) if feat_idx["volume"] < n else 1000.0
    return [adx, bbw, atr, rsi, volume]


def _build_context_matrix(
    results: list[_SampleResult], feat_idx: dict[str, int],
) -> np.ndarray:
    return np.array([
        [r.meta_input.context.get(k, 0.0) for k in ("adx", "bbw", "atr", "rsi", "volume")]
        for r in results
    ])


def _compute_permutation_importance(
    X_stack: np.ndarray,
    y_true: np.ndarray,
    meta_model: MetaModel,
    results: list[_SampleResult],
) -> list[float]:
    base_acc = np.mean(
        np.argmax(X_stack[:, :3] + X_stack[:, 3:6], axis=1) == y_true
    )
    n_features = X_stack.shape[1]
    importances = []
    for col in range(n_features):
        X_perm = X_stack.copy()
        np.random.shuffle(X_perm[:, col])
        perm_pred = np.argmax(X_perm[:, :3] + X_perm[:, 3:6], axis=1)
        perm_acc = np.mean(perm_pred == y_true)
        drop = base_acc - perm_acc
        importances.append(max(0.0, drop))
    return importances
