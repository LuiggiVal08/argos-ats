"""Phase 13 — Monte Carlo Simulation.

Shuffle the trade sequence from Phase 11 (7007 trades) via random permutation,
recompute equity curve and metrics for each permutation.
Adds: Ulcer Index, Recovery Factor, Time Under Water.
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("phase13")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

PHASE11_DIR = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v2_phase11"
REPORT_DIR = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v2_phase13"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

INITIAL_CAPITAL = 100_000.0
N_PERMUTATIONS = 1000
BASE_SEED = 42


def compute_metrics_from_trades(trades: list[dict], label: str) -> dict:
    """Compute portfolio metrics from a shuffled list of trades.

    Capital follows a simple model: allocate size at entry, return at exit.
    MTM is approximated by daily PnL aggregation.
    """
    if len(trades) == 0:
        return {"label": label, "n_trades": 0, "total_return": 0.0, "sharpe": 0.0}

    # Simulate sequential capital (no MTM between bars in MC)
    equity_curve = [INITIAL_CAPITAL]
    balance = INITIAL_CAPITAL
    for t in trades:
        # Capital committed is 'size', entry cost deducted
        committed = t["size"] + t["entry_cost"]
        if balance >= committed:
            balance -= committed
            # At exit: return capital + PnL - exit cost
            balance += t["size"] + t["entry_cost"] + t["net_pnl"]
        equity_curve.append(balance)

    equity = np.array(equity_curve)

    # Daily returns: we don't have timestamps in MC, so simulate daily
    # by assuming trades are evenly distributed across days
    # Use the number of trades as a proxy for timeline
    n_days_sim = max(len(equity_curve) // 3, 252)  # ~3 trades/day -> days
    daily_returns = []
    for i in range(1, len(equity)):
        daily_ret = equity[i] / equity[i - 1] - 1.0
        daily_returns.append(daily_ret)

    daily_ret_arr = np.array(daily_returns)
    mean_d = daily_ret_arr.mean()
    std_d = daily_ret_arr.std()

    sharpe = mean_d / std_d * np.sqrt(365) if std_d > 1e-10 else 0.0
    downside = daily_ret_arr[daily_ret_arr < 0]
    sortino = mean_d / (downside.std() + 1e-10) * np.sqrt(365) if len(downside) > 1 else 0.0

    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    max_dd = float(dd.min())

    # "Years" based on n_trades / avg_trades_per_day
    # Phase 11 has 7007 trades over ~1600 days ≈ 4.38 trades/day
    years = len(trades) / (4.38 * 365)
    years = max(years, 0.1)
    total_return = float(equity[-1] / equity[0] - 1.0) if equity[0] > 0 else 0.0
    cagr = (equity[-1] / equity[0]) ** (1.0 / years) - 1.0 if equity[0] > 0 else 0.0
    calmar = cagr / abs(max_dd) if max_dd < 0 else 0.0

    # Trade-level stats
    net_pnls = np.array([t["net_pnl"] for t in trades])
    wins = net_pnls[net_pnls > 0]
    losses = net_pnls[net_pnls <= 0]
    win_rate = len(wins) / len(net_pnls)
    profit_factor = abs(wins.sum()) / abs(losses.sum() + 1e-10)

    # Ulcer
    dd_sq = dd ** 2
    ulcer = np.sqrt(dd_sq.mean())

    # Recovery Factor
    total_gain = float(equity[-1] - equity[0])
    max_dd_abs = float(np.max(np.abs(dd)) * equity[0])
    recovery_factor = total_gain / max_dd_abs if max_dd_abs > 1e-10 else 0.0

    # Time Under Water
    underwater = equity < peak
    tuw_pct = underwater.mean() * 100

    return {
        "label": label,
        "final_equity": round(float(equity[-1]), 2),
        "total_return": round(total_return, 6),
        "cagr": round(cagr, 6),
        "sharpe": round(sharpe, 6),
        "sortino": round(sortino, 6),
        "calmar": round(calmar, 6),
        "max_drawdown": round(max_dd, 6),
        "ulcer_index": round(float(ulcer), 6),
        "recovery_factor": round(recovery_factor, 6),
        "time_under_water_pct": round(float(tuw_pct), 4),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4),
        "n_trades": len(trades),
    }


def run_monte_carlo():
    logger.info("╔══════════════════════════════════════╗")
    logger.info("║  Phase 13 — Monte Carlo Simulation   ║")
    logger.info(f"║  Permutations: {N_PERMUTATIONS}                     ║")
    logger.info("╚══════════════════════════════════════╝")
    t0 = time.time()

    # Load trades from Phase 11
    trade_path = PHASE11_DIR / "trade_log.parquet"
    if not trade_path.exists():
        logger.error(f"Trade log not found: {trade_path}")
        # Fall back to Phase 12 trade logs
        trade_path = PHASE11_DIR.parent / "quant_validation_v2_phase12" / "trade_log.parquet"
        if not trade_path.exists():
            logger.error("No trade log found anywhere")
            return

    df_trades = pd.read_parquet(trade_path)
    logger.info(f"[trades] loaded {len(df_trades)} trades from {trade_path}")

    required_cols = ["size", "entry_cost", "net_pnl"]
    missing = [c for c in required_cols if c not in df_trades.columns]
    if missing:
        # Create from available data
        if "entry_price" in df_trades.columns and "exit_price" in df_trades.columns:
            logger.warning(f"[warn] missing columns: {missing}, inferring net_pnl")
            df_trades["gross_return"] = df_trades["exit_price"] / df_trades["entry_price"] - 1.0
            side_sign = np.where(df_trades["side"] == "LONG", 1, -1)
            df_trades["gross_pnl"] = df_trades["size"] * df_trades["gross_return"] * side_sign
            df_trades["entry_cost"] = df_trades["size"] * 0.00155
            df_trades["exit_cost"] = df_trades["size"] * 0.00155
            df_trades["net_pnl"] = df_trades["gross_pnl"] - df_trades["entry_cost"] - df_trades["exit_cost"]
        else:
            logger.error("Cannot compute net_pnl — missing entry_price/exit_price")
            return

    trades_raw = df_trades.to_dict("records")
    n_trades = len(trades_raw)
    logger.info(f"[mc] {n_trades} trades available for permutation")

    # Reference: original sequence metrics
    ref = compute_metrics_from_trades(trades_raw, "reference")
    logger.info(f"[ref] Sharpe={ref['sharpe']:.4f}  CAGR={ref['cagr']:.4%}  "
                f"MaxDD={ref['max_drawdown']:.4%}  "
                f"Return={ref['total_return']:.4f}x")

    # Run permutations
    all_metrics: list[dict] = [ref]
    rng = np.random.default_rng(BASE_SEED)
    last_report = time.time()

    for perm in range(N_PERMUTATIONS):
        perm_indices = rng.permutation(n_trades)
        shuffled = [trades_raw[i] for i in perm_indices]
        m = compute_metrics_from_trades(shuffled, f"perm_{perm}")
        all_metrics.append(m)

        if (perm + 1) % 100 == 0 or time.time() - last_report > 30:
            elapsed = time.time() - t0
            logger.info(f"  [{perm+1}/{N_PERMUTATIONS}]  "
                        f"elapsed={elapsed:.0f}s  "
                        f"avg_sharpe={sum(mm['sharpe'] for mm in all_metrics[1:]) / max(len(all_metrics)-1, 1):.4f}")
            last_report = time.time()

    elapsed = time.time() - t0
    logger.info(f"[mc] done: {elapsed:.0f}s  {N_PERMUTATIONS} permutations")

    # Summary statistics for all permutations (exclude reference)
    perm_metrics = all_metrics[1:]
    fields = ["final_equity", "total_return", "cagr", "sharpe", "sortino",
              "calmar", "max_drawdown", "ulcer_index", "recovery_factor",
              "time_under_water_pct", "win_rate", "profit_factor"]

    summary = {"reference": ref, "n_permutations": N_PERMUTATIONS}
    for f in fields:
        vals = [m[f] for m in perm_metrics]
        summary[f] = {
            "mean": round(float(np.mean(vals)), 6),
            "median": round(float(np.median(vals)), 6),
            "std": round(float(np.std(vals)), 6),
            "p5": round(float(np.percentile(vals, 5)), 6),
            "p25": round(float(np.percentile(vals, 25)), 6),
            "p75": round(float(np.percentile(vals, 75)), 6),
            "p95": round(float(np.percentile(vals, 95)), 6),
            "min": round(float(np.min(vals)), 6),
            "max": round(float(np.max(vals)), 6),
        }

    # Beyond-chance test: what fraction of permutations beat reference?
    for f in fields:
        ref_val = ref[f]
        if f in ("max_drawdown", "ulcer_index", "time_under_water_pct"):
            # Lower is better — count permutations with value LOWER than reference
            beat_ref = sum(1 for m in perm_metrics if m[f] < ref_val)
        else:
            # Higher is better — count permutations with value HIGHER than reference
            beat_ref = sum(1 for m in perm_metrics if m[f] > ref_val)
        summary[f"{f}_pct_better_than_ref"] = round(beat_ref / N_PERMUTATIONS, 4)

    # Save
    out_path = REPORT_DIR / "montecarlo_results.json"
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    logger.info(f"[report] saved {out_path}")

    logger.info(f"\nReference: Sharpe={ref['sharpe']:.4f}  CAGR={ref['cagr']:.4%}  "
                f"MaxDD={ref['max_drawdown']:.4%}")
    logger.info(f"Permutations (median): Sharpe={summary['sharpe']['median']:.4f}  "
                f"CAGR={summary['cagr']['median']:.4%}")
    logger.info(f"Sharpe > ref: {summary['sharpe_pct_better_than_ref']:.1%}  "
                f"CAGR > ref: {summary['cagr_pct_better_than_ref']:.1%}")
    logger.info("Phase 13 complete")

    return summary


def main():
    try:
        run_monte_carlo()
    except Exception as e:
        logger.error(f"Phase 13 FAILED: {e}")
        logger.error(traceback.format_exc())


if __name__ == "__main__":
    main()
