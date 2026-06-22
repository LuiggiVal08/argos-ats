"""QV2 Phase 3.75 — Cross-Market Validation.

Protocol: Phase 3.5 (lookahead=5, stride=5, embargo=1)
Order: BTC (control) → ETH → SOL
ETH/SOL only run if BTC reproduces Phase 3.5 (LR f1 ≈ 0.74, Ridge R² ≈ 0.30).

Usage:
  # First fetch data
  python3 -m experiments.quant_validation_v2_phase375.fetch_data

  # Then run experiment
  python3 -m experiments.quant_validation_v2_phase375.run
"""

from __future__ import annotations

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
    SYMBOLS,
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
    run_walk_forward_classification,
    run_shuffle_test_classification,
    run_walk_forward_regression,
    run_shuffle_test_regression,
)


LOOKAHEAD = 5
STRIDE = 5
EMBARGO = 1  # ceil(5/5)
N_CLF_SHUFFLE = 10
N_REG_SHUFFLE = 5

# BTC control thresholds (Phase 3.5 expected values ±5%)
BTC_F1_EXPECTED = 0.74
BTC_R2_EXPECTED = 0.30


# ── model factories (identical to Phase 3.5) ────────────────────


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


def run_phase_3a(X_sub, y_sub, feature_names, symbol) -> dict:
    print(f"\n  {symbol} Phase 3A Multiclass (n={len(y_sub)})")
    t0 = time.time()

    nulls = {
        "always_HOLD": null_classification(y_sub, [0, 1, 2], 1, "always_HOLD"),
        "always_BUY": null_classification(y_sub, [0, 1, 2], 2, "always_BUY"),
        "always_SELL": null_classification(y_sub, [0, 1, 2], 0, "always_SELL"),
        "persist_last_label": null_persist_label(y_sub, "persist_last_label"),
    }
    for n in nulls.values():
        print(f"    null {n.strategy:20s}  acc={n.accuracy:.4f}  f1={n.f1_macro:.4f}")

    splits = walk_forward_splits_phase35(len(X_sub), N_FOLDS, EMBARGO)
    model_names = ["LogisticRegression", "RandomForest", "HistGradientBoosting"]
    models_out = {}
    for model_name in model_names:
        fn = make_clf(model_name)
        print(f"    training [{model_name}]...")
        result = run_walk_forward_classification(X_sub, y_sub, splits, fn, labels=[0, 1, 2])
        result.model_name = model_name
        print(f"    shuffle test [{model_name}] ({N_CLF_SHUFFLE} seeds)...", end=" ")
        sh_avg, sh_std = run_shuffle_test_classification(
            X_sub, y_sub, splits, fn, list(range(42, 42 + N_CLF_SHUFFLE)),
            labels=[0, 1, 2],
        )
        result.shuffle_f1_avg = sh_avg
        result.shuffle_f1_std = sh_std
        result.shuffle_delta = result.avg_f1 - sh_avg
        print(f"f1={result.avg_f1:.4f}  shuffled={sh_avg:.4f}  delta={result.shuffle_delta:+.4f}")
        models_out[model_name] = asdict(result)

    best_model = max(models_out, key=lambda k: models_out[k]["avg_f1"])
    best_f1 = models_out[best_model]["avg_f1"]
    best_null_f1 = max(n.f1_macro for n in nulls.values())
    print(f"    → best: {best_model} f1={best_f1:.4f}  null={best_null_f1:.4f}  ({time.time()-t0:.0f}s)")

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


def run_phase_3b(X_sub, y_full_sub, mask_sub, feature_names, symbol) -> dict:
    X_bin = X_sub[mask_sub]
    y_bin = y_full_sub[mask_sub]
    print(f"\n  {symbol} Phase 3B Binary (n={len(y_bin)})")
    t0 = time.time()

    unique, counts = np.unique(y_bin, return_counts=True)
    for k, v in zip(unique, counts):
        lbl = "BUY" if k == 1 else "SELL"
        print(f"    {lbl}: {v} ({100*v/len(y_bin):.1f}%)")

    nulls = {
        "always_SELL": null_classification(y_bin, [0, 1], 0, "always_SELL"),
        "always_BUY": null_classification(y_bin, [0, 1], 1, "always_BUY"),
        "persist_last_label": null_persist_label(y_bin, "persist_last_label"),
    }
    for n in nulls.values():
        print(f"    null {n.strategy:20s}  acc={n.accuracy:.4f}  f1={n.f1_macro:.4f}")

    splits = walk_forward_splits_phase35(len(X_bin), N_FOLDS, EMBARGO)
    model_names = ["LogisticRegression", "RandomForest", "HistGradientBoosting"]
    models_out = {}
    for model_name in model_names:
        fn = make_clf(model_name)
        print(f"    training [{model_name}]...")
        result = run_walk_forward_classification(X_bin, y_bin, splits, fn, labels=[0, 1])
        result.model_name = model_name
        print(f"    shuffle test [{model_name}] ({N_CLF_SHUFFLE} seeds)...", end=" ")
        sh_avg, sh_std = run_shuffle_test_classification(
            X_bin, y_bin, splits, fn, list(range(42, 42 + N_CLF_SHUFFLE)),
            labels=[0, 1],
        )
        result.shuffle_f1_avg = sh_avg
        result.shuffle_f1_std = sh_std
        result.shuffle_delta = result.avg_f1 - sh_avg
        print(f"f1={result.avg_f1:.4f}  auc={result.avg_auc}  delta={result.shuffle_delta:+.4f}")
        models_out[model_name] = asdict(result)

    best_model = max(models_out, key=lambda k: models_out[k]["avg_f1"])
    best_f1 = models_out[best_model]["avg_f1"]
    best_auc = max((m["avg_auc"] for m in models_out.values()), default=None)
    best_null_f1 = max(n.f1_macro for n in nulls.values())
    print(f"    → best: {best_model} f1={best_f1:.4f}  auc={best_auc}  null={best_null_f1:.4f}  ({time.time()-t0:.0f}s)")

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


def run_phase_3c(X_sub, y_reg_sub, feature_names, symbol) -> dict:
    valid_mask = ~np.isnan(y_reg_sub)
    X_reg = X_sub[valid_mask]
    y_reg = y_reg_sub[valid_mask]
    print(f"\n  {symbol} Phase 3C Regression (n={len(y_reg)})")
    t0 = time.time()

    nulls = {
        "predict_zero": null_regression(y_reg, "predict_zero"),
        "predict_mean": null_regression(y_reg, "predict_mean"),
        "persist_last_value": null_regression(y_reg, "persist_last_value"),
    }
    for n in nulls.values():
        print(f"    null {n.strategy:20s}  r2={n.r2:.4f}  dir_acc={n.dir_acc:.4f}")

    splits = walk_forward_splits_phase35(len(X_reg), N_FOLDS, EMBARGO)
    model_names = ["Ridge", "RandomForestRegressor", "HistGradientBoostingRegressor"]
    models_out = {}
    for model_name in model_names:
        fn = make_reg(model_name)
        print(f"    training [{model_name}]...")
        result = run_walk_forward_regression(X_reg, y_reg, splits, fn)
        result.model_name = model_name
        print(f"    shuffle test [{model_name}] ({N_REG_SHUFFLE} seeds)...", end=" ")
        sh_avg, sh_std = run_shuffle_test_regression(
            X_reg, y_reg, splits, fn, list(range(42, 42 + N_REG_SHUFFLE)),
        )
        result.shuffle_r2_avg = sh_avg
        result.shuffle_r2_std = sh_std
        result.shuffle_r2_delta = result.avg_r2 - sh_avg
        print(f"r2={result.avg_r2:.4f}  dir_acc={result.avg_dir_acc:.4f}  shuffle_delta={result.shuffle_r2_delta:+.4f}")
        models_out[model_name] = asdict(result)

    best_model_r2 = max(models_out, key=lambda k: models_out[k]["avg_r2"])
    best_r2 = models_out[best_model_r2]["avg_r2"]
    best_null_r2 = max(n.r2 for n in nulls.values())
    print(f"    → best: {best_model_r2} r2={best_r2:.4f}  null={best_null_r2:.4f}  ({time.time()-t0:.0f}s)")

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


# ── run single symbol ────────────────────────────────────────────


def run_symbol(symbol: str) -> dict | None:
    print(f"\n{'=' * 70}")
    print(f"{symbol}/USDT — Phase 3.75 (lookahead=5, stride=5, embargo=1)")
    print(f"{'=' * 70}")
    t0 = time.time()

    (X, _, feature_names, _), ohlcv = build_feature_matrix(symbol)
    indices = subsample_indices(len(X), LOOKAHEAD, STRIDE)
    X_sub = X[indices]
    print(f"  subsample: {len(indices)} ← {len(X)} original rows")

    # Labels
    y_3a = label_3class(ohlcv, lookahead=LOOKAHEAD, threshold=0.5)
    y_3a_sub = y_3a[indices]
    y_bin, mask_bin = label_binary(ohlcv, lookahead=LOOKAHEAD, threshold=0.5)
    y_bin_sub, mask_sub = y_bin[indices], mask_bin[indices]
    y_reg = label_regression(ohlcv, lookahead=LOOKAHEAD)
    y_reg_sub = y_reg[indices]

    phases = {
        "3A_multiclass": run_phase_3a(X_sub, y_3a_sub, feature_names, symbol),
        "3B_binary": run_phase_3b(X_sub, y_bin_sub, mask_sub, feature_names, symbol),
        "3C_regression": run_phase_3c(X_sub, y_reg_sub, feature_names, symbol),
    }

    result = {
        "symbol": f"{symbol}/USDT",
        "lookahead": LOOKAHEAD,
        "stride": STRIDE,
        "embargo": EMBARGO,
        "n_samples_original": len(X),
        "n_samples_subsampled": len(X_sub),
        "feature_count": len(feature_names),
        "phases": phases,
        "elapsed_seconds": round(time.time() - t0, 1),
    }

    save_report(result, f"{symbol.lower()}.json")
    print(f"\n  {symbol} complete: {result['elapsed_seconds']:.0f}s")
    return result


# ── BTC control check ────────────────────────────────────────────


def check_btc_control(result: dict) -> bool:
    """BTC must reproduce Phase 3.5 approximately. Returns False if abort."""
    p3b = result["phases"]["3B_binary"]
    p3c = result["phases"]["3C_regression"]

    models_3b = p3b.get("models", {})
    models_3c = p3c.get("models", {})
    lr = models_3b.get("LogisticRegression", {})
    ridge = models_3c.get("Ridge", {})

    f1 = lr.get("avg_f1", 0)
    auc = lr.get("avg_auc", 0)
    r2 = ridge.get("avg_r2", 0)

    print(f"\n{'─' * 60}")
    print("BTC CONTROL CHECK")
    print(f"{'─' * 60}")
    print(f"  Expected: Binary LR f1≈{BTC_F1_EXPECTED}, AUC≈0.82, Ridge R²≈{BTC_R2_EXPECTED}")
    print(f"  Got:      Binary LR f1={f1:.4f}, AUC={auc:.4f}, Ridge R²={r2:.4f}")

    f1_ok = abs(f1 - BTC_F1_EXPECTED) / BTC_F1_EXPECTED < 0.08
    r2_ok = abs(r2 - BTC_R2_EXPECTED) / max(BTC_R2_EXPECTED, 0.01) < 0.15

    if f1_ok and r2_ok:
        print(f"\n  ✅ BTC control PASS: reproduces Phase 3.5")
        print(f"  → Proceeding to ETH and SOL")
        return True
    else:
        print(f"\n  ❌ BTC control FAIL: deviation exceeds tolerance")
        print(f"     F1 tolerance: {abs(f1 - BTC_F1_EXPECTED)/BTC_F1_EXPECTED*100:.1f}% (max 8%)")
        print(f"     R² tolerance: {abs(r2 - BTC_R2_EXPECTED)/max(BTC_R2_EXPECTED,0.01)*100:.1f}% (max 15%)")
        print(f"  → ABORT: pipeline bug or data mismatch")
        return False


# ── main ─────────────────────────────────────────────────────────


def main():
    t0 = time.time()
    print("=" * 70)
    print("QV2 PHASE 3.75 — Cross-Market Validation")
    print("Protocol: Phase 3.5 (lookahead=5, stride=5, embargo=1)")
    print("Markets: BTC (control) → ETH → SOL")
    print("=" * 70)

    # 1. BTC control
    btc_result = run_symbol("BTC")
    if not btc_result:
        print("\n❌ BTC run failed. Aborting.")
        _save_master({"btc": {"error": "run failed"}}, t0)
        return

    if not check_btc_control(btc_result):
        print("\n❌ BTC control validation failed. Aborting cross-market validation.")
        _save_master({"btc": btc_result, "abort_reason": "BTC control failed"}, t0)
        return

    # 2. ETH
    eth_result = run_symbol("ETH")

    # 3. SOL
    sol_result = run_symbol("SOL")

    # 4. Save master
    results = {
        "btc": btc_result,
        "eth": eth_result,
        "sol": sol_result,
    }
    _save_master(results, t0)

    total = time.time() - t0
    print(f"\n{'=' * 70}")
    print(f"ALL SYMBOLS COMPLETE: {total:.0f}s")
    print("=" * 70)

    for sym in SYMBOLS:
        r = results.get(sym.lower())
        if not r:
            print(f"\n{sym}: ERROR")
            continue
        p3b = r.get("phases", {}).get("3B_binary", {})
        p3c = r.get("phases", {}).get("3C_regression", {})
        f1 = p3b.get("best_f1", "?")
        auc = p3b.get("best_auc", "?")
        r2 = p3c.get("best_r2", "?")
        sd_bin = p3b.get("best_shuffle_delta", "?")
        sd_reg = p3c.get("best_shuffle_delta", "?")
        print(f"  {sym:6s}  Bin F1={f1:.4f} AUC={auc:.4f}  Reg R²={r2:.4f}  SD_bin={sd_bin:+.4f}  SD_reg={sd_reg:+.4f}")

    print(f"\n  Run summary.py for final verdict.")


def _save_master(results: dict, start_time: float):
    master = {
        "experiment": "QV2_Phase375",
        "protocol": {"lookahead": LOOKAHEAD, "stride": STRIDE, "embargo": EMBARGO},
        "elapsed_seconds": round(time.time() - start_time, 1),
        "symbols": results,
    }
    save_report(master, "phase375_master.json")


if __name__ == "__main__":
    main()
