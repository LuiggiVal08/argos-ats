"""FASE 5.5.3 — Trade Filters (hierarchical grid search).

Stage A: 20 combos (probability × ADX)
Stage B: top 5 configs × (HTF alignment, vol regime, max trades/day)

HARD RULES:
- No cambiar modelo RandomForest
- No tocar hiperparámetros
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
from fase55_backtest_engine import BacktestEngine, BacktestResult
from fase55_feature_selection import load_data, ALL_NAMES

REPORT_DIR = BASE_DIR / "reports" / "backtest"
REPORT_DIR.mkdir(parents=True, exist_ok=True)
RETAINED_FILE = BASE_DIR / "data" / "retained_features.txt"


def load_retained_features() -> list[str]:
    with open(RETAINED_FILE) as f:
        return [line.strip() for line in f if line.strip()]


def prepare_data():
    df, X, y, close, high, low, volume = load_data()
    retained = load_retained_features()
    retained_set = set(retained)
    names = list(ALL_NAMES)
    retained_idx = [i for i, name in enumerate(names) if name in retained_set]

    valid = y != -1
    X_v, y_v = X[valid][:, retained_idx], y[valid]

    scaler = StandardScaler()
    X_s = scaler.fit_transform(X_v)

    rf = RandomForestClassifier(max_depth=7, n_estimators=100, class_weight="balanced", random_state=42, n_jobs=-1)
    rf.fit(X_s, y_v)
    full_proba = rf.predict_proba(X_s)[:, 1]

    # Full proba array (with -1 positions set to 0)
    proba_full = np.zeros(len(y))
    proba_full[valid] = full_proba

    # Build ATR
    atr_vals = pd.Series(high).rolling(14).apply(
        lambda x: x.max() - x.min() if len(x) == 14 else 0, raw=True
    ).values
    atr_vals = np.nan_to_num(atr_vals, nan=0.0)

    # ADX
    import ta as ta_lib
    adx_series = ta_lib.trend.ADXIndicator(
        pd.Series(high), pd.Series(low), pd.Series(close), window=14
    ).adx().values
    adx_vals = np.nan_to_num(adx_series, nan=0.0)

    # Vol regime (from close z-score)
    close_series = pd.Series(close)
    vol_regime = np.where(
        close_series.rolling(60).std().rank(pct=True) > 0.7, 1,
        np.where(close_series.rolling(60).std().rank(pct=True) < 0.3, -1, 0)
    ).astype(float)
    vol_regime = np.nan_to_num(vol_regime, nan=0.0)

    # HTF trend (4h EMA slope)
    htf_trend = np.sign(
        pd.Series(close).ewm(span=9).mean() - pd.Series(close).ewm(span=50).mean()
    ).values.astype(float)

    timestamps = df["timestamp"].values

    return df, rf, X_s, proba_full, close, high, low, atr_vals, adx_vals, vol_regime, htf_trend, timestamps


def grid_search_stage_a(
    proba: np.ndarray, close: np.ndarray, high: np.ndarray, low: np.ndarray,
    atr_vals: np.ndarray, adx_vals: np.ndarray,
    vol_regime: np.ndarray, htf_trend: np.ndarray, timestamps: np.ndarray,
    risk_pct: float = 0.01,
    sl_mult: float = 1.5,
    tp_mult: float = 3.0,
) -> list[dict]:
    probs = [0.425, 0.50, 0.55, 0.60, 0.65]
    adxs = [0, 20, 25, 30]
    results: list[dict] = []

    print(f"\nStage A: {len(probs)} × {len(adxs)} = {len(probs) * len(adxs)} combos")
    for p in probs:
        for a in adxs:
            engine = BacktestEngine(
                sl_mult=sl_mult, tp_mult=tp_mult, risk_pct=risk_pct,
                min_prob=p, adx_threshold=a,
            )
            result = engine.run(close, high, low, timestamps, proba, adx=adx_vals, atr_values=atr_vals)
            results.append({
                "min_prob": p, "adx_threshold": a, "risk_pct": risk_pct,
                "sl_mult": sl_mult, "tp_mult": tp_mult,
                "n_trades": result.n_trades, "win_rate": result.win_rate,
                "total_return_pct": result.total_return_pct,
                "sharpe": result.sharpe, "max_dd_pct": result.max_dd_pct,
                "calmar": result.calmar, "profit_factor": result.profit_factor,
                "sortino": result.sortino, "cagr": result.cagr,
                "expectancy": result.expectancy,
                "max_consecutive_losses": result.max_consecutive_losses,
                "turnover_per_day": result.turnover_per_day,
            })

    results.sort(key=lambda r: (-r["calmar"], r["max_dd_pct"]))
    return results


def grid_search_stage_b(
    proba: np.ndarray, close: np.ndarray, high: np.ndarray, low: np.ndarray,
    atr_vals: np.ndarray, adx_vals: np.ndarray,
    vol_regime: np.ndarray, htf_trend: np.ndarray, timestamps: np.ndarray,
    top_configs: list[dict],
    risk_pct: float = 0.01,
    sl_mult: float = 1.5,
    tp_mult: float = 3.0,
) -> list[dict]:
    htf_opts = ["none", "4h_trend", "1d_trend"]
    vol_opts = ["all", "low_only", "high_only"]
    mtd_opts = [1, 2, 3, float("inf")]

    results: list[dict] = []
    total = len(top_configs) * len(htf_opts) * len(vol_opts) * len(mtd_opts)
    print(f"\nStage B: {len(top_configs)} × {len(htf_opts)} × {len(vol_opts)} × {len(mtd_opts)} = {total} combos")

    for cfg in top_configs:
        for htf in htf_opts:
            for vol in vol_opts:
                for mtd in mtd_opts:
                    engine = BacktestEngine(
                        sl_mult=sl_mult, tp_mult=tp_mult, risk_pct=risk_pct,
                        min_prob=cfg["min_prob"], adx_threshold=cfg["adx_threshold"],
                        htf_alignment=htf,
                        vol_regime_filter=vol,
                        max_trades_per_day=mtd,
                        vol_regime_series=vol_regime,
                        htf_trend_series=htf_trend,
                    )
                    result = engine.run(close, high, low, timestamps, proba, adx=adx_vals, atr_values=atr_vals)
                    results.append({
                        "min_prob": cfg["min_prob"], "adx_threshold": cfg["adx_threshold"],
                        "htf_alignment": htf, "vol_regime": vol, "max_trades_per_day": mtd,
                        "risk_pct": risk_pct, "sl_mult": sl_mult, "tp_mult": tp_mult,
                        "n_trades": result.n_trades, "win_rate": result.win_rate,
                        "total_return_pct": result.total_return_pct,
                        "sharpe": result.sharpe, "max_dd_pct": result.max_dd_pct,
                        "calmar": result.calmar, "profit_factor": result.profit_factor,
                        "sortino": result.sortino, "cagr": result.cagr,
                        "expectancy": result.expectancy,
                        "max_consecutive_losses": result.max_consecutive_losses,
                        "turnover_per_day": result.turnover_per_day,
                    })

    results.sort(key=lambda r: (-r["calmar"], r["max_dd_pct"]))
    return results


def main():
    t0 = time.time()
    print("=" * 60)
    print("FASE 5.5.3 — Trade Filters")
    print("=" * 60)

    df, rf, X_s, proba_full, close, high, low, atr_vals, adx_vals, vol_regime, htf_trend, timestamps = prepare_data()
    timestamps_np = timestamps

    # Use default SL/TP/sizing
    risk_pct = 0.01
    sl_mult = 1.5
    tp_mult = 3.0

    # Stage A
    stage_a = grid_search_stage_a(
        proba_full, close, high, low, atr_vals, adx_vals,
        vol_regime, htf_trend, timestamps_np,
        risk_pct=risk_pct, sl_mult=sl_mult, tp_mult=tp_mult,
    )

    print(f"\nStage A — Top 10:")
    print(f"{'prob':<6} {'ADX':<4} {'trades':<7} {'WR':<6} {'Ret%':<7} {'Sharpe':<7} {'DD%':<7} {'Calmar':<7}")
    print("-" * 55)
    for r in stage_a[:10]:
        print(f"{r['min_prob']:<6} {r['adx_threshold']:<4} {r['n_trades']:<7} {r['win_rate']:<6.2%} {r['total_return_pct']:<7.2f} {r['sharpe']:<7.2f} {r['max_dd_pct']:<7.2f} {r['calmar']:<7.2f}")

    # Stage B — top 5 from stage A
    top5 = stage_a[:5]
    stage_b = grid_search_stage_b(
        proba_full, close, high, low, atr_vals, adx_vals,
        vol_regime, htf_trend, timestamps_np, top5,
        risk_pct=risk_pct, sl_mult=sl_mult, tp_mult=tp_mult,
    )

    print(f"\nStage B — Top 10:")
    print(f"{'prob':<6} {'ADX':<4} {'HTF':<12} {'Vol':<10} {'MTD':<5} {'trades':<7} {'WR':<6} {'Ret%':<7} {'Sharpe':<7} {'DD%':<7} {'Calmar':<7}")
    print("-" * 90)
    for r in stage_b[:10]:
        mtd = "inf" if r["max_trades_per_day"] == float("inf") else str(r["max_trades_per_day"])
        print(f"{r['min_prob']:<6} {r['adx_threshold']:<4} {r['htf_alignment']:<12} {r['vol_regime']:<10} {mtd:<5} {r['n_trades']:<7} {r['win_rate']:<6.2%} {r['total_return_pct']:<7.2f} {r['sharpe']:<7.2f} {r['max_dd_pct']:<7.2f} {r['calmar']:<7.2f}")

    # Report
    report = {
        "stage_a_results": stage_a,
        "stage_b_results": stage_b,
        "top_10_a": stage_a[:10],
        "top_10_b": stage_b[:10],
        "best_config": stage_b[0] if stage_b else stage_a[0],
        "elapsed_seconds": time.time() - t0,
    }
    report_path = REPORT_DIR / "filter_grid_results.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nReport: {report_path}")

    return report


if __name__ == "__main__":
    main()
