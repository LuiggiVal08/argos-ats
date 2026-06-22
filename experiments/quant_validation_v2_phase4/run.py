"""QV2 Phase 4 — Cross-Regime Validation.

Protocol: Phase 3.5 (lookahead=5, stride=5, embargo=1)
7 experiments: full (control), bull, bear, sideways, high_vol, medium_vol, low_vol

Usage:
  python3 -m experiments.quant_validation_v2_phase4.run
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.linear_model import LogisticRegression, Ridge

from .common import (
    N_FOLDS,
    RANDOM_STATE,
    build_feature_matrix,
    label_3class,
    label_binary,
    label_regression,
    subsample_indices,
    walk_forward_splits_phase35,
    null_classification,
    null_persist_label,
    null_regression,
    save_report,
    compute_trend_regime,
    compute_volatility_regime,
    filter_and_validate,
    logger,
)

from experiments.quant_validation_v1.common import (
    run_walk_forward_classification,
    run_shuffle_test_classification,
    run_walk_forward_regression,
    run_shuffle_test_regression,
)


LOOKAHEAD = 5
STRIDE = 5
EMBARGO = 1
N_CLF_SHUFFLE = 10
N_REG_SHUFFLE = 5

# Phase 3.5 expected values (control)
EXPECTED_LR_F1 = 0.744
EXPECTED_LR_AUC = 0.826
EXPECTED_RIDGE_R2 = 0.308

EXPERIMENTS = [
    ("full", None),
    ("bull", "trend"),
    ("bear", "trend"),
    ("sideways", "trend"),
    ("high_vol", "vol"),
    ("medium_vol", "vol"),
    ("low_vol", "vol"),
]


# ── model factories ──────────────────────────────────────────────


def make_clf(name: str):
    if name == "LogisticRegression":
        return lambda: LogisticRegression(max_iter=1000, random_state=RANDOM_STATE, class_weight="balanced", n_jobs=1)
    elif name == "RandomForest":
        return lambda: RandomForestClassifier(n_estimators=100, max_depth=7, random_state=RANDOM_STATE, class_weight="balanced", n_jobs=1)
    elif name == "HistGradientBoosting":
        return lambda: HistGradientBoostingClassifier(max_iter=200, max_depth=5, random_state=RANDOM_STATE, class_weight="balanced")
    raise ValueError(f"Unknown classifier: {name}")


def make_reg(name: str):
    if name == "Ridge":
        return lambda: Ridge(alpha=1.0, random_state=RANDOM_STATE)
    elif name == "RandomForestRegressor":
        return lambda: RandomForestRegressor(n_estimators=50, max_depth=5, random_state=RANDOM_STATE, n_jobs=1)
    elif name == "HistGradientBoostingRegressor":
        return lambda: HistGradientBoostingRegressor(max_iter=200, max_depth=5, random_state=RANDOM_STATE)
    raise ValueError(f"Unknown regressor: {name}")


# ── PHASE 3A — multiclass ────────────────────────────────────────


def run_phase_3a(X_sub, y_sub) -> dict:
    logger.info(f"Phase 3A Multiclass (n={len(y_sub)})")
    t0 = time.time()

    nulls = {
        "always_HOLD": null_classification(y_sub, [0, 1, 2], 1, "always_HOLD"),
        "always_BUY": null_classification(y_sub, [0, 1, 2], 2, "always_BUY"),
        "always_SELL": null_classification(y_sub, [0, 1, 2], 0, "always_SELL"),
        "persist_last_label": null_persist_label(y_sub, "persist_last_label"),
    }
    for n in nulls.values():
        logger.info(f"  null {n.strategy:20s}  acc={n.accuracy:.4f}  f1={n.f1_macro:.4f}")

    splits = walk_forward_splits_phase35(len(X_sub), N_FOLDS, EMBARGO)
    model_names = ["LogisticRegression", "RandomForest", "HistGradientBoosting"]
    models_out = {}
    for model_name in model_names:
        fn = make_clf(model_name)
        logger.info(f"  training [{model_name}]...")
        result = run_walk_forward_classification(X_sub, y_sub, splits, fn, labels=[0, 1, 2])
        result.model_name = model_name
        logger.info(f"  shuffle test [{model_name}] ({N_CLF_SHUFFLE} seeds)...")
        sh_avg, sh_std = run_shuffle_test_classification(
            X_sub, y_sub, splits, fn, list(range(42, 42 + N_CLF_SHUFFLE)),
            labels=[0, 1, 2],
        )
        result.shuffle_f1_avg = sh_avg
        result.shuffle_f1_std = sh_std
        result.shuffle_delta = result.avg_f1 - sh_avg
        logger.info(f"    f1={result.avg_f1:.4f}  shuffled={sh_avg:.4f}  delta={result.shuffle_delta:+.4f}")
        models_out[model_name] = asdict(result)

    best_model = max(models_out, key=lambda k: models_out[k]["avg_f1"])
    best_f1 = models_out[best_model]["avg_f1"]
    best_null_f1 = max(n.f1_macro for n in nulls.values())
    logger.info(f"  → best: {best_model} f1={best_f1:.4f}  null={best_null_f1:.4f}  ({time.time()-t0:.0f}s)")

    return {
        "phase": "3A_multiclass",
        "n_samples": len(y_sub),
        "class_distribution": {int(k): int(v) for k, v in zip(*np.unique(y_sub, return_counts=True))},
        "null_baselines": {k: asdict(v) for k, v in nulls.items()},
        "models": models_out,
        "best_model": best_model,
        "best_f1": best_f1,
        "best_null_f1": best_null_f1,
        "beats_null": bool(best_f1 > best_null_f1 * 1.01),
        "best_shuffle_delta": round(max(m["shuffle_delta"] for m in models_out.values()), 4),
    }


# ── PHASE 3B — binary ────────────────────────────────────────────


def run_phase_3b(X_sub, y_full_sub, mask_sub) -> dict:
    X_bin = X_sub[mask_sub]
    y_bin = y_full_sub[mask_sub]
    logger.info(f"Phase 3B Binary (n={len(y_bin)})")
    t0 = time.time()

    unique, counts = np.unique(y_bin, return_counts=True)
    for k, v in zip(unique, counts):
        lbl = "BUY" if k == 1 else "SELL"
        logger.info(f"  {lbl}: {v} ({100*v/len(y_bin):.1f}%)")

    nulls = {
        "always_SELL": null_classification(y_bin, [0, 1], 0, "always_SELL"),
        "always_BUY": null_classification(y_bin, [0, 1], 1, "always_BUY"),
        "persist_last_label": null_persist_label(y_bin, "persist_last_label"),
    }
    for n in nulls.values():
        logger.info(f"  null {n.strategy:20s}  acc={n.accuracy:.4f}  f1={n.f1_macro:.4f}")

    splits = walk_forward_splits_phase35(len(X_bin), N_FOLDS, EMBARGO)
    model_names = ["LogisticRegression", "RandomForest", "HistGradientBoosting"]
    models_out = {}
    for model_name in model_names:
        fn = make_clf(model_name)
        logger.info(f"  training [{model_name}]...")
        result = run_walk_forward_classification(X_bin, y_bin, splits, fn, labels=[0, 1])
        result.model_name = model_name
        logger.info(f"  shuffle test [{model_name}] ({N_CLF_SHUFFLE} seeds)...")
        sh_avg, sh_std = run_shuffle_test_classification(
            X_bin, y_bin, splits, fn, list(range(42, 42 + N_CLF_SHUFFLE)),
            labels=[0, 1],
        )
        result.shuffle_f1_avg = sh_avg
        result.shuffle_f1_std = sh_std
        result.shuffle_delta = result.avg_f1 - sh_avg
        logger.info(f"    f1={result.avg_f1:.4f}  auc={result.avg_auc}  delta={result.shuffle_delta:+.4f}")
        models_out[model_name] = asdict(result)

    best_model = max(models_out, key=lambda k: models_out[k]["avg_f1"])
    best_f1 = models_out[best_model]["avg_f1"]
    best_auc = max((m["avg_auc"] for m in models_out.values()), default=None)
    best_null_f1 = max(n.f1_macro for n in nulls.values())
    logger.info(f"  → best: {best_model} f1={best_f1:.4f}  auc={best_auc}  null={best_null_f1:.4f}  ({time.time()-t0:.0f}s)")

    return {
        "phase": "3B_binary",
        "n_samples_original": len(y_full_sub),
        "n_samples_nonhold": len(y_bin),
        "class_distribution": {int(k): int(v) for k, v in zip(unique, counts)},
        "null_baselines": {k: asdict(v) for k, v in nulls.items()},
        "models": models_out,
        "best_model": best_model,
        "best_f1": best_f1,
        "best_null_f1": best_null_f1,
        "best_auc": best_auc,
        "beats_null": bool(best_f1 > best_null_f1 * 1.01),
        "best_shuffle_delta": round(max(m["shuffle_delta"] for m in models_out.values()), 4),
    }


# ── PHASE 3C — regression ────────────────────────────────────────


def run_phase_3c(X_sub, y_reg_sub) -> dict:
    valid_mask = ~np.isnan(y_reg_sub)
    X_reg = X_sub[valid_mask]
    y_reg = y_reg_sub[valid_mask]
    logger.info(f"Phase 3C Regression (n={len(y_reg)})")
    t0 = time.time()

    nulls = {
        "predict_zero": null_regression(y_reg, "predict_zero"),
        "predict_mean": null_regression(y_reg, "predict_mean"),
        "persist_last_value": null_regression(y_reg, "persist_last_value"),
    }
    for n in nulls.values():
        logger.info(f"  null {n.strategy:20s}  r2={n.r2:.4f}  dir_acc={n.dir_acc:.4f}")

    splits = walk_forward_splits_phase35(len(X_reg), N_FOLDS, EMBARGO)
    model_names = ["Ridge", "RandomForestRegressor", "HistGradientBoostingRegressor"]
    models_out = {}
    for model_name in model_names:
        fn = make_reg(model_name)
        logger.info(f"  training [{model_name}]...")
        result = run_walk_forward_regression(X_reg, y_reg, splits, fn)
        result.model_name = model_name
        logger.info(f"  shuffle test [{model_name}] ({N_REG_SHUFFLE} seeds)...")
        sh_avg, sh_std = run_shuffle_test_regression(
            X_reg, y_reg, splits, fn, list(range(42, 42 + N_REG_SHUFFLE)),
        )
        result.shuffle_r2_avg = sh_avg
        result.shuffle_r2_std = sh_std
        result.shuffle_r2_delta = result.avg_r2 - sh_avg
        logger.info(f"    r2={result.avg_r2:.4f}  dir_acc={result.avg_dir_acc:.4f}  shuffle_delta={result.shuffle_r2_delta:+.4f}")
        models_out[model_name] = asdict(result)

    best_model_r2 = max(models_out, key=lambda k: models_out[k]["avg_r2"])
    best_r2 = models_out[best_model_r2]["avg_r2"]
    best_null_r2 = max(n.r2 for n in nulls.values())
    logger.info(f"  → best: {best_model_r2} r2={best_r2:.4f}  null={best_null_r2:.4f}  ({time.time()-t0:.0f}s)")

    return {
        "phase": "3C_regression",
        "n_samples": len(y_reg),
        "target_stats": {"mean": float(np.mean(y_reg)), "std": float(np.std(y_reg))},
        "null_baselines": {k: asdict(v) for k, v in nulls.items()},
        "models": models_out,
        "best_model": best_model_r2,
        "best_r2": round(best_r2, 4),
        "best_null_r2": round(best_null_r2, 4),
        "beats_null_r2": bool(best_r2 > best_null_r2 + 0.001),
        "best_shuffle_delta": round(max(m["shuffle_r2_delta"] for m in models_out.values()), 4),
    }


# ── run single experiment ────────────────────────────────────────


def run_experiment(
    exp_name: str,
    X: np.ndarray,
    X_sub: np.ndarray,
    ohlcv: pd.DataFrame,
    indices: np.ndarray,
) -> dict:
    logger.info(f"{'=' * 70}")
    logger.info(f"  Experiment: {exp_name}")
    logger.info(f"  Subsamples: {len(indices)}")
    logger.info(f"{'=' * 70}")
    t0 = time.time()

    y_3a = label_3class(ohlcv, lookahead=LOOKAHEAD, threshold=0.5)
    y_3a_sub = y_3a[indices]
    y_bin, mask_bin = label_binary(ohlcv, lookahead=LOOKAHEAD, threshold=0.5)
    y_bin_sub, mask_sub = y_bin[indices], mask_bin[indices]
    y_reg = label_regression(ohlcv, lookahead=LOOKAHEAD)
    y_reg_sub = y_reg[indices]

    phases = {
        "3A_multiclass": run_phase_3a(X_sub, y_3a_sub),
        "3B_binary": run_phase_3b(X_sub, y_bin_sub, mask_sub),
        "3C_regression": run_phase_3c(X_sub, y_reg_sub),
    }

    result = {
        "experiment": exp_name,
        "symbol": "BTC/USDT",
        "exchange": "binance",
        "lookahead": LOOKAHEAD,
        "stride": STRIDE,
        "embargo": EMBARGO,
        "n_samples_original": len(X),
        "n_samples_subsampled": len(indices),
        "feature_count": X_sub.shape[1],
        "phases": phases,
        "elapsed_seconds": round(time.time() - t0, 1),
    }

    save_report(result, f"{exp_name}.json")
    logger.info(f"  ✓ {exp_name} complete ({result['elapsed_seconds']:.0f}s)")
    return result


# ── BTC+Binance control check ──────────────────────────────────


def check_control(result: dict) -> bool:
    p3b = result["phases"]["3B_binary"]
    p3c = result["phases"]["3C_regression"]

    models_3b = p3b.get("models", {})
    models_3c = p3c.get("models", {})
    lr = models_3b.get("LogisticRegression", {})
    ridge = models_3c.get("Ridge", {})

    f1 = lr.get("avg_f1", 0)
    auc = lr.get("avg_auc", 0)
    r2 = ridge.get("avg_r2", 0)

    f1_dev = abs(f1 - EXPECTED_LR_F1) / max(EXPECTED_LR_F1, 0.01)
    r2_dev = abs(r2 - EXPECTED_RIDGE_R2) / max(EXPECTED_RIDGE_R2, 0.01)

    logger.info(f"{'─' * 60}")
    logger.info("CONTROL CHECK (full = BTC Binance, Phase 3.5 protocol)")
    logger.info(f"{'─' * 60}")
    logger.info(f"  Expected: LR f1={EXPECTED_LR_F1}, AUC={EXPECTED_LR_AUC}, Ridge R²={EXPECTED_RIDGE_R2}")
    logger.info(f"  Got:      LR f1={f1:.4f}, AUC={auc:.4f}, Ridge R²={r2:.4f}")
    logger.info(f"  Dev:      F1={f1_dev*100:.1f}%  R²={r2_dev*100:.1f}%")

    if f1_dev < 0.05 and r2_dev < 0.05:
        logger.info(f"\n  ✅ CONTROL PASS — proceeding to regime experiments")
        return True
    else:
        logger.error(f"\n  ❌ CONTROL FAIL — deviation exceeds 5% tolerance")
        logger.error(f"  Phase 4 invalidated. Aborting.")
        return False


# ── queued result type ───────────────────────────────────────────


@dataclass
class RegimeResult:
    status: str  # "ok" | "insufficient_data" | "error"
    info: dict | None
    result: dict | None


# ── main ─────────────────────────────────────────────────────────


def main():
    t0 = time.time()
    logger.info("=" * 70)
    logger.info("QV2 Phase 4 — Cross-Regime Validation")
    logger.info("Protocol: Phase 3.5 (lookahead=5, stride=5, embargo=1)")
    logger.info("Experiments: full (control) + 6 regimes = 7")
    logger.info("=" * 70)

    (X, _, feature_names, _), ohlcv = build_feature_matrix()
    logger.info(f"Feature matrix: {X.shape}, {len(feature_names)} features")

    # Compute regimes on full OHLCV (raw close)
    close = ohlcv["close"]
    trend_arr = compute_trend_regime(close)
    vol_arr = compute_volatility_regime(close)

    # Subsample indices (Phase 3.5 protocol)
    idx_full = subsample_indices(len(X), LOOKAHEAD, STRIDE)
    logger.info(f"Subsampled indices: {len(idx_full)} ← {len(X)} original")

    results: dict[str, RegimeResult] = {}

    for i, (exp_name, regime_type) in enumerate(EXPERIMENTS):
        exp_label = f"[{i+1}/{len(EXPERIMENTS)}] {exp_name}"
        logger.info(f"\n{'#' * 70}")
        logger.info(f"# {exp_label}")
        logger.info(f"{'#' * 70}")

        try:
            if exp_name == "full":
                idx_exp = idx_full
                regime_info = {"n_total_filtered": len(idx_exp)}
            else:
                regime_arr = trend_arr if regime_type == "trend" else vol_arr
                idx_exp, regime_info = filter_and_validate(
                    idx_full, regime_arr, exp_name,
                    lookahead=LOOKAHEAD, stride=STRIDE, n_folds=N_FOLDS,
                )
                if idx_exp is None:
                    logger.warning(f"[{exp_name}] insufficient data — skipping")
                    save_report({
                        "experiment": exp_name,
                        "status": "insufficient_data",
                        "info": regime_info,
                    }, f"{exp_name}.json")
                    results[exp_name] = RegimeResult("insufficient_data", regime_info, None)
                    continue

            X_sub_exp = X[idx_exp]
            exp_result = run_experiment(exp_name, X, X_sub_exp, ohlcv, idx_exp)
            results[exp_name] = RegimeResult("ok", regime_info, exp_result)

            # Control check after "full" completes
            if exp_name == "full":
                if not check_control(exp_result):
                    _save_master(results, t0, aborted=True)
                    return

        except Exception as e:
            logger.error(f"[{exp_name}] error: {e}", exc_info=True)
            save_report({
                "experiment": exp_name,
                "status": "error",
                "error": str(e),
            }, f"{exp_name}.json")
            results[exp_name] = RegimeResult("error", None, None)
            continue

    _save_master(results, t0)

    total = time.time() - t0
    logger.info(f"\n{'=' * 70}")
    logger.info(f"ALL EXPERIMENTS COMPLETE: {total:.0f}s ({total/60:.1f} min)")
    logger.info("=" * 70)

    # Summary table
    logger.info(f"\n{'Experiment':15s}  {'Status':18s}  {'F1':>6s}  {'AUC':>6s}  {'R²':>7s}  {'Δbin':>7s}  {'Δreg':>7s}")
    logger.info("-" * 75)
    for exp_name, _ in EXPERIMENTS:
        r = results.get(exp_name)
        if r is None or r.result is None:
            label = "insufficient" if r and r.status == "insufficient_data" else "error"
            logger.info(f"{exp_name:15s}  {label:18s}  {'—':>6s}  {'—':>6s}  {'—':>7s}  {'—':>7s}  {'—':>7s}")
            continue
        p3b = r.result.get("phases", {}).get("3B_binary", {})
        p3c = r.result.get("phases", {}).get("3C_regression", {})
        f1 = p3b.get("best_f1", 0)
        auc = p3b.get("best_auc", 0) or 0
        r2 = p3c.get("best_r2", 0)
        sd_bin = p3b.get("best_shuffle_delta", 0)
        sd_reg = p3c.get("best_shuffle_delta", 0)
        logger.info(f"{exp_name:15s}  {'ok':18s}  {f1:6.4f}  {auc:6.4f}  {r2:7.4f}  {sd_bin:7.4f}  {sd_reg:7.4f}")

    logger.info(f"\n  Run summary.py for formal verdict.")


def _save_master(results: dict, start_time: float, aborted: bool = False):
    master = {
        "experiment": "QV2_Phase4",
        "protocol": {"lookahead": LOOKAHEAD, "stride": STRIDE, "embargo": EMBARGO},
        "aborted": aborted,
        "elapsed_seconds": round(time.time() - start_time, 1),
        "experiments": {k: (
            {"status": v.status, "experiment": v.result.get("experiment", k) if v.result else k}
            if v.result else {"status": v.status}
        ) for k, v in results.items()},
    }
    save_report(master, "phase4_master.json")


if __name__ == "__main__":
    main()
