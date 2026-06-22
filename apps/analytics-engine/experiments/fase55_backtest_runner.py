"""FASE 5.5.2 — Backtest SL/TP/sizing config grid.

Tests 3 SL/TP combos × 3 risk % = 9 configs on retained features.
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

REPORT_DIR = BASE_DIR / "reports" / "backtest"
REPORT_DIR.mkdir(parents=True, exist_ok=True)
RETAINED_FILE = BASE_DIR / "data" / "retained_features.txt"


def load_retained_features() -> list[str]:
    with open(RETAINED_FILE) as f:
        return [line.strip() for line in f if line.strip()]


def compute_metrics(result) -> dict:
    return {
        "n_trades": result.n_trades, "win_rate": result.win_rate,
        "total_return_pct": result.total_return_pct,
        "sharpe": result.sharpe, "max_dd_pct": result.max_dd_pct,
        "calmar": result.calmar, "profit_factor": result.profit_factor,
        "sortino": result.sortino, "cagr": result.cagr,
        "expectancy": result.expectancy,
        "avg_win": result.avg_win, "avg_loss": result.avg_loss,
        "max_consecutive_losses": result.max_consecutive_losses,
        "avg_bars_held": result.avg_bars_held,
        "exposure_pct": result.exposure_pct,
        "time_in_market_pct": result.time_in_market_pct,
        "turnover_per_day": result.turnover_per_day,
    }


def main():
    t0 = time.time()
    print("=" * 60)
    print("FASE 5.5.2 — Backtest Config Grid (9 combos)")
    print("=" * 60)

    retained = load_retained_features()
    print(f"\nRetained features: {len(retained)}")
    retained_set = set(retained)

    # Load data and filter features
    df, X, y, close, high, low, volume = load_data()
    names = list(ALL_NAMES)

    retained_idx = [i for i, name in enumerate(names) if name in retained_set]
    print(f"Retained feature indices: {len(retained_idx)}")

    valid = y != -1
    X_v, y_v = X[valid][:, retained_idx], y[valid]

    scaler = StandardScaler()
    X_s = scaler.fit_transform(X_v)

    # Train RF on retained features only
    print("\nTraining RF on retained features...")
    rf = RandomForestClassifier(max_depth=7, n_estimators=100, class_weight="balanced", random_state=42, n_jobs=-1)
    rf.fit(X_s, y_v)

    proba = np.zeros(len(y))
    proba[valid] = rf.predict_proba(X_s)[:, 1]

    # ATR
    atr_vals = np.nan_to_num(pd.Series(high).rolling(14).apply(
        lambda x: x.max() - x.min() if len(x) == 14 else 0, raw=True
    ).values, nan=0.0)

    timestamps = df["timestamp"].values

    # 9 combos: 3 SL/TP × 3 risk%
    sl_tp_combos = [
        (1.0, 2.0, "1/2 ATR"),
        (1.5, 3.0, "1.5/3 ATR"),
        (2.0, 4.0, "2/4 ATR"),
    ]
    risk_levels = [
        (0.0025, "0.25%"),
        (0.005, "0.5%"),
        (0.01, "1%"),
    ]

    print(f"\n{'SL/TP':<14} {'Risk':<7} {'trades':<7} {'WR':<6} {'Ret%':<7} {'Sharpe':<7} {'DD%':<7} {'Calmar':<7} {'ProfF':<7} {'CAGR':<7}")
    print("-" * 85)

    all_results: list[dict] = []

    for sl_mult, tp_mult, sl_label in sl_tp_combos:
        for risk, risk_label in risk_levels:
            engine = BacktestEngine(
                sl_mult=sl_mult, tp_mult=tp_mult, risk_pct=risk,
                min_prob=0.425, adx_threshold=0,
            )
            result = engine.run(close, high, low, timestamps, proba, atr_values=atr_vals)

            label = f"{sl_label} {risk_label}"
            print(f"{sl_label:<4} {risk_label:<9} {result.n_trades:<7} {result.win_rate:<6.2%} {result.total_return_pct:<7.2f} {result.sharpe:<7.2f} {result.max_dd_pct:<7.2f} {result.calmar:<7.2f} {result.profit_factor:<7.2f} {result.cagr:<7.2f}")

            all_results.append({
                "sl_mult": sl_mult, "tp_mult": tp_mult, "sl_label": sl_label,
                "risk_pct": risk, "risk_label": risk_label,
                **compute_metrics(result),
            })

    # Rank by Calmar
    all_results.sort(key=lambda r: (-r["calmar"], r["max_dd_pct"]))

    print(f"\n--- Best 3 by Calmar ---")
    for r in all_results[:3]:
        print(f"  {r['sl_label']:<4} {r['risk_label']:<9} Calmar={r['calmar']:.2f} DD={r['max_dd_pct']:.2f}% Ret={r['total_return_pct']:.2f}%")

    report_path = REPORT_DIR / "backtest_config_grid.json"
    with open(report_path, "w") as f:
        json.dump({
            "results": all_results,
            "best": all_results[0],
            "top_3": all_results[:3],
            "elapsed_seconds": time.time() - t0,
            "retained_features": retained,
            "n_features": len(retained),
        }, f, indent=2, default=str)
    print(f"\nReport: {report_path}")
    print(f"Elapsed: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
