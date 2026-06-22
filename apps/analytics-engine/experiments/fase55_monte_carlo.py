"""FASE 5.5.3.5 — Monte Carlo Stress Test.

Take the trade sequence from the best config and generate 1000
random permutations to validate strategy robustness.

Metrics: CAGR distribution, DD distribution, ruin probability.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR / "experiments"))

REPORT_DIR = BASE_DIR / "reports" / "backtest"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def run_monte_carlo(
    trade_pnls: list[float],
    trade_bars: list[int],
    total_bars: int,
    initial_balance: float = 10000.0,
    n_permutations: int = 1000,
    confidence: float = 0.95,
) -> dict:
    """Run Monte Carlo simulation by permuting trade order."""
    print(f"\nRunning {n_permutations} Monte Carlo permutations...")
    pnls = np.array(trade_pnls)
    bars = np.array(trade_bars)
    n_trades = len(pnls)

    cagrs = []
    max_dds = []
    ruin_count = 0

    for seed in range(n_permutations):
        rng = np.random.RandomState(seed)
        perm = rng.permutation(n_trades)
        perm_pnls = pnls[perm]

        balance = initial_balance
        peak = balance
        max_dd = 0.0
        for pnl in perm_pnls:
            balance += pnl
            peak = max(peak, balance)
            dd = (peak - balance) / peak * 100
            max_dd = max(max_dd, dd)

        years = total_bars / (365 * 24)
        cagr = ((balance / initial_balance) ** (1 / years) - 1) * 100 if years > 0 else 0.0
        cagrs.append(cagr)
        max_dds.append(max_dd)
        if max_dd > 99:
            ruin_count += 1

    cagrs = np.array(cagrs)
    max_dds = np.array(max_dds)

    # Confidence intervals
    lower_pct = (1 - confidence) / 2 * 100
    upper_pct = (1 + confidence) / 2 * 100

    results = {
        "n_permutations": n_permutations,
        "n_trades": n_trades,
        "total_bars": total_bars,
        "initial_balance": initial_balance,
        "confidence_level": confidence,
        "cagr": {
            "mean": float(np.mean(cagrs)),
            "std": float(np.std(cagrs)),
            "median": float(np.median(cagrs)),
            "p5": float(np.percentile(cagrs, 5)),
            "p95": float(np.percentile(cagrs, 95)),
            "ci_lower": float(np.percentile(cagrs, lower_pct)),
            "ci_upper": float(np.percentile(cagrs, upper_pct)),
            "min": float(np.min(cagrs)),
            "max": float(np.max(cagrs)),
        },
        "max_drawdown": {
            "mean": float(np.mean(max_dds)),
            "std": float(np.std(max_dds)),
            "median": float(np.median(max_dds)),
            "p5": float(np.percentile(max_dds, 5)),
            "p95": float(np.percentile(max_dds, 95)),
            "ci_lower": float(np.percentile(max_dds, lower_pct)),
            "ci_upper": float(np.percentile(max_dds, upper_pct)),
            "min": float(np.min(max_dds)),
            "max": float(np.max(max_dds)),
        },
        "ruin_probability": float(ruin_count / n_permutations),
    }

    print(f"  CAGR mean: {results['cagr']['mean']:.2f}% (p5: {results['cagr']['p5']:.2f}%, p95: {results['cagr']['p95']:.2f}%)")
    print(f"  Max DD mean: {results['max_drawdown']['mean']:.2f}% (p5: {results['max_drawdown']['p5']:.2f}%, p95: {results['max_drawdown']['p95']:.2f}%)")
    print(f"  Ruin probability: {results['ruin_probability']:.1%}")

    return results


def load_best_config_trades() -> tuple[list[float], list[int], int, float]:
    """Load trade data from filter grid results."""
    grid_path = REPORT_DIR / "filter_grid_results.json"
    if not grid_path.exists():
        print("No filter grid results found, using fallback...")
        return [100, -50, 200, -30, 150], [5, 3, 8, 2, 6], 50000, 10000.0

    with open(grid_path) as f:
        data = json.load(f)

    best = data.get("best_config") or data.get("top_10_b", [{}])[0]
    print(f"Best config: prob={best.get('min_prob')}, ADX={best.get('adx_threshold')}")

    return [], [], 0, 10000.0  # placeholder — real trades loaded externally


def main():
    t0 = time.time()
    print("=" * 60)
    print("FASE 5.5.3.5 — Monte Carlo Stress Test")
    print("=" * 60)

    # Try to load from trade_distribution
    dist_path = REPORT_DIR / "trade_distribution.json"
    if dist_path.exists():
        with open(dist_path) as f:
            dist = json.load(f)
        trade_pnls = dist.get("all_pnls", [])
        trade_bars = dist.get("all_bars_held", [])
        total_bars = dist.get("total_bars", 39049)
        initial_balance = dist.get("initial_balance", 10000.0)
    else:
        trade_pnls, trade_bars, total_bars, initial_balance = load_best_config_trades()

    if not trade_pnls:
        print("WARNING: no trade data available. Run trade_filters first.")
        return

    results = run_monte_carlo(
        trade_pnls, trade_bars, total_bars, initial_balance,
        n_permutations=1000,
    )

    report_path = REPORT_DIR / "monte_carlo_results.json"
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nReport: {report_path}")
    print(f"Elapsed: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
