"""Phase 8 — Capacity & Friction Stress.

Subjects the portfolio signal to 6 stress scenarios to estimate capacity and
execution resilience.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("phase8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

PHASE5_REPORT_DIR = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v2_phase5"
REPORT_DIR = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v2_phase8"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = ["BTC", "ETH", "SOL"]

# Base costs
BASE_FEE = 0.0010
BASE_SLIPPAGE = 0.0005
BASE_SPREAD_HALF = 0.00005
BASE_COST_PER_SIDE = BASE_FEE + BASE_SLIPPAGE + BASE_SPREAD_HALF  # 0.00155
BASE_ROUND_TRIP = BASE_COST_PER_SIDE * 2  # 0.0031


def load_predictions_parquet(filename: str) -> pd.DataFrame | None:
    path = PHASE5_REPORT_DIR / filename
    if not path.exists():
        logger.warning(f"[parquet] not found: {path}")
        return None
    df = pd.read_parquet(path)
    return df


def save_report(data: dict, filename: str) -> Path:
    path = REPORT_DIR / filename
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    logger.info(f"[report] saved {path}")
    return path


BUY_THRESHOLD = 0.60
SELL_THRESHOLD = 0.40


def apply_thresholds(df: pd.DataFrame) -> pd.DataFrame:
    pos = np.zeros(len(df), dtype=np.int8)
    pos[df["y_proba"].values > BUY_THRESHOLD] = 1
    pos[df["y_proba"].values < SELL_THRESHOLD] = -1
    df = df.copy()
    df["position"] = pos
    return df


def compute_trade_returns(
    df: pd.DataFrame,
    cost_round_trip: float = BASE_ROUND_TRIP,
    delay_bars: int = 0,
    invert: bool = False,
) -> pd.Series:
    """Compute P&L with configurable costs, delay, and inversion.

    Args:
        df: must have 'position', 'forward_return', 'timestamp'
        cost_round_trip: fraction deducted per round-trip trade
        delay_bars: 0 = same-bar, 1 = one bar late
        invert: True = trade opposite of model signal
    """
    pos = df["position"].values.copy()
    if invert:
        pos = -pos

    # Filter to traded rows
    traded_mask = pos != 0

    if delay_bars > 0:
        fwd = df["forward_return"].shift(-delay_bars).values
    else:
        fwd = df["forward_return"].values

    ret = np.zeros(len(df))
    long_mask = traded_mask & (pos == 1)
    short_mask = traded_mask & (pos == -1)
    ret[long_mask] = fwd[long_mask] - cost_round_trip
    ret[short_mask] = -fwd[short_mask] - cost_round_trip

    return pd.Series(ret, index=pd.to_datetime(df["timestamp"])).sort_index()


def compute_portfolio_metrics(
    trade_returns: pd.Series,
    name: str = "portfolio",
    annual_factor: float = 365.0,
) -> dict:
    if len(trade_returns) < 10:
        return {"name": name, "status": "insufficient_data", "n_trades": len(trade_returns)}

    daily = trade_returns.resample("D").sum().dropna()
    if len(daily) < 5:
        return {"name": name, "status": "insufficient_data", "n_trades": len(trade_returns), "n_days": len(daily)}

    equity = (1.0 + daily).cumprod()
    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    n_days = len(daily)
    years = n_days / annual_factor
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0 if years > 0 and equity.iloc[0] > 0 else 0.0
    mean_d = daily.mean()
    std_d = daily.std()
    sharpe = (mean_d / std_d * np.sqrt(annual_factor)) if std_d > 1e-10 else 0.0
    downside = daily[daily < 0]
    downside_std = downside.std() if len(downside) > 1 else 0.0
    sortino = (mean_d / downside_std * np.sqrt(annual_factor)) if downside_std > 1e-10 else 0.0
    peak = equity.cummax()
    dd = (equity - peak) / peak
    max_dd = float(dd.min())
    calmar = cagr / abs(max_dd) if max_dd < 0 else 0.0
    wins = (trade_returns > 0).sum()
    total = len(trade_returns)
    win_rate = wins / total if total > 0 else 0.0
    gains = trade_returns[trade_returns > 0].sum()
    losses = trade_returns[trade_returns < 0].sum()
    profit_factor = float(gains / abs(losses)) if abs(losses) > 1e-10 else 0.0

    return {
        "name": name,
        "n_trades": int(total),
        "n_days": int(n_days),
        "total_return": round(total_return, 6),
        "cagr": round(cagr, 6),
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4),
        "calmar": round(calmar, 4),
        "max_dd": round(max_dd, 6),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4),
    }


# ── stress scenarios ─────────────────────────────────────────────


STRESS_SCENARIOS = {
    "base": {"cost_round_trip": BASE_ROUND_TRIP, "delay_bars": 0, "invert": False},
    "slippage_2x": {"cost_round_trip": BASE_ROUND_TRIP + 2 * BASE_SLIPPAGE, "delay_bars": 0, "invert": False},
    "delay_1bar": {"cost_round_trip": BASE_ROUND_TRIP, "delay_bars": 1, "invert": False},
    "cost_3x": {"cost_round_trip": BASE_ROUND_TRIP * 3, "delay_bars": 0, "invert": False},
    "combined": {"cost_round_trip": BASE_ROUND_TRIP * 3, "delay_bars": 1, "invert": False},
    "adverse_selection": {"cost_round_trip": BASE_ROUND_TRIP, "delay_bars": 0, "invert": True},
}


def run_stress(scenario_name: str, params: dict) -> dict:
    logger.info(f"  Stress: {scenario_name}")
    port_returns = []
    for sym in SYMBOLS:
        df = load_predictions_parquet(f"{sym.lower()}_predictions.parquet")
        if df is None or len(df) < 100:
            continue
        df = apply_thresholds(df)
        traded = df[df["position"] != 0]
        if len(traded) < 10:
            continue
        ret = compute_trade_returns(traded, **params)
        ret.name = sym
        port_returns.append(ret)

    if not port_returns:
        return {"name": scenario_name, "status": "no_data"}

    portfolio_df = pd.DataFrame(port_returns).T
    portfolio_df["portfolio"] = portfolio_df.mean(axis=1)
    port_metrics = compute_portfolio_metrics(portfolio_df["portfolio"], name=scenario_name)
    logger.info(f"    Sharpe={port_metrics.get('sharpe', 'N/A')}  "
                f"CAGR={port_metrics.get('cagr', 0):.4%}  "
                f"MaxDD={port_metrics.get('max_dd', 0):.4%}")
    return port_metrics


def main():
    logger.info("╔══════════════════════════════════════╗")
    logger.info("║  Phase 8 — Capacity & Friction Stress ║")
    logger.info("║  6 scenarios                        ║")
    logger.info("╚══════════════════════════════════════╝")

    stress_results = {}
    for name, params in STRESS_SCENARIOS.items():
        stress_results[name] = run_stress(name, params)

    # ── Resilience Index ─────────────────────────────────────────
    shs = [m.get("sharpe", 0) for m in stress_results.values()
           if isinstance(m, dict) and m.get("status") != "no_data"]
    if shs:
        resilience = {
            "mean_sharpe": round(float(np.mean(shs)), 4),
            "median_sharpe": round(float(np.median(shs)), 4),
            "std_sharpe": round(float(np.std(shs)), 4),
            "min_sharpe": round(float(min(shs)), 4),
            "max_sharpe": round(float(max(shs)), 4),
            "n_scenarios": len(shs),
            "n_positive": sum(1 for s in shs if s > 0),
            "n_negative": sum(1 for s in shs if s < 0),
        }

        mean_sh = np.mean(shs)
        if mean_sh > 1.0:
            resilience["verdict"] = "RESILIENT"
        elif mean_sh > 0:
            resilience["verdict"] = "VULNERABLE"
        else:
            resilience["verdict"] = "BRITTLE"

        logger.info(f"\n  Resilience Index:")
        logger.info(f"    Mean Sharpe:   {resilience['mean_sharpe']:.2f}")
        logger.info(f"    Median Sharpe: {resilience['median_sharpe']:.2f}")
        logger.info(f"    Min Sharpe:    {resilience['min_sharpe']:.2f}")
        logger.info(f"    Verdict:       {resilience['verdict']}")
    else:
        resilience = {"status": "no_data"}

    output = {
        "phase": "quant_validation_v2_phase8",
        "base_cost_round_trip": BASE_ROUND_TRIP,
        "stress_scenarios": {
            k: {
                "label": k,
                "params": STRESS_SCENARIOS[k],
                "metrics": v,
            }
            for k, v in stress_results.items()
        },
        "resilience_index": resilience,
    }
    save_report(output, "stress.json")
    logger.info(f"\nPhase 8 complete — verdict: {resilience.get('verdict', 'N/A')}")


if __name__ == "__main__":
    main()
