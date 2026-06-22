"""QV2 — Run all 3 framings (multiclass, binary, regression).

Identical protocol to QV1:
  - Same models, seeds, walk-forward folds, metrics, null baselines
  - Feature space: 53 features (20 base TA + 30 MTF 4h/1d + 3 funding)

Usage:
  cd /home/egraterol/projects/argos-ats
  python3 -m experiments.quant_validation_v2.run
"""

from __future__ import annotations

import time
from dataclasses import asdict

import numpy as np
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.linear_model import LogisticRegression, Ridge

from .common import (
    BASE_FEATURES,
    FUNDING_FEATURES,
    N_FOLDS,
    RANDOM_STATE,
    REPORT_DIR,
    SHUFFLE_SEEDS,
    SYMBOL,
    TIMEFRAME,
    load_ohlcv,
    load_funding,
    build_feature_matrix,
    label_3class,
    label_binary,
    label_regression,
    save_report,
)

from experiments.quant_validation_v1.common import (
    walk_forward_splits,
    run_walk_forward_classification,
    run_shuffle_test_classification,
    run_walk_forward_regression,
    run_shuffle_test_regression,
    null_classification,
    null_persist_label,
    null_regression,
)


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


def run_phase_3a(X, y, feature_names) -> dict:
    print("\n" + "=" * 60)
    print("Phase 3A — Multiclass (BUY/HOLD/SELL)")
    print("=" * 60)

    nulls = {
        "always_HOLD": null_classification(y, [0, 1, 2], 1, "always_HOLD"),
        "always_BUY": null_classification(y, [0, 1, 2], 2, "always_BUY"),
        "always_SELL": null_classification(y, [0, 1, 2], 0, "always_SELL"),
        "persist_last_label": null_persist_label(y, "persist_last_label"),
    }
    for n in nulls.values():
        print(f"  {n.strategy:20s}  acc={n.accuracy:.4f}  f1={n.f1_macro:.4f}")

    splits = walk_forward_splits(len(y), N_FOLDS)
    model_names = ["LogisticRegression", "RandomForest", "HistGradientBoosting"]
    models_out = {}
    for name in model_names:
        fn = make_clf(name)
        print(f"\n  [{name}]")
        result = run_walk_forward_classification(X, y, splits, fn, labels=[0, 1, 2])
        result.model_name = name
        sh_avg, sh_std = run_shuffle_test_classification(X, y, splits, fn, SHUFFLE_SEEDS, labels=[0, 1, 2])
        result.shuffle_f1_avg = sh_avg
        result.shuffle_f1_std = sh_std
        result.shuffle_delta = result.avg_f1 - sh_avg
        print(f"    f1={result.avg_f1:.4f}  kappa={result.avg_kappa:.4f}  mcc={result.avg_mcc:.4f}")
        print(f"    shuffled f1: {sh_avg:.4f} ± {sh_std:.4f}  delta={result.shuffle_delta:+.4f}")
        models_out[name] = asdict(result)

    best_model = max(models_out, key=lambda k: models_out[k]["avg_f1"])
    best_f1 = models_out[best_model]["avg_f1"]
    best_null_f1 = max(n.f1_macro for n in nulls.values())
    best_shuffle_delta = max(m["shuffle_delta"] for m in models_out.values())
    print(f"\n  Best model: {best_model} (f1={best_f1:.4f}) vs best null (f1={best_null_f1:.4f})")
    print(f"  Best shuffle delta: {best_shuffle_delta:+.4f}")
    print(f"  Beats null: {best_f1 > best_null_f1 * 1.01}")

    return {
        "phase": "3A_multiclass",
        "feature_count": len(feature_names),
        "class_distribution": {int(k): int(v) for k, v in zip(*np.unique(y, return_counts=True))},
        "null_baselines": {k: asdict(v) for k, v in nulls.items()},
        "models": models_out,
        "best_model": best_model,
        "best_f1": best_f1,
        "best_null_f1": best_null_f1,
        "beats_null": bool(best_f1 > best_null_f1 * 1.01),
        "best_shuffle_delta": round(best_shuffle_delta, 4),
    }


def run_phase_3b(X, y_full, valid_mask, feature_names) -> dict:
    print("\n" + "=" * 60)
    print("Phase 3B — Binary (BUY vs SELL, no HOLD)")
    print("=" * 60)

    X_bin = X[valid_mask]
    y_bin = y_full[valid_mask]
    unique, counts = np.unique(y_bin, return_counts=True)
    for k, v in zip(unique, counts):
        label = "BUY" if k == 1 else "SELL"
        print(f"  {label}: {v} ({100*v/len(y_bin):.1f}%)")

    nulls = {
        "always_SELL": null_classification(y_bin, [0, 1], 0, "always_SELL"),
        "always_BUY": null_classification(y_bin, [0, 1], 1, "always_BUY"),
        "persist_last_label": null_persist_label(y_bin, "persist_last_label"),
    }
    for n in nulls.values():
        print(f"  {n.strategy:20s}  acc={n.accuracy:.4f}  f1={n.f1_macro:.4f}")

    splits = walk_forward_splits(len(y_bin), N_FOLDS)
    model_names = ["LogisticRegression", "RandomForest", "HistGradientBoosting"]
    models_out = {}
    for name in model_names:
        fn = make_clf(name)
        print(f"\n  [{name}]")
        result = run_walk_forward_classification(X_bin, y_bin, splits, fn, labels=[0, 1])
        result.model_name = name
        sh_avg, sh_std = run_shuffle_test_classification(X_bin, y_bin, splits, fn, SHUFFLE_SEEDS, labels=[0, 1])
        result.shuffle_f1_avg = sh_avg
        result.shuffle_f1_std = sh_std
        result.shuffle_delta = result.avg_f1 - sh_avg
        print(f"    f1={result.avg_f1:.4f}  kappa={result.avg_kappa:.4f}  auc={result.avg_auc}")
        print(f"    shuffled f1: {sh_avg:.4f} ± {sh_std:.4f}  delta={result.shuffle_delta:+.4f}")
        models_out[name] = asdict(result)

    best_model = max(models_out, key=lambda k: models_out[k]["avg_f1"])
    best_f1 = models_out[best_model]["avg_f1"]
    best_null_f1 = max(n.f1_macro for n in nulls.values())
    best_shuffle_delta = max(m["shuffle_delta"] for m in models_out.values())
    print(f"\n  Best model: {best_model} (f1={best_f1:.4f}) vs best null (f1={best_null_f1:.4f})")
    print(f"  Beats null: {best_f1 > best_null_f1 * 1.01}")

    return {
        "phase": "3B_binary",
        "feature_count": len(feature_names),
        "n_samples_original": len(y_full),
        "n_samples_nonhold": len(y_bin),
        "class_distribution": {int(k): int(v) for k, v in zip(unique, counts)},
        "null_baselines": {k: asdict(v) for k, v in nulls.items()},
        "models": models_out,
        "best_model": best_model,
        "best_f1": best_f1,
        "best_null_f1": best_null_f1,
        "beats_null": bool(best_f1 > best_null_f1 * 1.01),
        "best_shuffle_delta": round(best_shuffle_delta, 4),
    }


def run_phase_3c(X, df, feature_names) -> dict:
    print("\n" + "=" * 60)
    print("Phase 3C — Regression (vol-adj returns)")
    print("=" * 60)

    y = label_regression(df, lookahead=5)
    valid_mask = ~np.isnan(y)
    X_reg = X[valid_mask]
    y_reg = y[valid_mask]
    print(f"  Samples: {len(y_reg)}, mean={np.mean(y_reg):.4f}, std={np.std(y_reg):.4f}")

    nulls = {
        "predict_zero": null_regression(y_reg, "predict_zero"),
        "predict_mean": null_regression(y_reg, "predict_mean"),
        "persist_last_value": null_regression(y_reg, "persist_last_value"),
    }
    for n in nulls.values():
        print(f"  {n.strategy:20s}  r2={n.r2:.4f}  dir_acc={n.dir_acc:.4f}")

    reg_shuffle_seeds = list(range(42, 47))
    splits = walk_forward_splits(len(y_reg), N_FOLDS)
    model_names = ["Ridge", "RandomForestRegressor", "HistGradientBoostingRegressor"]
    models_out = {}
    for name in model_names:
        fn = make_reg(name)
        print(f"\n  [{name}]")
        result = run_walk_forward_regression(X_reg, y_reg, splits, fn)
        result.model_name = name
        sh_avg, sh_std = run_shuffle_test_regression(X_reg, y_reg, splits, fn, reg_shuffle_seeds)
        result.shuffle_r2_avg = sh_avg
        result.shuffle_r2_std = sh_std
        result.shuffle_r2_delta = result.avg_r2 - sh_avg
        print(f"    r2={result.avg_r2:.4f}  mae={result.avg_mae:.4f}  dir_acc={result.avg_dir_acc:.4f}  ic={result.avg_ic:.4f}")
        print(f"    shuffled r2: {sh_avg:.4f} ± {sh_std:.4f}  delta={result.shuffle_r2_delta:+.4f}")
        models_out[name] = asdict(result)

    best_model_r2 = max(models_out, key=lambda k: models_out[k]["avg_r2"])
    best_r2 = models_out[best_model_r2]["avg_r2"]
    best_null_r2 = max(n.r2 for n in nulls.values())
    best_shuffle_delta = max(m["shuffle_r2_delta"] for m in models_out.values())
    print(f"\n  Best R²: {best_model_r2} (r2={best_r2:.4f}) vs best null (r2={best_null_r2:.4f})")
    print(f"  Beats null R²: {best_r2 > best_null_r2 + 0.001}")

    return {
        "phase": "3C_regression",
        "feature_count": len(feature_names),
        "n_samples": len(y_reg),
        "target_stats": {"mean": float(np.mean(y_reg)), "std": float(np.std(y_reg))},
        "null_baselines": {k: asdict(v) for k, v in nulls.items()},
        "models": models_out,
        "best_model": best_model_r2,
        "best_r2": round(best_r2, 4),
        "best_null_r2": round(best_null_r2, 4),
        "beats_null_r2": bool(best_r2 > best_null_r2 + 0.001),
        "best_shuffle_delta": round(best_shuffle_delta, 4),
    }


def main() -> dict:
    t0 = time.time()
    print("=" * 60)
    print("QV2 — ALL PHASES")
    print(f"Feature space: {len(BASE_FEATURES)} base TA + 30 MTF + 3 funding = {len(BASE_FEATURES) + 30 + 3}")
    print("=" * 60)

    ohlcv = load_ohlcv()
    funding = load_funding()
    X, _, feature_names, _ = build_feature_matrix(ohlcv, funding)

    phases = {}

    result_3a = run_phase_3a(X, label_3class(ohlcv, lookahead=5, threshold=0.5), feature_names)
    phases[result_3a["phase"]] = result_3a
    save_report(result_3a, "phase3a_multiclass.json")

    y_bin, mask_bin = label_binary(ohlcv, lookahead=5, threshold=0.5)
    result_3b = run_phase_3b(X, y_bin, mask_bin, feature_names)
    phases[result_3b["phase"]] = result_3b
    save_report(result_3b, "phase3b_binary.json")

    result_3c = run_phase_3c(X, ohlcv, feature_names)
    phases[result_3c["phase"]] = result_3c
    save_report(result_3c, "phase3c_regression.json")

    total_elapsed = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"QV2 complete: {total_elapsed:.1f}s")
    print("=" * 60)
    print(f"\nQuick summary:")
    for phase, result in sorted(phases.items()):
        print(f"  {phase}: best_f1={result.get('best_f1', result.get('best_r2', '?')):.4f}  "
              f"shuffle_delta={result['best_shuffle_delta']:+.4f}")

    report = {
        "experiment": "QV2",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "features": {
            "base_ta": len(BASE_FEATURES),
            "mtf_4h": 15,
            "mtf_1d": 15,
            "funding": 3,
            "total": len(feature_names),
            "names": feature_names,
        },
        "phases": phases,
        "elapsed_seconds": total_elapsed,
    }
    save_report(report, "qv2_report.json")
    return report


if __name__ == "__main__":
    main()
