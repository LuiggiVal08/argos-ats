"""FASE 6.75.1 — Purged K-Fold Cross Validation.

Expanding-window purged CV with 6 folds.
For each fold: train on all data BEFORE the test segment,
purge LABEL_LOOKAHEAD bars to prevent label leakage.
"""
from __future__ import annotations

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

FOLDS = [
    {"train_end": "2022-07-31", "test_start": "2022-08-01", "test_end": "2023-03-31"},
    {"train_end": "2023-03-31", "test_start": "2023-04-01", "test_end": "2023-10-31"},
    {"train_end": "2023-10-31", "test_start": "2023-11-01", "test_end": "2024-05-31"},
    {"train_end": "2024-05-31", "test_start": "2024-06-01", "test_end": "2024-12-31"},
    {"train_end": "2024-12-31", "test_start": "2025-01-01", "test_end": "2025-07-31"},
    {"train_end": "2025-07-31", "test_start": "2025-08-01", "test_end": "2026-06-16"},
]


def load_retained_features() -> list[str]:
    with open(RETAINED_FILE) as f:
        return [line.strip() for line in f if line.strip()]


def get_date_range_mask(timestamps: pd.Series, start: str, end: str) -> np.ndarray:
    if isinstance(timestamps.iloc[0], (int, float, np.integer, np.floating)):
        ts_dt = pd.to_datetime(timestamps, unit="ns")
    else:
        ts_dt = pd.to_datetime(timestamps)
    return (ts_dt >= start) & (ts_dt <= end)


def get_before_mask(timestamps: pd.Series, end: str) -> np.ndarray:
    if isinstance(timestamps.iloc[0], (int, float, np.integer, np.floating)):
        ts_dt = pd.to_datetime(timestamps, unit="ns")
    else:
        ts_dt = pd.to_datetime(timestamps)
    return ts_dt <= end


def compute_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, window: int = 14) -> np.ndarray:
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - close[:-1]),
                               np.abs(low[1:] - close[:-1])))
    tr_full = np.concatenate([[tr[0]], tr])
    atr = pd.Series(tr_full).rolling(window).mean().values
    return np.nan_to_num(atr, nan=0.0)


def run_fold(fold_id: int, fold: dict,
             X: np.ndarray, y: np.ndarray,
             close: np.ndarray, high: np.ndarray, low: np.ndarray,
             timestamps: pd.Series, full_labels: np.ndarray,
             retained_idx: list[int]) -> dict | None:
    train_mask = get_before_mask(timestamps, fold["train_end"])
    test_mask = get_date_range_mask(timestamps, fold["test_start"], fold["test_end"])

    train_idx_full = np.where(train_mask)[0]
    test_idx = np.where(test_mask)[0]

    if len(train_idx_full) < 200 or len(test_idx) < 100:
        return None

    # Purge: exclude last LABEL_LOOKAHEAD bars from train (label leakage from shift(-5))
    train_idx = train_idx_full[:-LABEL_LOOKAHEAD] if len(train_idx_full) > LABEL_LOOKAHEAD else train_idx_full

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
        "fold": fold_id,
        "train_end": fold["train_end"],
        "test_start": fold["test_start"],
        "test_end": fold["test_end"],
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
        "max_consecutive_losses": result.max_consecutive_losses,
        "max_consecutive_wins": result.max_consecutive_wins,
    }


def main():
    t0 = time.time()
    print("=" * 60)
    print("FASE 6.75.1 — Purged K-Fold CV")
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

    results = []
    for fold_id, fold in enumerate(FOLDS):
        print(f"\nFold {fold_id}: train ≤{fold['train_end']}, test {fold['test_start']}→{fold['test_end']}")
        f_result = run_fold(fold_id, fold, X, y, close, high, low, timestamps, full_labels, retained_idx)

        if f_result is None:
            print("  SKIP (insufficient data)")
            continue

        results.append(f_result)
        print(f"  Trades: {f_result['n_trades']}, Sharpe: {f_result['sharpe']:.2f}, "
              f"Calmar: {f_result['calmar']:.2f}, PF: {f_result['profit_factor']:.2f}")

    if len(results) == 0:
        print("\nNo folds completed!")
        return

    # Summary
    metrics = ["sharpe", "calmar", "profit_factor", "expectancy", "cagr",
               "total_return_pct", "max_dd_pct", "sortino", "n_trades", "win_rate"]
    summary = {}
    for m in metrics:
        vals = [r[m] for r in results if m in r]
        summary[m] = {
            "mean": float(np.mean(vals)),
            "std": float(np.std(vals)),
            "min": float(np.min(vals)),
            "max": float(np.max(vals)),
            "values": [r[m] for r in results if m in r],
        }

    output = {
        "results": results,
        "n_folds": len(results),
        "summary": summary,
        "elapsed_seconds": time.time() - t0,
    }

    report_path = REPORT_DIR / "purged_cv.json"
    with open(report_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nReport: {report_path}")

    print(f"\n{'=' * 50}")
    print("SUMMARY")
    print(f"{'=' * 50}")
    print(f"  Mean Sharpe:       {summary['sharpe']['mean']:.2f} ± {summary['sharpe']['std']:.2f}")
    print(f"  Mean Calmar:       {summary['calmar']['mean']:.2f} ± {summary['calmar']['std']:.2f}")
    print(f"  Mean Profit Factor: {summary['profit_factor']['mean']:.2f} ± {summary['profit_factor']['std']:.2f}")
    print(f"  Mean CAGR:          {summary['cagr']['mean']:.2f}%")
    print(f"  Mean Max DD:        {summary['max_dd_pct']['mean']:.2f}%")
    print(f"  Mean Win Rate:      {summary['win_rate']['mean']:.2%}")
    print(f"Elapsed: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
