"""FASE 6.5.4 — Benchmark vs Buy & Hold.

Compare each fold's risk-adjusted metrics against buy-and-hold.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR / "experiments"))
from fase55_feature_selection import load_data

WF_DIR = BASE_DIR / "reports" / "walkforward"
REPORT_DIR = BASE_DIR / "reports" / "benchmark"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def get_date_range_mask(timestamps: pd.Series, start: str, end: str) -> np.ndarray:
    if isinstance(timestamps.iloc[0], (int, float, np.integer, np.floating)):
        ts_dt = pd.to_datetime(timestamps, unit="ns")
    else:
        ts_dt = pd.to_datetime(timestamps)
    return (ts_dt >= start) & (ts_dt <= end)


def buy_and_hold_metrics(close: np.ndarray, timestamps: pd.Series,
                         test_start: str, test_end: str) -> dict:
    """Compute buy-and-hold metrics for the test period."""
    mask = get_date_range_mask(timestamps, test_start, test_end)
    idx = np.where(mask)[0]
    if len(idx) < 2:
        return {}

    test_close = close[idx]
    n = len(idx)
    years = n / (365 * 24)

    start_price = test_close[0]
    end_price = test_close[-1]
    total_return = (end_price / start_price - 1) * 100
    cagr = ((end_price / start_price) ** (1 / years) - 1) * 100 if years > 0 else 0.0

    # Daily returns for Sharpe/Sortino
    daily_ret = np.diff(test_close) / test_close[:-1]
    sharpe = float(np.mean(daily_ret) / np.std(daily_ret) * np.sqrt(365 * 24)) if np.std(daily_ret) > 0 else 0.0

    neg = daily_ret[daily_ret < 0]
    downside = np.std(neg) if len(neg) > 0 else 1e-10
    sortino = float(np.mean(daily_ret) / downside * np.sqrt(365 * 24))

    # Max DD
    peak = np.maximum.accumulate(test_close)
    dd = (peak - test_close) / peak * 100
    max_dd = float(np.max(dd))

    calmar = cagr / max_dd if max_dd > 0 else 0.0

    return {
        "total_return_pct": total_return,
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "max_dd_pct": max_dd,
        "years": years,
    }


def main():
    t0 = time.time()
    print("=" * 60)
    print("FASE 6.5.4 — Benchmark vs Buy & Hold")
    print("=" * 60)

    df, X, y, close, high, low, volume = load_data()
    timestamps = df["timestamp"]

    folds = [
        {"test_start": "2023-07-01", "test_end": "2023-12-31"},
        {"test_start": "2024-01-01", "test_end": "2024-06-30"},
        {"test_start": "2024-07-01", "test_end": "2024-12-31"},
        {"test_start": "2025-01-01", "test_end": "2025-12-31"},
    ]

    print(f"\n{'Fold':<6} {'Metric':<20} {'Strategy':<12} {'B&H':<12} {'Better?':<8}")
    print("-" * 60)

    all_comparisons = []
    for fold_id, fold in enumerate(folds):
        # Load strategy result
        wf_path = WF_DIR / f"fold_{fold_id}.json"
        if not wf_path.exists():
            print(f"Fold {fold_id}: no walkforward result found")
            continue

        with open(wf_path) as f:
            strat = json.load(f)

        bh = buy_and_hold_metrics(close, timestamps, fold["test_start"], fold["test_end"])

        metrics = ["total_return_pct", "cagr", "sharpe", "sortino", "calmar", "max_dd_pct"]
        comparisons = {"fold": fold_id, "strategy": {}, "buy_and_hold": {}}

        for m in metrics:
            s_val = strat.get(m, 0)
            b_val = bh.get(m, 0)
            comparisons["strategy"][m] = s_val
            comparisons["buy_and_hold"][m] = b_val

            if m == "max_dd_pct":
                better = s_val < b_val  # lower DD is better
            else:
                better = s_val > b_val

            label = "Strategy" if fold_id == 0 else ""
            s_fmt = f"{s_val:.2f}" if m != "max_dd_pct" else f"{s_val:.2f}%"
            b_fmt = f"{b_val:.2f}" if m != "max_dd_pct" else f"{b_val:.2f}%"
            print(f"{f'Fold {fold_id}':<6} {m:<20} {s_fmt:<12} {b_fmt:<12} {'YES' if better else 'no':<8}")

        all_comparisons.append(comparisons)
        print()

    # Save per-fold and summary
    for fold_id, comp in enumerate(all_comparisons):
        path = REPORT_DIR / f"fold_{fold_id}.json"
        with open(path, "w") as f:
            json.dump(comp, f, indent=2, default=str)
        print(f"Saved: {path}")

    summary_path = REPORT_DIR / "benchmark_summary.json"
    with open(summary_path, "w") as f:
        json.dump({"comparisons": all_comparisons, "elapsed_seconds": time.time() - t0}, f, indent=2)
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
