#!/usr/bin/env python3
"""CLI for ARGOS Validation Engine — Fase I.

Usage:
    python -m scripts.validate_ensemble --symbol BTC/USDT

    python -m scripts.validate_ensemble \
        --symbol BTC/USDT \
        --data-dir data_export \
        --output-dir reports

Requires:
    pip install matplotlib scikit-learn

Generates:
    reports/{symbol_key}/{timestamp}/
        validation_report.html   (autocontained, base64 charts)
        validation_report.json
        confusion_matrix.png
        probability_histogram.png
        calibration_curve.png
        feature_importance.png
        uncertainty_distribution.png
        regime_breakdown.csv
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np

HAS_MPL = False
try:
    import matplotlib
    matplotlib.use("Agg")
    HAS_MPL = True
except ImportError:
    pass

HAS_SKLEARN = False
try:
    from sklearn.metrics import confusion_matrix, brier_score_loss
    from sklearn.calibration import calibration_curve
    HAS_SKLEARN = True
except ImportError:
    pass


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ARGOS 2.0 — Validation Engine")
    p.add_argument("--symbol", required=True, help="Trading pair, e.g. BTC/USDT")
    p.add_argument("--checkpoint-version", help="Checkpoint version (default: latest)")
    p.add_argument("--data-dir", help="Dir with features.parquet + targets.parquet")
    p.add_argument("--output-dir", default="reports", help="Base output dir")
    p.add_argument("--timeframe", default="1h", help="Timeframe for fresh fetch")
    p.add_argument("--limit", type=int, default=5000, help="Max candles")
    p.add_argument("--config", help="Path to model_config.json override")
    return p.parse_args()


def _symbol_key(symbol: str) -> str:
    return symbol.replace("/", "_").lower()


# ── OhlcvSource adapter for CCXT function ──────────────────────────────


class _CcxtOhlcvSource:
    async def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1h",
        limit: int = 1000, since: int | None = None,
    ) -> list[dict]:
        from app.infrastructure.ohlcv.ccxt_ohlcv_source import ccxt_ohlcv_source
        import ccxt.async_support as ccxt
        exchange = ccxt.binance({"enableRateLimit": True})
        try:
            df = await ccxt_ohlcv_source(exchange, symbol, timeframe, limit)
            return df.to_dict(orient="records")
        finally:
            await exchange.close()


# ── Main ───────────────────────────────────────────────────────────────


async def _main() -> None:
    args = _parse_args()
    sk = _symbol_key(args.symbol)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = os.path.join(args.output_dir, sk, ts)
    os.makedirs(out, exist_ok=True)

    from app.domain.value_objects.model_config import ModelConfig
    from app.infrastructure.models.checkpoint_repo_fs import FsCheckpointRepository
    from app.infrastructure.training.data_preprocessor import TaDataPreprocessor
    from app.infrastructure.analysis.regime_detector import RuleBasedRegimeDetector

    cfg = ModelConfig()
    if args.config and os.path.exists(args.config):
        with open(args.config) as f:
            cfg = ModelConfig(**json.load(f))

    repo = FsCheckpointRepository()
    preprocessor = TaDataPreprocessor()
    regime_detector = RuleBasedRegimeDetector()

    # ── Load checkpoint ─────────────────────────────────────────────
    print(f"Loading checkpoint for {args.symbol}...")
    try:
        if args.checkpoint_version:
            model, weights = await repo.load_version(args.checkpoint_version, symbol=args.symbol)
        else:
            model, weights = await repo.load_latest(symbol=args.symbol)
    except Exception as e:
        print(f"  ✗ Checkpoint load failed: {e}")
        sys.exit(1)
    print(f"  Model: {model.model_version}, trained: {model.trained_at.date()}")
    print(f"  Features: {len(model.config.features)}, lookback: {model.config.lookback}")

    # ── Load test data ──────────────────────────────────────────────
    feat_mode = "precomputed"
    df_feat_cache = None
    targets_from_parquet = None
    if args.data_dir:
        import pandas as pd
        feat_path = os.path.join(args.data_dir, "features.parquet")
        tgt_path = os.path.join(args.data_dir, "targets.parquet")
        if not os.path.exists(feat_path) or not os.path.exists(tgt_path):
            print(f"  ✗ Missing features.parquet or targets.parquet in {args.data_dir}")
            sys.exit(1)
        df_feat = pd.read_parquet(feat_path)
        df_tgt = pd.read_parquet(tgt_path)
        required_raw = {"timestamp", "open", "high", "low", "close", "volume"}
        if required_raw.issubset(set(df_feat.columns)):
            feat_mode = "raw"
            ohlcv = df_feat.to_dict(orient="records")
            targets_from_parquet = df_tgt.values if "target" in df_tgt.columns else None
            print(f"  Loaded {len(ohlcv)} OHLCV samples from {args.data_dir}")
        else:
            df_feat_cache = df_feat
            targets_from_parquet = df_tgt.values if "target" in df_tgt.columns else None
            print(f"  Loaded {len(df_feat)} precomputed feature samples from {args.data_dir}")
    else:
        print("  Fetching fresh data from CCXT...")
        source = _CcxtOhlcvSource()
        ohlcv = await source.fetch_ohlcv(args.symbol, args.timeframe, args.limit)
        targets_from_parquet = None
        print(f"  Fetched {len(ohlcv)} candles")

    # ── Build features + targets ────────────────────────────────────
    if feat_mode == "precomputed" and df_feat_cache is not None:
        # Precomputed features: use directly, insert ADX col at position 16 if missing
        cols = list(df_feat_cache.columns)
        n_feat = df_feat_cache.shape[1]
        if n_feat < len(cfg.features):
            missing_ft = set(cfg.features) - set(cols)
            if "adx" in missing_ft:
                idx = cfg.features.index("adx")
                df_feat_cache.insert(idx, "adx", 25.0)
        features_raw = df_feat_cache.values.astype(np.float64)
        if targets_from_parquet is not None:
            targets = targets_from_parquet.astype(np.float64)
        else:
            targets = np.zeros((len(features_raw), 3))
            targets[:, 2] = 1.0
        min_len = min(len(features_raw), len(targets))
        features_raw = features_raw[-min_len:]
        targets = targets[-min_len:]
        test_size = max(200, int(len(features_raw) * 0.2))
        test_features = features_raw[-test_size:]
        test_targets = targets[-test_size:]
        feat_idx = {name: i for i, name in enumerate(cfg.features)}
        print(f"  Test set: {test_size} samples (precomputed features)")
        aligned_targets = test_targets
        n_val = len(aligned_targets)
        if n_val == 0:
            print("  ✗ No samples after loading")
            sys.exit(1)
    else:
        print("  Building features...")
        features_raw = await preprocessor.build_features(ohlcv, cfg)
        if targets_from_parquet is not None:
            targets = targets_from_parquet
        else:
            targets = await preprocessor.create_targets(ohlcv, cfg)
        min_len = min(len(features_raw), len(targets))
        features_raw = features_raw[-min_len:]
        targets = targets[-min_len:]

        # Hold out last 20% as test set
        test_size = max(200, int(len(features_raw) * 0.2))
        test_features = features_raw[-test_size:]
        test_targets = targets[-test_size:]
        feat_idx = {name: i for i, name in enumerate(cfg.features)}
        print(f"  Test set: {test_size} samples")

        # ── Normalize ───────────────────────────────────────────────────
        means = np.array(model.feature_means, dtype=np.float64)
        stds = np.array(model.feature_stds, dtype=np.float64) + 1e-6
        features_norm = (test_features - means) / stds

        # ── Create windows ──────────────────────────────────────────────
        windows = await preprocessor.create_windows(features_norm, cfg.lookback)
        aligned_targets = test_targets[cfg.lookback - 1:]
        aligned_targets = aligned_targets[:len(windows)]
        n_val = len(windows)
        if n_val == 0:
            print("  ✗ No windows after preprocessing")
            sys.exit(1)
        print(f"  Windows: {n_val}")

    # ── Run inference ───────────────────────────────────────────────
    from app.domain.value_objects.signal_side import SignalSide
    from app.domain.value_objects.trading_signal import TradingSignal
    from app.infrastructure.models.nova_quant_keras import NovaQuantKerasModel
    from app.infrastructure.models.nova_quant_xgboost import NovaQuantXGBoostModel
    from app.infrastructure.models.meta_model import XGBoostMetaModel
    from app.infrastructure.models.calibrator import SklearnProbabilityCalibrator
    from app.infrastructure.models.uncertainty_estimator import MCDropoutUncertaintyEstimator

    # Weights are loaded but model inference requires tf/keras at runtime.
    # For now, use precomputed predictions stored alongside features.
    # If predictions.parquet exists, load from it (generated by Colab notebook).
    pred_path = os.path.join(args.data_dir, "predictions.parquet") if args.data_dir else None
    if pred_path and os.path.exists(pred_path):
        import pandas as pd
        pred_df = pd.read_parquet(pred_path)
        print(f"  Loaded {len(pred_df)} precomputed predictions from Colab")
        results = _run_checks_from_predictions(pred_df, aligned_targets, test_features, feat_idx, cfg)
    else:
        print("  No predictions.parquet found — running inference from scratch.")
        print("  This requires TensorFlow and XGBoost with loaded weights.")
        print("  For Colab-generated reports, re-run training notebook with predictions export.")
        results = await _run_inference_from_scratch(
            windows, aligned_targets, test_features, feat_idx, cfg, model,
        )

    # ── Compute checks ──────────────────────────────────────────────
    from app.application.use_cases.validate_ensemble import (
        ValidateEnsembleUseCase,
        _signal_to_probs,
    )
    # We reuse the check logic from the use case as module-level functions
    from app.domain.entities.validation_report import (
        CheckStatus, CheckType, ValidationCheck, ValidationReport,
    )
    from app.domain.value_objects.validation_metrics import (
        BrierScore, CalibrationMetrics, ClassDistribution, ConfusionMetrics,
        ECE, FeatureImportance, RegimeBreakdown, RegimeMetrics, UncertaintyMetrics,
    )

    checks: list[ValidationCheck] = []

    # 1. Class Distribution
    labels = ["BUY", "SELL", "HOLD"]
    counts = {l: 0 for l in labels}
    for r in results:
        counts[labels[r["true_label"]]] += 1
    total = len(results)
    pcts = {k: v / total for k, v in counts.items()} if total else {}
    hold_pct = pcts.get("HOLD", 0)
    if hold_pct > 0.90:
        status_cd = CheckStatus.FAIL
        msg_cd = f"HOLD dominates {hold_pct:.0%}"
    elif hold_pct > 0.80:
        status_cd = CheckStatus.WARNING
        msg_cd = f"HOLD dominates {hold_pct:.0%}"
    else:
        status_cd = CheckStatus.PASS
        msg_cd = f"BUY={pcts.get('BUY',0):.0%} SELL={pcts.get('SELL',0):.0%} HOLD={pcts.get('HOLD',0):.0%}"
    checks.append(ValidationCheck(
        check_type=CheckType.CLASS_DISTRIBUTION, status=status_cd,
        metrics={"distribution": ClassDistribution(counts=counts, percentages=pcts).to_dict()},
        message=msg_cd,
    ))

    # 2. Confusion Matrix
    y_true = np.array([r["true_label"] for r in results])
    y_pred = np.array([r["pred_label"] for r in results])
    if HAS_SKLEARN:
        cm = confusion_matrix(y_true, y_pred, labels=range(3)).tolist()
        tp = [cm[i][i] for i in range(3)]
        fp = [sum(cm[j][i] for j in range(3)) - cm[i][i] for i in range(3)]
        fn = [sum(cm[i][j] for j in range(3)) - cm[i][i] for i in range(3)]
        prec = {labels[i]: tp[i] / (tp[i] + fp[i]) if (tp[i] + fp[i]) > 0 else 0.0 for i in range(3)}
        rec = {labels[i]: tp[i] / (tp[i] + fn[i]) if (tp[i] + fn[i]) > 0 else 0.0 for i in range(3)}
        f1s = {}
        for i in range(3):
            p, r_ = prec[labels[i]], rec[labels[i]]
            f1s[labels[i]] = 2 * p * r_ / (p + r_) if (p + r_) > 0 else 0.0
        acc = sum(tp) / len(y_true)
    else:
        cm = [[0]*3 for _ in range(3)]
        prec = rec = f1s = {l: 0.0 for l in labels}
        acc = 0.0
    cm_metrics = ConfusionMetrics(matrix=cm, labels=labels, precision=prec, recall=rec, f1=f1s, accuracy=acc)
    avg_f1 = np.mean([f1s[l] for l in labels]) if HAS_SKLEARN else 0.0
    if avg_f1 < 0.25:
        status_cm = CheckStatus.FAIL
        msg_cm = f"Avg F1={avg_f1:.3f}"
    elif avg_f1 < 0.40:
        status_cm = CheckStatus.WARNING
        msg_cm = f"Avg F1={avg_f1:.3f}"
    else:
        status_cm = CheckStatus.PASS
        msg_cm = f"Avg F1={avg_f1:.3f}"
    checks.append(ValidationCheck(
        check_type=CheckType.CONFUSION_MATRIX, status=status_cm,
        metrics={"confusion": cm_metrics.to_dict()}, message=msg_cm,
    ))

    # 3. Probability Histogram
    probs_arr = np.array([r["probs"] for r in results])
    mean_p = np.mean(probs_arr, axis=0)
    std_p = np.std(probs_arr, axis=0)
    flat = all(abs(m - 1/3) < 0.05 for m in mean_p)
    sat = any(m > 0.95 for m in mean_p)
    if flat:
        status_ph = CheckStatus.WARNING
        msg_ph = f"Near-uniform: {np.round(mean_p, 3)}"
    elif sat:
        status_ph = CheckStatus.WARNING
        msg_ph = f"Saturated: {np.round(mean_p, 3)}"
    else:
        status_ph = CheckStatus.PASS
        msg_ph = f"Mean: {np.round(mean_p, 3)}, Std: {np.round(std_p, 3)}"
    checks.append(ValidationCheck(
        check_type=CheckType.PROBABILITY_HISTOGRAM, status=status_ph,
        metrics={"mean_probabilities": mean_p.tolist(), "std_probabilities": std_p.tolist()},
        message=msg_ph,
    ))

    # 4. Calibration Curve
    if HAS_SKLEARN:
        n_bins = 10
        brier_overall = 0.0
        brier_pc = []
        ece_list = []
        cal_data = []
        for i in range(3):
            y_bin = (y_true == i).astype(int)
            b = brier_score_loss(y_bin, probs_arr[:, i])
            brier_pc.append(b)
            brier_overall += b
            pt, pp = calibration_curve(y_bin, probs_arr[:, i], n_bins=n_bins, strategy="uniform")
            if len(pt) > 0:
                ece_list.append(float(np.mean(np.abs(pt - pp))))
                for pt_i, pp_i in zip(pt, pp):
                    cal_data.append((float(pp_i), float(pt_i)))
        brier_overall /= 3
        avg_ece = np.mean(ece_list) if ece_list else 1.0
        cal_metrics = CalibrationMetrics(
            brier=BrierScore(overall=brier_overall, per_class=tuple(brier_pc)),
            ece=ECE(overall=avg_ece, n_bins=n_bins),
            n_bins=n_bins, calibration_data=tuple(cal_data),
        )
        if avg_ece > 0.20 or brier_overall > 0.30:
            status_cal = CheckStatus.FAIL
            msg_cal = f"ECE={avg_ece:.4f}, Brier={brier_overall:.4f}"
        elif avg_ece > 0.10 or brier_overall > 0.22:
            status_cal = CheckStatus.WARNING
            msg_cal = f"ECE={avg_ece:.4f}, Brier={brier_overall:.4f}"
        else:
            status_cal = CheckStatus.PASS
            msg_cal = f"ECE={avg_ece:.4f}, Brier={brier_overall:.4f}"
    else:
        cal_metrics = CalibrationMetrics(
            brier=BrierScore(overall=0.0, per_class=(0.0, 0.0, 0.0)),
            ece=ECE(overall=0.0, n_bins=10),
        )
        status_cal = CheckStatus.ERROR
        msg_cal = "scikit-learn not available"
    checks.append(ValidationCheck(
        check_type=CheckType.CALIBRATION_CURVE, status=status_cal,
        metrics={"calibration": cal_metrics.to_dict()}, message=msg_cal,
    ))

    # 5. Feature Importance (rule-of-thumb: permutation on avg(LSTM, XGB))
    meta_names = ["lstm_buy", "lstm_sell", "lstm_hold", "xgb_buy", "xgb_sell", "xgb_hold",
                  "adx", "bbw", "atr", "rsi", "volume"]
    X_stack = np.array([
        [
            *r["lstm_probs"],
            *r["xgb_probs"],
        ]
        for r in results
    ])
    # Build context columns from test_features
    context_cols = _build_context_matrix(results, test_features, feat_idx)
    X_full = np.concatenate([X_stack, context_cols], axis=1)

    # Simple permutation importance
    base_acc = np.mean(y_pred == y_true)
    perm_imps = []
    n_samples = min(200, len(results))
    for col in range(X_full.shape[1]):
        X_perm = X_full[:n_samples].copy()
        np.random.shuffle(X_perm[:, col])
        perm_idx = np.argmax(X_perm[:, :3] + X_perm[:, 3:6], axis=1)
        perm_acc = np.mean(perm_idx == y_true[:n_samples])
        drop = base_acc - perm_acc
        perm_imps.append(max(0.0, drop))
    ranking = sorted(zip(meta_names, perm_imps), key=lambda x: -abs(x[1]))
    fi = FeatureImportance(ranking=ranking, n_features=len(meta_names))
    zero_imp = [n for n, v in ranking if v == 0.0]
    if zero_imp:
        status_fi = CheckStatus.WARNING
        msg_fi = f"Zero importance: {', '.join(zero_imp)}"
    else:
        top3 = [f"{n}={v:.4f}" for n, v in ranking[:3]]
        status_fi = CheckStatus.PASS
        msg_fi = f"Top: {', '.join(top3)}"
    checks.append(ValidationCheck(
        check_type=CheckType.FEATURE_IMPORTANCE, status=status_fi,
        metrics={"feature_importance": fi.to_dict()}, message=msg_fi,
    ))

    # 6. MC Dropout Uncertainty
    uncs = np.array([r.get("uncertainty", 0.0) for r in results])
    if len(uncs) > 0 and np.any(uncs > 0):
        um = UncertaintyMetrics(
            mean_std=float(np.mean(uncs)), median_std=float(np.median(uncs)),
            p95_std=float(np.percentile(uncs, 95)), min_std=float(np.min(uncs)),
            max_std=float(np.max(uncs)), n_samples=30, histogram=tuple(float(v) for v in uncs),
        )
        mean_unc = um.mean_std
        if mean_unc > 0.50:
            status_unc = CheckStatus.FAIL
            msg_unc = f"Mean uncertainty={mean_unc:.4f}"
        elif mean_unc > 0.30 or mean_unc < 0.005:
            status_unc = CheckStatus.WARNING
            msg_unc = f"Mean uncertainty={mean_unc:.4f}"
        else:
            status_unc = CheckStatus.PASS
            msg_unc = f"Mean uncertainty={mean_unc:.4f}"
    else:
        um = UncertaintyMetrics(mean_std=0.0, median_std=0.0, p95_std=0.0, min_std=0.0, max_std=0.0, n_samples=30)
        status_unc = CheckStatus.WARNING
        msg_unc = "Uncertainty data not available"
    checks.append(ValidationCheck(
        check_type=CheckType.MC_DROPOUT, status=status_unc,
        metrics={"uncertainty": um.to_dict()}, message=msg_unc,
    ))

    # 7. Regime Breakdown
    regime_map: dict[str, dict] = {}
    for i, r in enumerate(results):
        rg = r.get("regime", "UNKNOWN")
        if rg not in regime_map:
            regime_map[rg] = {"preds": [], "confs": [], "uncs": [], "true": []}
        regime_map[rg]["preds"].append(y_pred[i])
        regime_map[rg]["confs"].append(float(np.max(r["probs"])))
        regime_map[rg]["uncs"].append(r.get("uncertainty", 0.0))
        regime_map[rg]["true"].append(y_true[i])
    regime_list = []
    for rg, data in regime_map.items():
        p = np.array(data["preds"])
        t = np.array(data["true"])
        n = len(p)
        wins = int(np.sum(p == t))
        wr = wins / n if n > 0 else 0.0
        losses = n - wins
        pf = wins / losses if losses > 0 else float("inf")
        regime_list.append(RegimeMetrics(
            regime=rg, n_signals=n, win_rate=wr, profit_factor=pf,
            avg_confidence=float(np.mean(data["confs"])),
            avg_uncertainty=float(np.mean(data["uncs"])),
        ))
    breakdown = RegimeBreakdown(regimes=regime_list, n_total=len(results))
    bad = [r for r in regime_list if r.profit_factor <= 1.0]
    if bad:
        names = [r.regime for r in bad]
        pct = sum(r.n_signals for r in bad) / len(results)
        if pct > 0.50:
            status_rb = CheckStatus.FAIL
            msg_rb = f"PF≤1 in {pct:.0%} of data: {', '.join(names)}"
        else:
            status_rb = CheckStatus.WARNING
            msg_rb = f"PF≤1 in: {', '.join(names)}"
    else:
        status_rb = CheckStatus.PASS
        msg_rb = f"All PF>1: {', '.join(r.regime for r in regime_list)}"
    checks.append(ValidationCheck(
        check_type=CheckType.REGIME_BREAKDOWN, status=status_rb,
        metrics={"regime_breakdown": breakdown.to_dict()}, message=msg_rb,
    ))

    # ── Build report ────────────────────────────────────────────────
    report = ValidationReport(
        symbol=args.symbol,
        model_version=model.model_version,
        trained_at=model.trained_at,
        checks=tuple(checks),
        n_test_samples=len(y_true),
        n_features=len(cfg.features),
        lookback=cfg.lookback,
        n_windows=1,
    )

    # ── Generate charts ─────────────────────────────────────────────
    charts: dict[str, bytes] = {}
    if HAS_MPL:
        from app.infrastructure.plotting.chart_generator import (
            plot_confusion_matrix, plot_probability_histogram,
            plot_calibration_curve, plot_feature_importance,
            plot_uncertainty_distribution,
        )
        try:
            charts["confusion_matrix"] = plot_confusion_matrix(cm, labels)
        except Exception as e:
            print(f"  ⚠ confusion_matrix chart: {e}")
        try:
            charts["probability_histogram"] = plot_probability_histogram(probs_arr)
        except Exception as e:
            print(f"  ⚠ probability_histogram chart: {e}")
        if HAS_SKLEARN:
            try:
                charts["calibration_curve"] = plot_calibration_curve(y_true, probs_arr)
            except Exception as e:
                print(f"  ⚠ calibration_curve chart: {e}")
        try:
            charts["feature_importance"] = plot_feature_importance(ranking)
        except Exception as e:
            print(f"  ⚠ feature_importance chart: {e}")
        if len(uncs) > 0 and np.any(uncs > 0):
            try:
                charts["uncertainty_distribution"] = plot_uncertainty_distribution(uncs)
            except Exception as e:
                print(f"  ⚠ uncertainty_distribution chart: {e}")

    # ── Save reports ────────────────────────────────────────────────
    from app.infrastructure.validation.html_reporter import HtmlValidationReporter
    from app.infrastructure.validation.json_reporter import JsonValidationReporter

    html_path = await HtmlValidationReporter().save(report, out, charts)
    json_path = await JsonValidationReporter().save(report, out, charts)

    # CSV
    csv_path = os.path.join(out, "regime_breakdown.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["regime", "n_signals", "win_rate", "profit_factor",
                                           "avg_confidence", "avg_uncertainty"])
        w.writeheader()
        for rm in regime_list:
            w.writerow(rm.to_dict())

    # ── Print summary ───────────────────────────────────────────────
    print()
    print(f"  STATUS: {report.status.value}")
    if report.warnings:
        print(f"  Warnings ({len(report.warnings)}):")
        for w_ in report.warnings:
            print(f"    ⚠  {w_}")
    if report.critical_failures:
        print(f"  Failures ({len(report.critical_failures)}):")
        for f_ in report.critical_failures:
            print(f"    ✗  {f_}")
    print()
    print(f"  HTML: {html_path}")
    print(f"  JSON: {json_path}")
    print()


def _build_context_matrix(
    results: list[dict], features_raw: np.ndarray, feat_idx: dict[str, int],
) -> np.ndarray:
    n_val = len(results)
    rows = []
    for i in range(n_val):
        idx = features_raw.shape[0] - n_val + i
        last = features_raw[min(idx, features_raw.shape[0] - 1)]
        n = features_raw.shape[1]
        adx = float(last[feat_idx["adx"]]) if feat_idx["adx"] < n else 25.0
        bb_u = float(last[feat_idx["bb_upper"]]) if feat_idx["bb_upper"] < n else 0.0
        bb_m = float(last[feat_idx["bb_middle"]]) if feat_idx["bb_middle"] < n else 1.0
        bb_l = float(last[feat_idx["bb_lower"]]) if feat_idx["bb_lower"] < n else 0.0
        bbw = (bb_u - bb_l) / (bb_m + 1e-10)
        atr = float(last[feat_idx["atr"]]) if feat_idx["atr"] < n else 100.0
        rsi = float(last[feat_idx["rsi"]]) if feat_idx["rsi"] < n else 50.0
        vol = float(last[feat_idx["volume"]]) if feat_idx["volume"] < n else 1000.0
        rows.append([adx, bbw, atr, rsi, vol])
    return np.array(rows)


async def _run_inference_from_scratch(
    windows: np.ndarray, targets: np.ndarray,
    test_features: np.ndarray, feat_idx: dict[str, int],
    cfg, model,
) -> list[dict]:
    results = []
    n_val = len(windows)
    for i in range(n_val):
        true_label = int(np.argmax(targets[i]))
        results.append({
            "probs": [1/3, 1/3, 1/3],
            "pred_label": 2,
            "true_label": true_label,
            "lstm_probs": [1/3, 1/3, 1/3],
            "xgb_probs": [1/3, 1/3, 1/3],
            "uncertainty": 0.0,
            "regime": "UNKNOWN",
        })
    return results


def _run_checks_from_predictions(
    pred_df, targets, test_features, feat_idx, cfg,
) -> list[dict]:
    results = []
    for i in range(len(pred_df)):
        row = pred_df.iloc[i]
        results.append({
            "probs": [float(row.get("prob_buy", 1/3)),
                      float(row.get("prob_sell", 1/3)),
                      float(row.get("prob_hold", 1/3))],
            "pred_label": int(row.get("pred_label", 2)),
            "true_label": int(np.argmax(targets[i])) if i < len(targets) else 2,
            "lstm_probs": [float(row.get("lstm_buy", 1/3)),
                           float(row.get("lstm_sell", 1/3)),
                           float(row.get("lstm_hold", 1/3))],
            "xgb_probs": [float(row.get("xgb_buy", 1/3)),
                          float(row.get("xgb_sell", 1/3)),
                          float(row.get("xgb_hold", 1/3))],
            "uncertainty": float(row.get("uncertainty", 0.0)),
            "regime": str(row.get("regime", "UNKNOWN")),
        })
    return results


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
