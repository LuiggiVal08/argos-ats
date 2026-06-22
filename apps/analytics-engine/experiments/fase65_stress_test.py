"""FASE 6.5.3 — Stress Test.

Repeat walkforward's best fold with 2× fees, slippage, and spread.
PASS if Calmar > 1 survives.
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

REPORT_DIR = BASE_DIR / "reports" / "stress_test"
REPORT_DIR.mkdir(parents=True, exist_ok=True)
RETAINED_FILE = BASE_DIR / "data" / "retained_features.txt"

# Use the last fold (more data, more realistic)
FOLD = {"train_start": "2022-01-01", "train_end": "2024-12-31", "test_start": "2025-01-01", "test_end": "2025-12-31"}
LABEL_LOOKAHEAD = 5


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


def run_stress(fee: float, slippage: float, spread: float, label: str):
    """Run backtest with given friction levels."""
    print(f"\n  Config: fee={fee*100:.1f}% slip={slippage*100:.1f}% spread={spread*100:.2f}%")

    df, X, y, close, high, low, volume = load_data()
    timestamps = df["timestamp"]
    retained = [l.strip() for l in open(RETAINED_FILE) if l.strip()]
    retained_set = set(retained)
    names = list(ALL_NAMES)
    retained_idx = [i for i, n in enumerate(names) if n in retained_set]

    train_mask = get_date_range_mask(timestamps, FOLD["train_start"], FOLD["train_end"])
    test_mask = get_date_range_mask(timestamps, FOLD["test_start"], FOLD["test_end"])
    train_idx = np.where(train_mask)[0][:-LABEL_LOOKAHEAD]
    test_idx = np.where(test_mask)[0]

    X_r = X[:, retained_idx]
    scaler = StandardScaler()
    X_train = scaler.fit_transform(X_r[train_idx])
    y_train = y[train_idx]
    train_valid = y_train != -1

    rf = RandomForestClassifier(max_depth=7, n_estimators=100, class_weight="balanced", random_state=42, n_jobs=-1)
    rf.fit(X_train[train_valid], y_train[train_valid])

    proba = np.zeros(len(close))
    X_test = scaler.transform(X_r[test_idx])
    test_valid = y[test_idx] != -1
    if test_valid.sum() > 0:
        proba[test_idx[test_valid]] = rf.predict_proba(X_test[test_valid])[:, 1]

    atr_full = compute_atr(high, low, close)

    engine = BacktestEngine(
        sl_mult=2.0, tp_mult=4.0, risk_pct=0.01,
        min_prob=0.6, adx_threshold=0,
        fee=fee, slippage=slippage, spread=spread,
    )

    ts = test_idx[0]
    te = test_idx[-1] + 1
    result = engine.run(close[ts:te], high[ts:te], low[ts:te],
                        timestamps.iloc[ts:te].values, proba[ts:te],
                        atr_values=atr_full[ts:te])

    return {
        "label": label,
        "fee": fee, "slippage": slippage, "spread": spread,
        "n_trades": result.n_trades, "win_rate": result.win_rate,
        "total_return_pct": result.total_return_pct,
        "sharpe": result.sharpe, "max_dd_pct": result.max_dd_pct,
        "calmar": result.calmar, "profit_factor": result.profit_factor,
        "sortino": result.sortino, "cagr": result.cagr,
        "expectancy": result.expectancy,
        "turnover_per_day": result.turnover_per_day,
    }


def main():
    t0 = time.time()
    print("=" * 60)
    print("FASE 6.5.3 — Stress Test")
    print("=" * 60)

    results = []
    results.append(run_stress(0.001, 0.0005, 0.0001, "1x (baseline)"))
    results.append(run_stress(0.002, 0.001, 0.0002, "2x (stress)"))
    results.append(run_stress(0.003, 0.0015, 0.0003, "3x (extreme)"))

    print(f"\n{'=' * 60}")
    print(f"{'Config':<20} {'trades':<7} {'WR':<6} {'Ret%':<8} {'Sharpe':<8} {'DD%':<8} {'Calmar':<8} {'PF':<8}")
    print("-" * 70)
    for r in results:
        print(f"{r['label']:<20} {r['n_trades']:<7} {r['win_rate']:<6.2%} {r['total_return_pct']:<8.2f} {r['sharpe']:<8.2f} {r['max_dd_pct']:<8.2f} {r['calmar']:<8.2f} {r['profit_factor']:<8.2f}")

    passes = [r for r in results if r["calmar"] > 1.0]
    print(f"\nStress Test: {len(passes)}/{len(results)} configs pass Calmar > 1")
    print(f"PASS: {'YES' if len(passes) == len(results) else 'NO'}")

    report_path = REPORT_DIR / "stress_test.json"
    with open(report_path, "w") as f:
        json.dump({"results": results, "pass": len(passes) == len(results), "elapsed_seconds": time.time() - t0},
                  f, indent=2, default=str)
    print(f"\nReport: {report_path}")


if __name__ == "__main__":
    main()
