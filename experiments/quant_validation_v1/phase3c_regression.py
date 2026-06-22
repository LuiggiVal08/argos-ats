"""Phase 3C — Regression framing: predict vol-adjusted returns.

Models: Ridge, RandomForestRegressor, HistGradientBoostingRegressor.
Metrics: R², MAE, directional accuracy, IC, shuffle delta.

Usage:
  python -m experiments.quant_validation_v1.phase3c_regression
"""

from __future__ import annotations

import time
from dataclasses import asdict

import numpy as np
from sklearn.ensemble import (
    HistGradientBoostingRegressor,
    RandomForestRegressor,
)
from sklearn.linear_model import Ridge

from .common import (
    FEATURE_NAMES,
    N_FOLDS,
    RANDOM_STATE,
    REPORT_DIR,
    load_ohlcv,
    compute_features,
    label_regression,
    walk_forward_splits,
    run_walk_forward_regression,
    run_shuffle_test_regression,
    null_regression,
    save_report,
)

# fewer shuffle seeds and simpler RF to keep runtime manageable
SHUFFLE_SEEDS = list(range(42, 47))


def main() -> dict:
    t0 = time.time()
    print("=" * 60)
    print("Phase 3C — Regression Framing (predict vol-adj returns)")
    print("=" * 60)

    df = load_ohlcv()
    feat_df = compute_features(df)

    print("\n[1] Generating regression target (vol-adj returns)...")
    y = label_regression(df, lookahead=5)
    valid_mask = ~np.isnan(y)
    X = feat_df.values.astype(np.float64)
    X_reg = X[valid_mask]
    y_reg = y[valid_mask]
    print(f"  Total valid samples: {len(y_reg)} ({100*len(y_reg)/len(y):.1f}% of original)")
    print(f"  y mean={np.mean(y_reg):.4f}  std={np.std(y_reg):.4f}  |y|>0.5: {100*np.mean(np.abs(y_reg)>0.5):.1f}%")

    print("\n[2] Null baselines...")
    nulls = {
        "predict_zero": null_regression(y_reg, "predict_zero"),
        "predict_mean": null_regression(y_reg, "predict_mean"),
        "persist_last_value": null_regression(y_reg, "persist_last_value"),
    }
    for n in nulls.values():
        print(f"  {n.strategy:20s}  r2={n.r2:.4f}  mae={n.mae:.4f}  dir_acc={n.dir_acc:.4f}  ic={n.ic:.4f}")

    print(f"\n[3] Walk-forward ({N_FOLDS} folds)...")
    splits = walk_forward_splits(len(y_reg), N_FOLDS)
    for i, sp in enumerate(splits):
        print(f"  Fold {i}: train [0:{sp['train_end']}]  test [{sp['test_start']}:{sp['test_end']}]")

    def make_model(name: str):
        if name == "Ridge":
            return lambda: Ridge(alpha=1.0, random_state=RANDOM_STATE)
        elif name == "RandomForestRegressor":
            return lambda: RandomForestRegressor(n_estimators=50, max_depth=5, random_state=RANDOM_STATE, n_jobs=1)
        elif name == "HistGradientBoostingRegressor":
            return lambda: HistGradientBoostingRegressor(max_iter=200, max_depth=5, random_state=RANDOM_STATE)
        raise ValueError(f"Unknown model: {name}")

    model_names = ["Ridge", "RandomForestRegressor", "HistGradientBoostingRegressor"]
    models_out = {}
    for name in model_names:
        fn = make_model(name)
        print(f"\n  [{name}]")
        result = run_walk_forward_regression(X_reg, y_reg, splits, fn)
        result.model_name = name
        print(f"    avg_r2={result.avg_r2:.4f}  avg_mae={result.avg_mae:.4f}  dir_acc={result.avg_dir_acc:.4f}  ic={result.avg_ic:.4f}")

        print(f"    Shuffle test ({len(SHUFFLE_SEEDS)} seeds)...")
        sh_avg, sh_std = run_shuffle_test_regression(X_reg, y_reg, splits, fn, SHUFFLE_SEEDS)
        result.shuffle_r2_avg = sh_avg
        result.shuffle_r2_std = sh_std
        result.shuffle_r2_delta = result.avg_r2 - sh_avg
        print(f"    shuffled r2: {sh_avg:.4f} ± {sh_std:.4f}  delta={result.shuffle_r2_delta:+.4f}")

        models_out[name] = asdict(result)

    elapsed = time.time() - t0
    report = {
        "phase": "3C_regression",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "n_samples_original": len(y),
        "n_samples_valid": len(y_reg),
        "n_features": len(FEATURE_NAMES),
        "features": FEATURE_NAMES,
        "target_stats": {"mean": float(np.mean(y_reg)), "std": float(np.std(y_reg))},
        "null_baselines": {k: asdict(v) for k, v in nulls.items()},
        "models": models_out,
        "run_timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "elapsed_seconds": elapsed,
    }

    best_r2 = max(m["avg_r2"] for m in models_out.values())
    best_dir_acc = max(m["avg_dir_acc"] for m in models_out.values())
    best_model_r2 = max(models_out, key=lambda k: models_out[k]["avg_r2"])
    best_model_dir = max(models_out, key=lambda k: models_out[k]["avg_dir_acc"])
    best_null_r2 = max(n.r2 for n in nulls.values())
    best_null_dir = max(n.dir_acc for n in nulls.values())

    report["best_model_r2"] = {"name": best_model_r2, "avg_r2": best_r2}
    report["best_model_dir_acc"] = {"name": best_model_dir, "avg_dir_acc": best_dir_acc}
    report["beats_nulls_r2"] = best_r2 > best_null_r2 + 0.001
    report["beats_nulls_dir_acc"] = best_dir_acc > best_null_dir * 1.01

    print(f"\n  Best R² model: {best_model_r2} (r2={best_r2:.4f}) vs best null (r2={best_null_r2:.4f})")
    print(f"  Best dir_acc model: {best_model_dir} (dir_acc={best_dir_acc:.4f}) vs best null (dir_acc={best_null_dir:.4f})")
    print(f"  Beats null R²: {report['beats_nulls_r2']}  Beats null dir_acc: {report['beats_nulls_dir_acc']}")

    save_report(report, "phase3c_regression.json")
    print(f"\nTotal: {elapsed:.1f}s")
    return report


if __name__ == "__main__":
    main()
