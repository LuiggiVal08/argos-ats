"""FASE 6.5.2 — Monte Carlo per Fold.

Reads trade PnLs from walkforward and runs 1000 permutations per fold.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

BASE_DIR = Path(__file__).parent.parent

WF_DIR = BASE_DIR / "reports" / "walkforward"
REPORT_DIR = BASE_DIR / "reports" / "montecarlo_fold"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def run_monte_carlo(trade_pnls: list[float], n_test_bars: int,
                    n_permutations: int = 1000, initial_balance: float = 10000.0) -> dict:
    """Run Monte Carlo simulation by permuting trade order."""
    pnls = np.array(trade_pnls)
    n_trades = len(pnls)
    if n_trades == 0:
        return {"error": "no trades"}

    years = n_test_bars / (365 * 24)
    cagrs = []
    max_dds = []
    ruin_count = 0

    for seed in range(n_permutations):
        rng = np.random.RandomState(seed)
        perm_pnls = pnls[rng.permutation(n_trades)]

        balance = initial_balance
        peak = balance
        max_dd = 0.0
        for pnl in perm_pnls:
            balance += pnl
            peak = max(peak, balance)
            dd = (peak - balance) / peak * 100
            max_dd = max(max_dd, dd)

        cagr = ((balance / initial_balance) ** (1 / years) - 1) * 100 if years > 0 else 0.0
        cagrs.append(cagr)
        max_dds.append(max_dd)
        if max_dd > 99:
            ruin_count += 1

    cagrs = np.array(cagrs)
    max_dds = np.array(max_dds)

    results = {
        "n_trades": n_trades,
        "n_test_bars": n_test_bars,
        "n_permutations": n_permutations,
        "cagr": {
            "mean": float(np.mean(cagrs)),
            "std": float(np.std(cagrs)),
            "median": float(np.median(cagrs)),
            "p5": float(np.percentile(cagrs, 5)),
            "p95": float(np.percentile(cagrs, 95)),
        },
        "max_drawdown": {
            "mean": float(np.mean(max_dds)),
            "std": float(np.std(max_dds)),
            "median": float(np.median(max_dds)),
            "p5": float(np.percentile(max_dds, 5)),
            "p95": float(np.percentile(max_dds, 95)),
        },
        "ruin_probability": float(ruin_count / n_permutations),
    }
    return results


def main():
    t0 = time.time()
    print("=" * 60)
    print("FASE 6.5.2 — Monte Carlo per Fold")
    print("=" * 60)

    for fold_id in range(4):
        trades_path = WF_DIR / f"fold_{fold_id}_trades.json"
        if not trades_path.exists():
            print(f"\nFold {fold_id}: no trade data found at {trades_path}")
            continue

        with open(trades_path) as f:
            data = json.load(f)

        pnls = data.get("trade_pnls", [])
        n_bars = data.get("n_test_bars", 0)
        print(f"\nFold {fold_id}: {len(pnls)} trades, {n_bars} test bars")

        mc = run_monte_carlo(pnls, n_bars)

        if "error" in mc:
            print(f"  ERROR: {mc['error']}")
            continue

        print(f"  CAGR mean: {mc['cagr']['mean']:.2f}%")
        print(f"  CAGR p5:   {mc['cagr']['p5']:.2f}%")
        print(f"  CAGR p95:  {mc['cagr']['p95']:.2f}%")
        print(f"  DD mean:   {mc['max_drawdown']['mean']:.2f}%")
        print(f"  DD p95:    {mc['max_drawdown']['p95']:.2f}%")
        print(f"  Ruin prob: {mc['ruin_probability']:.1%}")

        mc_path = REPORT_DIR / f"fold_{fold_id}.json"
        with open(mc_path, "w") as f:
            json.dump(mc, f, indent=2, default=str)
        print(f"  Saved: {mc_path}")

    print(f"\nElapsed: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
