"""FASE 6.5.5 — White's Reality Check.

Compute p-value for the best configuration found in FASE 5.5
against a null distribution of random configurations.

Uses bootstrap: generate N random configs, compute Calmar for each,
and see where the FASE 5.5 best config falls in the distribution.
"""
from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR / "experiments"))
from fase55_backtest_engine import BacktestEngine
from fase55_feature_selection import load_data, ALL_NAMES

REPORT_DIR = BASE_DIR / "reports" / "benchmark"
REPORT_DIR.mkdir(parents=True, exist_ok=True)
RETAINED_FILE = BASE_DIR / "data" / "retained_features.txt"

N_BOOTSTRAP = 30
LABEL_LOOKAHEAD = 5

FOLD_TEST = {"test_start": "2025-01-01", "test_end": "2025-12-31"}
FOLD_TRAIN = {"train_start": "2022-01-01", "train_end": "2024-12-31"}

# Best config from FASE 5.5
BEST_CONFIG = {
    "threshold": 0.6,
    "sl_mult": 2.0,
    "tp_mult": 4.0,
    "risk_pct": 0.01,
    "adx_threshold": 0,
}


def get_date_range_mask(timestamps: pd.Series, start: str, end: str) -> np.ndarray:
    if isinstance(timestamps.iloc[0], (int, float, np.integer, np.floating)):
        ts_dt = pd.to_datetime(timestamps, unit="ns")
    else:
        ts_dt = pd.to_datetime(timestamps)
    return (ts_dt >= start) & (ts_dt <= end)


def compute_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, window: int = 14) -> np.ndarray:
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - close[:-1]),
                               np.abs(low[1:] - close[:-1])))
    tr_full = np.concatenate([[tr[0]], tr])
    return np.nan_to_num(pd.Series(tr_full).rolling(window).mean().values, nan=0.0)


def run_config(threshold: float, sl_mult: float, tp_mult: float,
               risk_pct: float, adx_threshold: float,
               X_r: np.ndarray, y: np.ndarray,
               close: np.ndarray, high: np.ndarray, low: np.ndarray,
               timestamps: pd.Series, train_idx: np.ndarray, test_idx: np.ndarray) -> dict:
    """Run backtest for a single random configuration."""
    scaler = StandardScaler()
    X_train = scaler.fit_transform(X_r[train_idx])
    y_train = y[train_idx]
    train_valid = y_train != -1
    if train_valid.sum() < 10:
        return {"calmar": 0, "n_trades": 0}

    rf = RandomForestClassifier(max_depth=7, n_estimators=100, class_weight="balanced", random_state=42, n_jobs=-1)
    rf.fit(X_train[train_valid], y_train[train_valid])

    proba = np.zeros(len(close))
    X_test = scaler.transform(X_r[test_idx])
    test_valid = y[test_idx] != -1
    if test_valid.sum() > 0:
        proba[test_idx[test_valid]] = rf.predict_proba(X_test[test_valid])[:, 1]

    atr_full = compute_atr(high, low, close)
    engine = BacktestEngine(
        sl_mult=sl_mult, tp_mult=tp_mult, risk_pct=risk_pct,
        min_prob=threshold, adx_threshold=adx_threshold,
        fee=0.001, slippage=0.0005, spread=0.0001,
    )

    ts, te = test_idx[0], test_idx[-1] + 1
    result = engine.run(close[ts:te], high[ts:te], low[ts:te],
                        timestamps.iloc[ts:te].values, proba[ts:te],
                        atr_values=atr_full[ts:te])

    return {"calmar": result.calmar, "sharpe": result.sharpe,
            "total_return_pct": result.total_return_pct,
            "n_trades": result.n_trades, "max_dd_pct": result.max_dd_pct,
            "profit_factor": result.profit_factor}


def main():
    t0 = time.time()
    print("=" * 60)
    print("FASE 6.5.5 — White's Reality Check")
    print("=" * 60)
    print(f"Bootstrap samples: {N_BOOTSTRAP}")

    # Load data
    df, X, y, close, high, low, volume = load_data()
    timestamps = df["timestamp"]
    retained = [l.strip() for l in open(RETAINED_FILE) if l.strip()]
    retained_set = set(retained)
    names = list(ALL_NAMES)
    retained_idx = [i for i, n in enumerate(names) if n in retained_set]
    X_r = X[:, retained_idx]

    # Time masks
    train_mask = get_date_range_mask(timestamps, FOLD_TRAIN["train_start"], FOLD_TRAIN["train_end"])
    test_mask = get_date_range_mask(timestamps, FOLD_TEST["test_start"], FOLD_TEST["test_end"])
    train_idx = np.where(train_mask)[0][:-LABEL_LOOKAHEAD]
    test_idx = np.where(test_mask)[0]

    print(f"Train: {len(train_idx)} bars, Test: {len(test_idx)} bars")

    # Best config result
    print("\nRunning best config (FASE 5.5)...")
    best = run_config(
        BEST_CONFIG["threshold"], BEST_CONFIG["sl_mult"], BEST_CONFIG["tp_mult"],
        BEST_CONFIG["risk_pct"], BEST_CONFIG["adx_threshold"],
        X_r, y, close, high, low, timestamps, train_idx, test_idx,
    )
    best_calmar = best["calmar"]
    print(f"  Best config Calmar: {best_calmar:.2f}")

    if best_calmar <= 0:
        print("ERROR: Best config has Calmar <= 0. Aborting.")
        return

    # Generate random configs
    print(f"\nGenerating {N_BOOTSTRAP} random configurations...")
    random_calmars = []
    random_configs = []

    param_space = {
        "threshold": [0.5, 0.55, 0.6, 0.65, 0.7],
        "sl_mult": [1.0, 1.5, 2.0, 2.5, 3.0],
        "tp_mult": [2.0, 2.5, 3.0, 4.0, 5.0],
        "risk_pct": [0.0025, 0.005, 0.0075, 0.01, 0.015],
        "adx_threshold": [0, 20, 25, 30],
    }

    for i in range(N_BOOTSTRAP):
        cfg = {
            "threshold": random.choice(param_space["threshold"]),
            "sl_mult": random.choice(param_space["sl_mult"]),
            "tp_mult": random.choice(param_space["tp_mult"]),
            "risk_pct": random.choice(param_space["risk_pct"]),
            "adx_threshold": random.choice(param_space["adx_threshold"]),
        }
        result = run_config(
            cfg["threshold"], cfg["sl_mult"], cfg["tp_mult"],
            cfg["risk_pct"], cfg["adx_threshold"],
            X_r, y, close, high, low, timestamps, train_idx, test_idx,
        )
        random_calmars.append(result["calmar"])
        random_configs.append({**cfg, **result})

        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{N_BOOTSTRAP} complete (mean Calmar: {np.mean(random_calmars):.2f})")

    random_calmars = np.array(random_calmars)

    # Compute p-value
    n_exceed = int(np.sum(random_calmars >= best_calmar))
    p_value = n_exceed / N_BOOTSTRAP
    is_significant = p_value < 0.05

    print(f"\n{'=' * 60}")
    print(f"Results:")
    print(f"  Best config Calmar: {best_calmar:.2f}")
    print(f"  Random configs Calmar: mean={np.mean(random_calmars):.2f}, std={np.std(random_calmars):.2f}")
    print(f"  Random configs Calmar max: {np.max(random_calmars):.2f}")
    print(f"  Random configs Calmar min: {np.min(random_calmars):.2f}")
    print(f"  Configs that beat best: {n_exceed}/{N_BOOTSTRAP}")
    print(f"  p-value: {p_value:.4f}")
    print(f"  Significant (p < 0.05): {'YES' if is_significant else 'NO'}")

    # Percentiles
    for pct in [50, 75, 90, 95, 99]:
        print(f"  P{pct} Calmar: {np.percentile(random_calmars, pct):.2f}")

    report = {
        "best_config": BEST_CONFIG,
        "best_calmar": best_calmar,
        "best_metrics": {k: v for k, v in best.items() if k != "calmar"},
        "n_bootstrap": N_BOOTSTRAP,
        "random_calmar_distribution": {
            "mean": float(np.mean(random_calmars)),
            "std": float(np.std(random_calmars)),
            "max": float(np.max(random_calmars)),
            "min": float(np.min(random_calmars)),
            "p50": float(np.percentile(random_calmars, 50)),
            "p75": float(np.percentile(random_calmars, 75)),
            "p90": float(np.percentile(random_calmars, 90)),
            "p95": float(np.percentile(random_calmars, 95)),
            "p99": float(np.percentile(random_calmars, 99)),
        },
        "p_value": p_value,
        "significant": is_significant,
        "elapsed_seconds": time.time() - t0,
    }

    report_path = REPORT_DIR / "white_reality_check.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nReport: {report_path}")

    # Best random config for audit
    best_random = max(random_configs, key=lambda x: x["calmar"])
    print(f"\nBest random config: {best_random}")


if __name__ == "__main__":
    main()
