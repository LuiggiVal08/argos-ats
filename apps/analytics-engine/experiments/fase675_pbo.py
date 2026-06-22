"""FASE 6.75.4 — Probability of Backtest Overfitting (PBO).

Uses Combinatorial Purged CV with 28 splits.
For each split: evaluates 60 configs on IS and OOS.
PBO = fraction of splits where best IS config ranks below median in OOS.
(López de Prado, Advances in Financial ML, Ch.12)
"""
from __future__ import annotations

import itertools
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

REPORT_DIR = BASE_DIR / "reports" / "fase675"
REPORT_DIR.mkdir(parents=True, exist_ok=True)
RETAINED_FILE = BASE_DIR / "data" / "retained_features.txt"
LABEL_LOOKAHEAD = 5

N_GROUPS = 8
K_TEST = 2

GROUPS = [
    ("2022-01-01", "2022-08-31"),
    ("2022-09-01", "2023-04-30"),
    ("2023-05-01", "2023-12-31"),
    ("2024-01-01", "2024-05-31"),
    ("2024-06-01", "2024-10-31"),
    ("2024-11-01", "2025-04-30"),
    ("2025-05-01", "2025-10-31"),
    ("2025-11-01", "2026-06-16"),
]

# Real configs tested in FASE 5.5.2 + 5.5.3 Stage A
REAL_CONFIGS = []
for sl_mult, tp_mult in [(1.0, 2.0), (1.5, 3.0), (2.0, 4.0)]:
    for risk_pct in [0.0025, 0.005, 0.01]:
        REAL_CONFIGS.append({
            "sl_mult": sl_mult, "tp_mult": tp_mult, "risk_pct": risk_pct,
            "min_prob": 0.425, "adx_threshold": 0,
        })

for min_prob in [0.425, 0.50, 0.55, 0.60, 0.65]:
    for adx_threshold in [0, 20, 25, 30]:
        REAL_CONFIGS.append({
            "sl_mult": 1.5, "tp_mult": 3.0, "risk_pct": 0.01,
            "min_prob": min_prob, "adx_threshold": adx_threshold,
        })

# Remove duplicates
REAL_CONFIGS_JSON = {json.dumps(c, sort_keys=True) for c in REAL_CONFIGS}
REAL_CONFIGS = [json.loads(s) for s in REAL_CONFIGS_JSON]  # 28 unique

# Synthetic grid for neighbor configs
SYNTH_GRID = {
    "sl_mult": [1.0, 1.5, 2.0, 2.5, 3.0],
    "tp_mult": [2.0, 3.0, 4.0, 5.0, 6.0],
    "risk_pct": [0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02],
    "min_prob": [0.35, 0.425, 0.5, 0.55, 0.6, 0.65, 0.7, 0.8],
    "adx_threshold": [0, 15, 20, 25, 30],
}

random.seed(42)
np.random.seed(42)


def load_retained_features() -> list[str]:
    with open(RETAINED_FILE) as f:
        return [line.strip() for line in f if line.strip()]


def get_date_range_mask(timestamps: pd.Series, start: str, end: str) -> np.ndarray:
    if isinstance(timestamps.iloc[0], (int, float, np.integer, np.floating)):
        ts_dt = pd.to_datetime(timestamps, unit="ns")
    else:
        ts_dt = pd.to_datetime(timestamps)
    return (ts_dt >= start) & (ts_dt <= end)


def get_mask_for_groups(timestamps: pd.Series, group_indices: list[int]) -> np.ndarray:
    mask = np.zeros(len(timestamps), dtype=bool)
    for gi in group_indices:
        g = GROUPS[gi]
        mask |= get_date_range_mask(timestamps, g[0], g[1])
    return mask


def compute_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, window: int = 14) -> np.ndarray:
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - close[:-1]),
                               np.abs(low[1:] - close[:-1])))
    tr_full = np.concatenate([[tr[0]], tr])
    atr = pd.Series(tr_full).rolling(window).mean().values
    return np.nan_to_num(atr, nan=0.0)


def purge_indices(train_idx: np.ndarray, test_idx: np.ndarray, timestamps: pd.Series) -> np.ndarray:
    ts = timestamps.values
    test_start = ts[test_idx[0]]
    if isinstance(test_start, (int, float, np.integer, np.floating)):
        test_start_dt = pd.Timestamp(test_start)
    else:
        test_start_dt = pd.Timestamp(test_start)
    purge_cutoff = test_start_dt - pd.Timedelta(hours=LABEL_LOOKAHEAD)
    train_dt = pd.to_datetime(ts[train_idx])
    return train_idx[train_dt < purge_cutoff]


def generate_synthetic_configs(n: int) -> list[dict]:
    real_json = set(json.dumps(c, sort_keys=True) for c in REAL_CONFIGS)
    synths: list[dict] = []
    attempts = 0
    while len(synths) < n and attempts < n * 50:
        cfg = {
            "sl_mult": random.choice(SYNTH_GRID["sl_mult"]),
            "tp_mult": random.choice(SYNTH_GRID["tp_mult"]),
            "risk_pct": random.choice(SYNTH_GRID["risk_pct"]),
            "min_prob": random.choice(SYNTH_GRID["min_prob"]),
            "adx_threshold": random.choice(SYNTH_GRID["adx_threshold"]),
        }
        cfg_json = json.dumps(cfg, sort_keys=True)
        if cfg_json not in real_json:
            real_json.add(cfg_json)
            synths.append(cfg)
        attempts += 1
    return synths


def evaluate_config(engine_params: dict,
                    close: np.ndarray, high: np.ndarray, low: np.ndarray,
                    timestamps_sel: np.ndarray, proba_sel: np.ndarray,
                    atr_sel: np.ndarray) -> dict:
    engine = BacktestEngine(**engine_params)
    result = engine.run(close, high, low, timestamps_sel, proba_sel, atr_values=atr_sel)
    return {"sharpe": result.sharpe, "calmar": result.calmar, "n_trades": result.n_trades,
            "profit_factor": result.profit_factor, "expectancy": result.expectancy,
            "total_return_pct": result.total_return_pct, "max_dd_pct": result.max_dd_pct,
            "sortino": result.sortino, "cagr": result.cagr, "win_rate": result.win_rate}


def main():
    t0 = time.time()
    print("=" * 60)
    print("FASE 6.75.4 — Probability of Backtest Overfitting")
    print("=" * 60)

    retained = load_retained_features()
    retained_set = set(retained)
    names = list(ALL_NAMES)
    retained_idx = [i for i, name in enumerate(names) if name in retained_set]
    print(f"Retained features: {len(retained)}/{len(names)}")

    # Generate configs: 28 real + 32 synthetic
    synthetic = generate_synthetic_configs(32)
    all_configs = REAL_CONFIGS + synthetic
    print(f"Configs: {len(all_configs)} (real={len(REAL_CONFIGS)}, synthetic={len(synthetic)})")

    df, X, y, close, high, low, volume = load_data()
    timestamps = df["timestamp"]
    full_labels = y.astype(np.int32)
    print(f"Data shape: {X.shape}, range: {timestamps.iloc[0]} to {timestamps.iloc[-1]}")
    atr_full = compute_atr(high, low, close)

    combos = list(itertools.combinations(range(N_GROUPS), K_TEST))
    print(f"CPCV combos: {len(combos)}")

    split_results = []

    for combo_id, test_groups in enumerate(combos):
        g_labels = "; ".join([f"G{g}" for g in test_groups])
        test_mask = get_mask_for_groups(timestamps, list(test_groups))
        test_idx = np.where(test_mask)[0]

        all_indices = set(range(len(timestamps)))
        train_candidates = sorted(all_indices - set(test_idx))
        train_idx = purge_indices(np.array(train_candidates), test_idx, timestamps)

        if len(train_idx) < 200 or len(test_idx) < 100:
            continue

        X_r = X[:, retained_idx]
        scaler = StandardScaler()
        X_train = scaler.fit_transform(X_r[train_idx])
        y_train = full_labels[train_idx]
        train_valid = y_train != -1

        if train_valid.sum() < 50:
            continue

        rf = RandomForestClassifier(
            max_depth=7, n_estimators=100, class_weight="balanced",
            random_state=42, n_jobs=-1,
        )
        rf.fit(X_train[train_valid], y_train[train_valid])

        # Predict on train (IS) and test (OOS)
        # Full proba array: 0 for invalid (y=-1) bars, RF prediction for valid bars
        proba_full = np.zeros(len(close))
        proba_full[train_idx[train_valid]] = rf.predict_proba(X_train[train_valid])[:, 1]

        X_test = scaler.transform(X_r[test_idx])
        test_valid_mask = full_labels[test_idx] != -1
        if test_valid_mask.sum() > 0:
            proba_full[test_idx[test_valid_mask]] = rf.predict_proba(X_test[test_valid_mask])[:, 1]

        proba_is = proba_full[train_idx]        # contiguous train bars, proba=0 for invalid
        proba_oos = proba_full[test_idx]         # contiguous test bars, proba=0 for invalid

        # Evaluate all configs on IS and OOS
        is_metrics = []
        oos_metrics = []

        ts_train = timestamps.iloc[train_idx]
        close_train = close[train_idx]
        high_train = high[train_idx]
        low_train = low[train_idx]
        atr_train = atr_full[train_idx]

        ts_oos = timestamps.iloc[test_idx]
        close_oos = close[test_idx]
        high_oos = high[test_idx]
        low_oos = low[test_idx]
        atr_oos = atr_full[test_idx]

        for i, cfg in enumerate(all_configs):
            engine_params = {
                "sl_mult": cfg["sl_mult"], "tp_mult": cfg["tp_mult"],
                "risk_pct": cfg["risk_pct"],
                "min_prob": cfg["min_prob"], "adx_threshold": cfg["adx_threshold"],
                "fee": 0.001, "slippage": 0.0005, "spread": 0.0001,
            }
            is_m = evaluate_config(engine_params, close_train, high_train, low_train,
                                   ts_train.values, proba_is, atr_train)
            oos_m = evaluate_config(engine_params, close_oos, high_oos, low_oos,
                                    ts_oos.values, proba_oos, atr_oos)
            is_metrics.append(is_m)
            oos_metrics.append(oos_m)

        # Rank by IS Sharpe
        is_sharpes = np.array([m["sharpe"] for m in is_metrics])
        oos_sharpes = np.array([m["sharpe"] for m in oos_metrics])

        is_rank = np.argsort(-is_sharpes)  # Descending: index 0 = best IS
        best_is_idx = is_rank[0]

        # OOS rank of the best IS config
        oos_rank_of_best = np.sum(oos_sharpes > oos_sharpes[best_is_idx])  # Lower is better (0 = best OOS)

        split_results.append({
            "combo": combo_id,
            "test_groups": list(test_groups),
            "best_is_index": int(best_is_idx),
            "best_is_sharpe": float(is_sharpes[best_is_idx]),
            "oos_sharpe_of_best": float(oos_sharpes[best_is_idx]),
            "oos_rank_of_best": int(oos_rank_of_best),
            "n_configs": len(all_configs),
            "is_above_median": oos_rank_of_best < (len(all_configs) / 2),
        })

        if (combo_id + 1) % 5 == 0 or combo_id == len(combos) - 1:
            print(f"  Combo {combo_id + 1}/{len(combos)} ({g_labels}): "
                  f"best IS rank in OOS = {oos_rank_of_best}/{len(all_configs)}, "
                  f"above median = {oos_rank_of_best < len(all_configs) / 2}")
            print(f"    IS Sharpe: {is_sharpes[best_is_idx]:.2f}, "
                  f"OOS Sharpe: {oos_sharpes[best_is_idx]:.2f}")

    n_splits = len(split_results)
    if n_splits == 0:
        print("\nNo splits completed!")
        return

    n_fail = sum(1 for r in split_results if not r["is_above_median"])
    pbo = n_fail / n_splits

    print(f"\n{'=' * 50}")
    print(f"PBO RESULTS")
    print(f"{'=' * 50}")
    print(f"  Completed splits:  {n_splits}")
    print(f"  Best IS below median in OOS: {n_fail}/{n_splits}")
    print(f"  PBO = {pbo:.3f}")
    print(f"  Verdict: ", end="")
    if pbo < 0.2:
        print("ROBUST (PBO < 0.2)")
    elif pbo < 0.4:
        print("DUDOSO (0.2 ≤ PBO < 0.4)")
    elif pbo < 0.5:
        print("SOBREAJUSTE PROBABLE (0.4 ≤ PBO < 0.5)")
    else:
        print("HARD FAIL (PBO ≥ 0.5)")

    output = {
        "pbo": float(pbo),
        "n_splits": n_splits,
        "n_configs": len(all_configs),
        "n_real_configs": len(REAL_CONFIGS),
        "n_synthetic_configs": len(synthetic),
        "best_is_below_median_count": n_fail,
        "splits": split_results,
        "verdict": "robust" if pbo < 0.2 else "dudoso" if pbo < 0.4 else "overfitting_probable" if pbo < 0.5 else "hard_fail",
        "elapsed_seconds": time.time() - t0,
    }

    report_path = REPORT_DIR / "pbo.json"
    with open(report_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nReport: {report_path}")


if __name__ == "__main__":
    main()
