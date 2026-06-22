"""FASE 6.75.2 — Combinatorial Purged Cross Validation.

N=8 groups, K=2 test groups per combo, C(8,2)=28 combos.
Each combo: train on remaining 6 groups (with purge), test on 2 groups.
Reports distribution of Sharpe, Calmar, CAGR, with stability metrics.
"""
from __future__ import annotations

import itertools
import json
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


def load_retained_features() -> list[str]:
    with open(RETAINED_FILE) as f:
        return [line.strip() for line in f if line.strip()]


def get_group_mask(timestamps: pd.Series, groups: list[tuple[str, str]]) -> np.ndarray:
    mask = np.zeros(len(timestamps), dtype=bool)
    for g in groups:
        mask |= get_date_range_mask(timestamps, g[0], g[1])
    return mask


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
    atr = pd.Series(tr_full).rolling(window).mean().values
    return np.nan_to_num(atr, nan=0.0)


def get_test_mask_from_groups(timestamps: pd.Series, test_group_indices: list[int]) -> np.ndarray:
    mask = np.zeros(len(timestamps), dtype=bool)
    for gi in test_group_indices:
        g = GROUPS[gi]
        mask |= get_date_range_mask(timestamps, g[0], g[1])
    return mask


def purge_train_indices(train_idx: np.ndarray, test_idx: np.ndarray, timestamps: pd.Series) -> np.ndarray:
    ts = timestamps.values
    test_start = ts[test_idx[0]]
    if isinstance(test_start, (int, float, np.integer, np.floating)):
        test_start_dt = pd.Timestamp(test_start)
    else:
        test_start_dt = pd.Timestamp(test_start)

    purge_cutoff = test_start_dt - pd.Timedelta(hours=LABEL_LOOKAHEAD)
    train_dt = pd.to_datetime(ts[train_idx])
    keep = train_dt < purge_cutoff
    return train_idx[keep]


def run_combo(combo_id: int, test_groups: list[int],
              X: np.ndarray, y: np.ndarray,
              close: np.ndarray, high: np.ndarray, low: np.ndarray,
              timestamps: pd.Series, full_labels: np.ndarray,
              retained_idx: list[int]) -> dict | None:
    test_mask = get_test_mask_from_groups(timestamps, test_groups)
    test_idx = np.where(test_mask)[0]

    all_indices = set(range(len(timestamps)))
    train_candidates = sorted(all_indices - set(test_idx))
    train_idx = purge_train_indices(np.array(train_candidates), test_idx, timestamps)

    if len(train_idx) < 200 or len(test_idx) < 100:
        return None

    proba_full = np.zeros(len(close))
    X_r = X[:, retained_idx]

    scaler = StandardScaler()
    X_train = scaler.fit_transform(X_r[train_idx])
    y_train = full_labels[train_idx]

    train_valid = y_train != -1
    if train_valid.sum() < 50:
        return None

    rf = RandomForestClassifier(
        max_depth=7, n_estimators=100, class_weight="balanced",
        random_state=42, n_jobs=-1,
    )
    rf.fit(X_train[train_valid], y_train[train_valid])

    X_test = scaler.transform(X_r[test_idx])
    test_valid_mask = full_labels[test_idx] != -1
    if test_valid_mask.sum() > 0:
        proba_full[test_idx[test_valid_mask]] = rf.predict_proba(X_test[test_valid_mask])[:, 1]

    atr_full = compute_atr(high, low, close)

    engine = BacktestEngine(
        sl_mult=2.0, tp_mult=4.0, risk_pct=0.01,
        min_prob=0.6, adx_threshold=0,
        fee=0.001, slippage=0.0005, spread=0.0001,
    )

    test_slice_start = test_idx[0]
    test_slice_end = test_idx[-1] + 1
    result = engine.run(
        close[test_slice_start:test_slice_end],
        high[test_slice_start:test_slice_end],
        low[test_slice_start:test_slice_end],
        timestamps.iloc[test_slice_start:test_slice_end].values,
        proba_full[test_slice_start:test_slice_end],
        atr_values=atr_full[test_slice_start:test_slice_end],
    )

    return {
        "combo": combo_id,
        "test_groups": test_groups,
        "n_train_bars": int(len(train_idx)),
        "n_train_valid": int(train_valid.sum()),
        "n_test_bars": int(len(test_idx)),
        "n_trades": result.n_trades,
        "win_rate": result.win_rate,
        "total_return_pct": result.total_return_pct,
        "sharpe": result.sharpe,
        "sortino": result.sortino,
        "calmar": result.calmar,
        "max_dd_pct": result.max_dd_pct,
        "profit_factor": result.profit_factor,
        "expectancy": result.expectancy,
        "cagr": result.cagr,
        "avg_bars_held": result.avg_bars_held,
    }


def main():
    t0 = time.time()
    print("=" * 60)
    print("FASE 6.75.2 — Combinatorial Purged CV")
    print(f"N={N_GROUPS}, K={K_TEST}, C({N_GROUPS},{K_TEST})={len(list(itertools.combinations(range(N_GROUPS), K_TEST)))} combos")
    print("=" * 60)

    retained = load_retained_features()
    retained_set = set(retained)
    names = list(ALL_NAMES)
    retained_idx = [i for i, name in enumerate(names) if name in retained_set]
    print(f"Retained features: {len(retained)}/{len(names)}")

    df, X, y, close, high, low, volume = load_data()
    timestamps = df["timestamp"]
    full_labels = y.astype(np.int32)
    print(f"Data shape: {X.shape}, range: {timestamps.iloc[0]} to {timestamps.iloc[-1]}")

    combos = list(itertools.combinations(range(N_GROUPS), K_TEST))
    print(f"Total combos: {len(combos)}")

    results = []
    for combo_id, test_groups in enumerate(combos):
        g_labels = "; ".join([f"G{g}" for g in test_groups])
        print(f"\nCombo {combo_id}/{len(combos)}: test = {g_labels}")
        r = run_combo(combo_id, list(test_groups), X, y, close, high, low, timestamps, full_labels, retained_idx)

        if r is None:
            print("  SKIP")
            continue

        results.append(r)
        print(f"  Trades: {r['n_trades']}, Sharpe: {r['sharpe']:.2f}, "
              f"Calmar: {r['calmar']:.2f}, Return: {r['total_return_pct']:.1f}%")

    if len(results) == 0:
        print("\nNo combos completed!")
        return

    # Distribution stats
    metrics = ["sharpe", "calmar", "profit_factor", "expectancy", "cagr",
               "total_return_pct", "max_dd_pct", "sortino", "n_trades", "win_rate"]
    dist = {}
    for m in metrics:
        vals = np.array([r[m] for r in results if m in r])
        cv = float(np.std(vals) / np.mean(vals)) if np.mean(vals) != 0 else float("inf")
        dist[m] = {
            "mean": float(np.mean(vals)),
            "std": float(np.std(vals)),
            "cv": cv,
            "min": float(np.min(vals)),
            "max": float(np.max(vals)),
            "p5": float(np.percentile(vals, 5)),
            "p25": float(np.percentile(vals, 25)),
            "p50": float(np.percentile(vals, 50)),
            "p75": float(np.percentile(vals, 75)),
            "p95": float(np.percentile(vals, 95)),
            "values": [float(v) for v in vals],
        }

    output = {
        "config": {"n_groups": N_GROUPS, "k_test": K_TEST, "n_combos": len(combos), "n_completed": len(results)},
        "groups": [{"id": i, "start": g[0], "end": g[1]} for i, g in enumerate(GROUPS)],
        "results": results,
        "distribution": dist,
        "elapsed_seconds": time.time() - t0,
    }

    report_path = REPORT_DIR / "combinatorial_cv.json"
    with open(report_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nReport: {report_path}")

    print(f"\n{'=' * 50}")
    print("DISTRIBUTION SUMMARY")
    print(f"{'=' * 50}")
    for m in ["sharpe", "calmar", "profit_factor", "cagr", "max_dd_pct"]:
        d = dist[m]
        print(f"  {m:20s}: mean={d['mean']:9.2f}  std={d['std']:9.2f}  CV={d['cv']:6.2f}  "
              f"p50={d['p50']:9.2f}  [p5={d['p5']:.2f}, p95={d['p95']:.2f}]")
    print(f"Elapsed: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
