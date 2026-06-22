"""Phase 3B — Binary framing: BUY vs SELL (HOLD removed).

Compares LogisticRegression, RandomForest, HistGradientBoosting
against null baselines and shuffled labels.

Usage:
  python -m experiments.quant_validation_v1.phase3b_binary
"""

from __future__ import annotations

import time
from dataclasses import asdict

import numpy as np
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    RandomForestClassifier,
)
from sklearn.linear_model import LogisticRegression

from .common import (
    FEATURE_NAMES,
    N_FOLDS,
    SHUFFLE_SEEDS,
    RANDOM_STATE,
    REPORT_DIR,
    load_ohlcv,
    compute_features,
    label_binary,
    walk_forward_splits,
    run_walk_forward_classification,
    run_shuffle_test_classification,
    null_classification,
    null_persist_label,
    save_report,
)


def main() -> dict:
    t0 = time.time()
    print("=" * 60)
    print("Phase 3B — Binary Framing (BUY vs SELL, no HOLD)")
    print("=" * 60)

    df = load_ohlcv()
    feat_df = compute_features(df)

    print("\n[1] Generating binary labels (BUY=1, SELL=0)...")
    y, valid_mask = label_binary(df, lookahead=5, threshold=0.5)
    X = feat_df.values.astype(np.float64)
    y = y.astype(int)
    X_bin = X[valid_mask]
    y_bin = y[valid_mask]
    unique, counts = np.unique(y_bin, return_counts=True)
    for k, v in zip(unique, counts):
        label = "BUY" if k == 1 else "SELL"
        print(f"  {label} ({int(k)}): {v} ({100*v/len(y_bin):.1f}%)")
    print(f"  Total non-HOLD samples: {len(y_bin)} ({100*len(y_bin)/len(y):.1f}% of original)")

    print("\n[2] Null baselines...")
    nulls = {
        "always_SELL": null_classification(y_bin, [0, 1], 0, "always_SELL"),
        "always_BUY": null_classification(y_bin, [0, 1], 1, "always_BUY"),
        "persist_last_label": null_persist_label(y_bin, "persist_last_label"),
    }
    for n in nulls.values():
        print(f"  {n.strategy:20s}  acc={n.accuracy:.4f}  f1={n.f1_macro:.4f}  mcc={n.mcc:.4f}")

    print(f"\n[3] Walk-forward ({N_FOLDS} folds)...")
    splits = walk_forward_splits(len(y_bin), N_FOLDS)
    for i, sp in enumerate(splits):
        print(f"  Fold {i}: train [0:{sp['train_end']}]  test [{sp['test_start']}:{sp['test_end']}]")

    def make_model(name: str):
        if name == "LogisticRegression":
            return lambda: LogisticRegression(max_iter=1000, random_state=RANDOM_STATE, class_weight="balanced", n_jobs=1)
        elif name == "RandomForest":
            return lambda: RandomForestClassifier(n_estimators=100, max_depth=7, random_state=RANDOM_STATE, class_weight="balanced", n_jobs=1)
        elif name == "HistGradientBoosting":
            return lambda: HistGradientBoostingClassifier(max_iter=200, max_depth=5, random_state=RANDOM_STATE, class_weight="balanced")
        raise ValueError(f"Unknown model: {name}")

    model_names = ["LogisticRegression", "RandomForest", "HistGradientBoosting"]
    models_out = {}
    for name in model_names:
        fn = make_model(name)
        print(f"\n  [{name}]")
        result = run_walk_forward_classification(X_bin, y_bin, splits, fn, labels=[0, 1])
        result.model_name = name
        print(f"    avg_acc={result.avg_accuracy:.4f}  avg_kappa={result.avg_kappa:.4f}  avg_f1={result.avg_f1:.4f}  avg_mcc={result.avg_mcc:.4f}  avg_auc={result.avg_auc}")

        print(f"    Shuffle test ({len(SHUFFLE_SEEDS)} seeds)...")
        sh_avg, sh_std = run_shuffle_test_classification(X_bin, y_bin, splits, fn, SHUFFLE_SEEDS, labels=[0, 1])
        result.shuffle_f1_avg = sh_avg
        result.shuffle_f1_std = sh_std
        result.shuffle_delta = result.avg_f1 - sh_avg
        print(f"    shuffled f1: {sh_avg:.4f} ± {sh_std:.4f}  delta={result.shuffle_delta:+.4f}")

        models_out[name] = asdict(result)

    elapsed = time.time() - t0
    report = {
        "phase": "3B_binary",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "n_samples_original": len(y),
        "n_samples_nonhold": len(y_bin),
        "n_features": len(FEATURE_NAMES),
        "features": FEATURE_NAMES,
        "class_distribution": {int(k): int(v) for k, v in zip(unique, counts)},
        "null_baselines": {k: asdict(v) for k, v in nulls.items()},
        "models": models_out,
        "run_timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "elapsed_seconds": elapsed,
    }

    # best model comparison
    best_f1 = max(m["avg_f1"] for m in models_out.values())
    best_model = max(models_out, key=lambda k: models_out[k]["avg_f1"])
    best_null_f1 = max(n.f1_macro for n in nulls.values())
    report["best_model"] = {"name": best_model, "avg_f1": best_f1}
    report["beats_all_nulls"] = best_f1 > best_null_f1 * 1.01
    report["notes"] = "persist_last_label dominates if labels are autocorrelated due to overlapping windows"
    print(f"\n  Best model: {best_model} (f1={best_f1:.4f}) vs best null (f1={best_null_f1:.4f})")
    print(f"  Beats all nulls: {report['beats_all_nulls']}")

    save_report(report, "phase3b_binary.json")
    print(f"\nTotal: {elapsed:.1f}s")
    return report


if __name__ == "__main__":
    main()
