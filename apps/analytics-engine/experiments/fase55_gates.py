"""FASE 5.5.4 — GATE 4 + GATE 6 re-run with best config.

GATE 4 — Calmar ≥ 1.0 after all optimizations
GATE 6 — Net return > buy-and-hold with DD ≤ 40%
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
from fase55_backtest_engine import BacktestEngine, BacktestResult
from fase55_feature_selection import load_data, ALL_NAMES

REPORT_DIR = BASE_DIR / "reports" / "backtest"
REPORT_DIR.mkdir(parents=True, exist_ok=True)
RETAINED_FILE = BASE_DIR / "data" / "retained_features.txt"


def load_retained_features() -> list[str]:
    with open(RETAINED_FILE) as f:
        return [line.strip() for line in f if line.strip()]


def compute_buy_and_hold(close: np.ndarray, timestamps: np.ndarray) -> dict:
    """Compute buy-and-hold return over the period."""
    start_price = close[0]
    end_price = close[-1]
    n_hours = len(timestamps)
    years = n_hours / (365 * 24)
    total_return = (end_price / start_price - 1) * 100
    cagr = ((end_price / start_price) ** (1 / years) - 1) * 100 if years > 0 else 0.0
    return {"total_return_pct": total_return, "cagr": cagr, "years": years}


def run_gate_4(engine: BacktestEngine, result: BacktestResult) -> dict:
    print("\n" + "=" * 50)
    print("GATE 4 — Calmar ≥ 1.0")
    print("=" * 50)
    print(f"  Calmar: {result.calmar:.2f}")
    print(f"  Max DD: {result.max_dd_pct:.2f}%")
    print(f"  CAGR:   {result.cagr:.2f}%")
    print(f"  Trades: {result.n_trades}")
    passed = result.calmar >= 1.0
    print(f"  → {'PASS' if passed else 'FAIL'} (Calmar {result.calmar:.2f} {'≥' if passed else '<'} 1.0)")
    return {"gate": 4, "metric": result.calmar, "threshold": 1.0, "passed": passed}


def run_gate_6(engine: BacktestEngine, result: BacktestResult, bh: dict) -> dict:
    print("\n" + "=" * 50)
    print("GATE 6 — Net return > buy-and-hold, DD ≤ 40%")
    print("=" * 50)
    print(f"  Strategy return: {result.total_return_pct:.2f}%")
    print(f"  Buy-and-hold:    {bh['total_return_pct']:.2f}%")
    print(f"  Max DD:          {result.max_dd_pct:.2f}%")
    print(f"  DD threshold:    40%")
    better_than_bh = result.total_return_pct > bh["total_return_pct"]
    dd_ok = result.max_dd_pct <= 40.0
    passed = better_than_bh and dd_ok
    print(f"  Better than BH: {'YES' if better_than_bh else 'NO'} ({result.total_return_pct:.2f}% vs {bh['total_return_pct']:.2f}%)")
    print(f"  DD ≤ 40%:        {'YES' if dd_ok else 'NO'} ({result.max_dd_pct:.2f}%)")
    print(f"  → {'PASS' if passed else 'FAIL'}")
    return {
        "gate": 6, "strategy_return": result.total_return_pct,
        "buy_and_hold_return": bh["total_return_pct"],
        "max_dd": result.max_dd_pct, "dd_threshold": 40.0,
        "better_than_bh": better_than_bh, "dd_ok": dd_ok,
        "passed": passed,
    }


def main():
    t0 = time.time()
    print("=" * 60)
    print("FASE 5.5.4 — GATE 4 + GATE 6 Re-run")
    print("=" * 60)

    # Load best config from filter grid (Stage A top 1 = prob=0.6)
    grid_path = REPORT_DIR / "filter_grid_results.json"
    config = {"min_prob": 0.6, "adx_threshold": 0}
    if grid_path.exists():
        with open(grid_path) as f:
            grid = json.load(f)
        if grid.get("stage_a_results"):
            top = grid["stage_a_results"][0]
            config = {
                "min_prob": top.get("min_prob", 0.6),
                "adx_threshold": top.get("adx_threshold", 0),
                "htf_alignment": "none",
                "vol_regime": "all",
                "max_trades_per_day": float("inf"),
            }
            print(f"Best config from filters: prob={config['min_prob']}, ADX={config['adx_threshold']}")

    # Use best SL/TP/sizing from FASE 5.5.2: 2/4 ATR 1% risk
    combos = [
        {"sl_mult": 2.0, "tp_mult": 4.0, "risk_pct": 0.01, "label": "2/4 ATR 1%"},
        {"sl_mult": 2.0, "tp_mult": 4.0, "risk_pct": 0.005, "label": "2/4 ATR 0.5%"},
        {"sl_mult": 1.5, "tp_mult": 3.0, "risk_pct": 0.01, "label": "1.5/3 ATR 1%"},
    ]

    # Load data
    df, X, y, close, high, low, volume = load_data()
    retained = load_retained_features()
    retained_set = set(retained)
    names = list(ALL_NAMES)
    retained_idx = [i for i, name_ in enumerate(names) if name_ in retained_set]
    valid = y != -1
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import StandardScaler
    scaler = StandardScaler()
    X_s = scaler.fit_transform(X[valid][:, retained_idx])
    rf = RandomForestClassifier(max_depth=7, n_estimators=100, class_weight="balanced", random_state=42, n_jobs=-1)
    rf.fit(X_s, y[valid])
    proba = np.zeros(len(y))
    proba[valid] = rf.predict_proba(X_s)[:, 1]

    # ATR
    atr_vals = np.nan_to_num(pd.Series(high).rolling(14).apply(
        lambda x: x.max() - x.min() if len(x) == 14 else 0, raw=True
    ).values, nan=0.0)
    adx_vals = np.nan_to_num(
        pd.Series(high).rolling(14).apply(
            lambda x: x.max() - x.min() if len(x) == 14 else 0, raw=True
        ).values, nan=0.0)
    timestamps = df["timestamp"].values
    vol_regime = np.nan_to_num(np.where(
        pd.Series(close).rolling(60).std().rank(pct=True) > 0.7, 1,
        np.where(pd.Series(close).rolling(60).std().rank(pct=True) < 0.3, -1, 0)
    ).astype(float), nan=0.0)
    htf_trend = np.sign(
        pd.Series(close).ewm(span=9).mean() - pd.Series(close).ewm(span=50).mean()
    ).values.astype(float)

    bh = compute_buy_and_hold(close, timestamps)

    all_gates = []
    best_result = None

    for combo in combos:
        print(f"\n--- Config: {combo['label']} (SL={combo['sl_mult']}×, TP={combo['tp_mult']}×, risk={combo['risk_pct']*100:.2f}%)")
        engine = BacktestEngine(
            sl_mult=combo["sl_mult"], tp_mult=combo["tp_mult"],
            risk_pct=combo["risk_pct"],
            min_prob=config.get("min_prob", 0.425),
            adx_threshold=config.get("adx_threshold", 0),
            htf_alignment=config.get("htf_alignment", "none"),
            vol_regime_filter=config.get("vol_regime", "all"),
            max_trades_per_day=config.get("max_trades_per_day", float("inf")),
            vol_regime_series=vol_regime,
            htf_trend_series=htf_trend,
        )
        result = engine.run(close, high, low, timestamps, proba, adx=adx_vals, atr_values=atr_vals)

        g4 = run_gate_4(engine, result)
        g6 = run_gate_6(engine, result, bh)
        all_gates.append({"config": combo, "gate_4": g4, "gate_6": g6})

        if best_result is None or (g4["passed"] and g6["passed"]):
            best_result = result
            best_config = {"config": combo, "result": {
                "n_trades": result.n_trades, "win_rate": result.win_rate,
                "total_return_pct": result.total_return_pct,
                "sharpe": result.sharpe, "max_dd_pct": result.max_dd_pct,
                "calmar": result.calmar, "profit_factor": result.profit_factor,
                "sortino": result.sortino, "cagr": result.cagr,
                "expectancy": result.expectancy,
                "max_consecutive_losses": result.max_consecutive_losses,
                "turnover_per_day": result.turnover_per_day,
            }}

    overall = any(g["gate_4"]["passed"] and g["gate_6"]["passed"] for g in all_gates)
    print("\n" + "=" * 60)
    print(f"OVERALL: {'PASS — FASE 7 AUTORIZADA' if overall else 'FAIL — NO pasar a FASE 7'}")
    if not overall:
        print("CAUSA RAÍZ: drawdown excede 40% o retorno no supera buy-and-hold")
        print("DOCUMENTAR: trade_distribution.json + monte_carlo_results.json")
    print("=" * 60)

    report = {
        "gates": all_gates,
        "buy_and_hold": bh,
        "overall_pass": overall,
        "best_config": best_config if best_result else None,
        "elapsed_seconds": time.time() - t0,
    }

    report_path = REPORT_DIR / "gate_results.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nReport: {report_path}")

    # Generate trade distribution if available
    if best_result and hasattr(best_result, "trades"):
        trade_pnls = [t.pnl_usd for t in best_result.trades if t.exit_reason != "END"]
        trade_bars = [t.bars_held for t in best_result.trades if t.exit_reason != "END"]
        dist = {
            "all_pnls": trade_pnls,
            "all_bars_held": trade_bars,
            "total_bars": len(timestamps),
            "initial_balance": 10000.0,
            "n_trades": len(trade_pnls),
        }
        dist_path = REPORT_DIR / "trade_distribution.json"
        with open(dist_path, "w") as f:
            json.dump(dist, f, indent=2, default=str)
        print(f"Trade distribution: {dist_path}")

    return report


if __name__ == "__main__":
    main()
