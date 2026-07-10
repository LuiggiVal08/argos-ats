"""QV2 Phase 3.5 — Non-overlapping labels experiment.

Runs 3 experiments:
  PRIMARY  (5, 5) → phase35_nonoverlap.json + horizon_5.json
  Sanity 1 (1, 1) → horizon_1.json
  Sanity 3 (3, 3) → horizon_3.json

Usage:
  cd /home/egraterol/projects/argos-ats
  python3 -m experiments.quant_validation_v2_phase35.run
"""

from __future__ import annotations

import copy
import math
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
    SHUFFLE_SEEDS,
    SYMBOL,
    TIMEFRAME,
    load_ohlcv,
    load_funding,
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
)

from experiments.quant_validation_v1.common import (
    walk_forward_splits,
    run_walk_forward_classification,
    run_shuffle_test_classification,
    run_walk_forward_regression,
    run_shuffle_test_regression,
)


EXPERIMENTS = [
    {"lookahead": 1, "stride": 1, "name": "horizon_1",  "is_primary": False, "label": "sanity (1,1)"},
    {"lookahead": 3, "stride": 3, "name": "horizon_3",  "is_primary": False, "label": "sanity (3,3)"},
    {"lookahead": 5, "stride": 5, "name": "primary",    "is_primary": True,  "label": "PRIMARY (5,5)"},
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


def run_phase_3a(X_sub, y_sub, feature_names, embargo, horizon_label) -> dict:
    print(f"\n{'=' * 60}")
    print(f"Phase 3A Multiclass — {horizon_label}")
    print("=" * 60)

    nulls = {
        "always_HOLD": null_classification(y_sub, [0, 1, 2], 1, "always_HOLD"),
        "always_BUY": null_classification(y_sub, [0, 1, 2], 2, "always_BUY"),
        "always_SELL": null_classification(y_sub, [0, 1, 2], 0, "always_SELL"),
        "persist_last_label": null_persist_label(y_sub, "persist_last_label"),
    }
    for n in nulls.values():
        print(f"  {n.strategy:20s}  acc={n.accuracy:.4f}  f1={n.f1_macro:.4f}")

    splits = walk_forward_splits_phase35(len(X_sub), N_FOLDS, embargo)
    model_names = ["LogisticRegression", "RandomForest", "HistGradientBoosting"]
    models_out = {}
    for model_name in model_names:
        fn = make_clf(model_name)
        print(f"\n  [{model_name}]")
        result = run_walk_forward_classification(X_sub, y_sub, splits, fn, labels=[0, 1, 2])
        result.model_name = model_name
        sh_avg, sh_std = run_shuffle_test_classification(X_sub, y_sub, splits, fn, SHUFFLE_SEEDS, labels=[0, 1, 2])
        result.shuffle_f1_avg = sh_avg
        result.shuffle_f1_std = sh_std
        result.shuffle_delta = result.avg_f1 - sh_avg
        print(f"    f1={result.avg_f1:.4f}  kappa={result.avg_kappa:.4f}  mcc={result.avg_mcc:.4f}")
        print(f"    shuffled f1: {sh_avg:.4f} ± {sh_std:.4f}  delta={result.shuffle_delta:+.4f}")
        models_out[model_name] = asdict(result)

    best_model = max(models_out, key=lambda k: models_out[k]["avg_f1"])
    best_f1 = models_out[best_model]["avg_f1"]
    best_null_f1 = max(n.f1_macro for n in nulls.values())
    best_shuffle_delta = max(m["shuffle_delta"] for m in models_out.values())
    print(f"\n  Best model: {best_model} (f1={best_f1:.4f}) vs best null (f1={best_null_f1:.4f})")
    print(f"  Beats null: {best_f1 > best_null_f1 * 1.01}")

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
        "best_shuffle_delta": round(best_shuffle_delta, 4),
    }


# ── PHASE 3B — binary ────────────────────────────────────────────


def run_phase_3b(X_sub, y_full_sub, mask_sub, feature_names, embargo, horizon_label) -> dict:
    print(f"\n{'=' * 60}")
    print(f"Phase 3B Binary — {horizon_label}")
    print("=" * 60)

    X_bin = X_sub[mask_sub]
    y_bin = y_full_sub[mask_sub]
    unique, counts = np.unique(y_bin, return_counts=True)
    for k, v in zip(unique, counts):
        lbl = "BUY" if k == 1 else "SELL"
        print(f"  {lbl}: {v} ({100*v/len(y_bin):.1f}%)")

    nulls = {
        "always_SELL": null_classification(y_bin, [0, 1], 0, "always_SELL"),
        "always_BUY": null_classification(y_bin, [0, 1], 1, "always_BUY"),
        "persist_last_label": null_persist_label(y_bin, "persist_last_label"),
    }
    for n in nulls.values():
        print(f"  {n.strategy:20s}  acc={n.accuracy:.4f}  f1={n.f1_macro:.4f}")

    splits = walk_forward_splits_phase35(len(X_bin), N_FOLDS, embargo)
    model_names = ["LogisticRegression", "RandomForest", "HistGradientBoosting"]
    models_out = {}
    for model_name in model_names:
        fn = make_clf(model_name)
        print(f"\n  [{model_name}]")
        result = run_walk_forward_classification(X_bin, y_bin, splits, fn, labels=[0, 1])
        result.model_name = model_name
        sh_avg, sh_std = run_shuffle_test_classification(X_bin, y_bin, splits, fn, SHUFFLE_SEEDS, labels=[0, 1])
        result.shuffle_f1_avg = sh_avg
        result.shuffle_f1_std = sh_std
        result.shuffle_delta = result.avg_f1 - sh_avg
        print(f"    f1={result.avg_f1:.4f}  kappa={result.avg_kappa:.4f}  auc={result.avg_auc}")
        print(f"    shuffled f1: {sh_avg:.4f} ± {sh_std:.4f}  delta={result.shuffle_delta:+.4f}")
        models_out[model_name] = asdict(result)

    best_model = max(models_out, key=lambda k: models_out[k]["avg_f1"])
    best_f1 = models_out[best_model]["avg_f1"]
    best_null_f1 = max(n.f1_macro for n in nulls.values())
    best_shuffle_delta = max(m["shuffle_delta"] for m in models_out.values())
    best_auc = max((m["avg_auc"] for m in models_out.values()), default=None)
    print(f"\n  Best model: {best_model} (f1={best_f1:.4f}, auc={best_auc}) vs best null (f1={best_null_f1:.4f})")
    print(f"  Beats null: {best_f1 > best_null_f1 * 1.01}")

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
        "best_shuffle_delta": round(best_shuffle_delta, 4),
    }


# ── PHASE 3C — regression ────────────────────────────────────────


def run_phase_3c(X_sub, y_reg_sub, feature_names, embargo, horizon_label) -> dict:
    print(f"\n{'=' * 60}")
    print(f"Phase 3C Regression — {horizon_label}")
    print("=" * 60)

    valid_mask = ~np.isnan(y_reg_sub)
    X_reg = X_sub[valid_mask]
    y_reg = y_reg_sub[valid_mask]
    print(f"  Samples: {len(y_reg)}, mean={np.mean(y_reg):.4f}, std={np.std(y_reg):.4f}")

    nulls = {
        "predict_zero": null_regression(y_reg, "predict_zero"),
        "predict_mean": null_regression(y_reg, "predict_mean"),
        "persist_last_value": null_regression(y_reg, "persist_last_value"),
    }
    for n in nulls.values():
        print(f"  {n.strategy:20s}  r2={n.r2:.4f}  dir_acc={n.dir_acc:.4f}")

    reg_shuffle_seeds = list(range(42, 47))
    splits = walk_forward_splits_phase35(len(X_reg), N_FOLDS, embargo)
    model_names = ["Ridge", "RandomForestRegressor", "HistGradientBoostingRegressor"]
    models_out = {}
    for model_name in model_names:
        fn = make_reg(model_name)
        print(f"\n  [{model_name}]")
        result = run_walk_forward_regression(X_reg, y_reg, splits, fn)
        result.model_name = model_name
        sh_avg, sh_std = run_shuffle_test_regression(X_reg, y_reg, splits, fn, reg_shuffle_seeds)
        result.shuffle_r2_avg = sh_avg
        result.shuffle_r2_std = sh_std
        result.shuffle_r2_delta = result.avg_r2 - sh_avg
        print(f"    r2={result.avg_r2:.4f}  mae={result.avg_mae:.4f}  dir_acc={result.avg_dir_acc:.4f}  ic={result.avg_ic:.4f}")
        print(f"    shuffled r2: {sh_avg:.4f} ± {sh_std:.4f}  delta={result.shuffle_r2_delta:+.4f}")
        models_out[model_name] = asdict(result)

    best_model_r2 = max(models_out, key=lambda k: models_out[k]["avg_r2"])
    best_r2 = models_out[best_model_r2]["avg_r2"]
    best_null_r2 = max(n.r2 for n in nulls.values())
    best_shuffle_delta = max(m["shuffle_r2_delta"] for m in models_out.values())
    print(f"\n  Best R²: {best_model_r2} (r2={best_r2:.4f}) vs best null (r2={best_null_r2:.4f})")
    print(f"  Beats null R²: {best_r2 > best_null_r2 + 0.001}")

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
        "best_shuffle_delta": round(best_shuffle_delta, 4),
    }


# ── single experiment ────────────────────────────────────────────


def run_experiment(exp: dict, X: np.ndarray, ohlcv, feature_names) -> dict:
    lookahead = exp["lookahead"]
    stride = exp["stride"]
    embargo = math.ceil(lookahead / stride)
    horizon_label = exp["label"]

    print(f"\n\n{'#' * 70}")
    print(f"# {horizon_label}  (lookahead={lookahead}, stride={stride}, embargo={embargo})")
    print(f"{'#' * 70}")

    indices = subsample_indices(len(X), lookahead, stride)
    X_sub = X[indices]
    print(f"  subsample: {len(indices)} samples ← {len(X)} original")

    y_3a = label_3class(ohlcv, lookahead=lookahead, threshold=0.5)
    y_3a_sub = y_3a[indices]

    y_bin, mask_bin = label_binary(ohlcv, lookahead=lookahead, threshold=0.5)
    y_bin_sub = y_bin[indices]
    mask_sub = mask_bin[indices]

    y_reg = label_regression(ohlcv, lookahead=lookahead)
    y_reg_sub = y_reg[indices]

    phases = {}
    phases["3A_multiclass"] = run_phase_3a(X_sub, y_3a_sub, feature_names, embargo, horizon_label)
    phases["3B_binary"] = run_phase_3b(X_sub, y_bin_sub, mask_sub, feature_names, embargo, horizon_label)
    phases["3C_regression"] = run_phase_3c(X_sub, y_reg_sub, feature_names, embargo, horizon_label)

    return {
        "experiment": exp["name"],
        "lookahead": lookahead,
        "stride": stride,
        "embargo": embargo,
        "n_samples_original": len(X),
        "n_samples_subsampled": len(X_sub),
        "feature_count": len(feature_names),
        "is_primary": exp["is_primary"],
        "horizon_label": horizon_label,
        "phases": phases,
    }


# ── main ─────────────────────────────────────────────────────────


def main():
    t0 = time.time()
    print("=" * 70)
    print("QV2 PHASE 3.5 — Non-overlapping labels + embargo")
    print("Feature space: 53 (20 TA + 30 MTF + 3 funding)")
    print("PRIMARY (5,5) decides H1 vs H0. (1,1) and (3,3) are sanity only.")
    print("=" * 70)

    ohlcv = load_ohlcv()
    funding = load_funding()
    X, _, feature_names, _ = build_feature_matrix(ohlcv, funding)

    results = {}
    for exp in EXPERIMENTS:
        result = run_experiment(exp, X, ohlcv, feature_names)
        result["elapsed_seconds"] = time.time() - t0
        name = exp["name"]

        save_report(result, f"{name}.json")

        if exp["is_primary"]:
            save_report(copy.deepcopy(result), "phase35_nonoverlap.json")
            save_report(copy.deepcopy(result), "horizon_5.json")

        results[name] = result
        print(f"\n  ✓ {exp['label']} complete ({result['elapsed_seconds']:.0f}s)")

    total = time.time() - t0
    print(f"\n{'=' * 70}")
    print(f"ALL EXPERIMENTS COMPLETE: {total:.0f}s")
    print("=" * 70)

    for name, result in results.items():
        print(f"\n{result['horizon_label']:15s}  n={result['n_samples_subsampled']}")
        for pname, phase in result["phases"].items():
            val = phase.get("best_f1", phase.get("best_r2", "?"))
            sd = phase["best_shuffle_delta"]
            print(f"  {pname:20s}  best={val:.4f}  shuffle_delta={sd:+.4f}")

    master = {
        "experiment": "QV2_Phase35",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "elapsed_seconds": total,
        "experiments": {name: {
            "lookahead": r["lookahead"],
            "stride": r["stride"],
            "embargo": r["embargo"],
            "n_samples": r["n_samples_subsampled"],
            "is_primary": r["is_primary"],
            "phases": r["phases"],
        } for name, r in results.items()},
    }
    save_report(master, "phase35_master.json")


if __name__ == "__main__":
    main()
