"""Phase 5 — Portfolio Validation: summary and verdicts.

Reads per-symbol predictions (parquet), computes equal-weight portfolio, correlations,
and diversification/concentration assessments.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

from experiments.quant_validation_v2_phase5.common import (
    compute_trade_returns,
    compute_portfolio_metrics,
    compute_correlations,
    load_predictions_parquet,
    save_report,
    load_report,
    logger,
    REPORT_DIR,
)

SYMBOLS = ["BTC", "ETH", "SOL"]
COST_PER_SIDE = 0.0  # Phase 5: no costs


def merge_portfolio_returns(
    returns_dict: dict[str, pd.Series],
) -> pd.DataFrame:
    """Align per-asset returns by timestamp (outer join) and compute equal-weight portfolio.

    Returns DataFrame with columns per asset + 'portfolio' (mean).
    """
    df = pd.DataFrame(returns_dict)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df["portfolio"] = df.mean(axis=1)
    return df


def main():
    logger.info("╔══════════════════════════════════════╗")
    logger.info("║  Phase 5 — Summary & Verdicts         ║")
    logger.info("╚══════════════════════════════════════╝")
    t0 = time.time()

    # ── 1. Load predictions ──────────────────────────────────────
    predictions = {}
    for sym in SYMBOLS:
        df = load_predictions_parquet(f"{sym.lower()}_predictions.parquet")
        if df is not None and len(df) > 0:
            predictions[sym] = df
            logger.info(f"  {sym}: {len(df)} trades, "
                        f"y_pred=1: {int((df['y_pred'] == 1).sum())}, "
                        f"y_pred=0: {int((df['y_pred'] == 0).sum())}")
        else:
            logger.warning(f"  {sym}: no predictions found")

    if not predictions:
        logger.error("No predictions loaded — run run.py first")
        return

    # ── 2. Per-symbol returns ────────────────────────────────────
    indiv_returns = {}
    indiv_metrics = {}
    for sym, df in predictions.items():
        ret = compute_trade_returns(df, cost_per_side=COST_PER_SIDE)
        indiv_returns[sym] = ret
        m = compute_portfolio_metrics(ret, name=f"{sym}_no_cost")
        indiv_metrics[sym] = m
        logger.info(f"  {sym} Sharpe={m.get('sharpe', 'N/A')}, CAGR={m.get('cagr', 'N/A'):.4%}, "
                    f"WinRate={m.get('win_rate', 'N/A'):.2%}")

    # ── 3. Equal-weight portfolio ────────────────────────────────
    portfolio_df = merge_portfolio_returns(indiv_returns)
    portfolio_ret = portfolio_df["portfolio"]
    portfolio_metrics = compute_portfolio_metrics(portfolio_ret, name="equal_weight_portfolio")

    logger.info(f"  Portfolio Sharpe={portfolio_metrics.get('sharpe', 'N/A')}, "
                f"CAGR={portfolio_metrics.get('cagr', 'N/A'):.4%}, "
                f"WinRate={portfolio_metrics.get('win_rate', 'N/A'):.2%}")

    # ── 4. Correlations ─────────────────────────────────────────
    corr = compute_correlations(predictions)
    logger.info(f"  Mean signal corr: {corr.get('mean_signal_corr', 'N/A')}")
    logger.info(f"  Mean return corr: {corr.get('mean_return_corr', 'N/A')}")

    # ── 5. Best / worst asset ───────────────────────────────────
    shs = {sym: m.get("sharpe", -999) for sym, m in indiv_metrics.items() if m.get("status", "") != "insufficient_data"}
    best = max(shs, key=shs.get) if shs else None
    worst = min(shs, key=shs.get) if shs else None
    best_sharpe = shs.get(best, None)
    worst_sharpe = shs.get(worst, None)

    logger.info(f"  Best asset:  {best} (Sharpe={best_sharpe})")
    logger.info(f"  Worst asset: {worst} (Sharpe={worst_sharpe})")

    # ── 6. Verdicts ─────────────────────────────────────────────
    port_sharpe = portfolio_metrics.get("sharpe", None)
    mean_indiv_sharpe = float(np.mean(list(shs.values()))) if shs else None
    min_indiv_sharpe = min(shs.values()) if shs else None

    diversification_benefit = None
    concentration_risk = None

    if port_sharpe is not None and mean_indiv_sharpe is not None:
        if port_sharpe > mean_indiv_sharpe:
            diversification_benefit = True
            logger.info("  ✓ DIVERSIFICATION BENEFIT: portfolio Sharpe > mean(individual)")
        else:
            diversification_benefit = False
            logger.info("  ✗ NO diversification benefit: portfolio Sharpe ≤ mean(individual)")

    if port_sharpe is not None and min_indiv_sharpe is not None:
        if port_sharpe < min_indiv_sharpe:
            concentration_risk = True
            logger.warning("  ⚠ CONCENTRATION RISK: portfolio Sharpe < best single asset")
        else:
            concentration_risk = False
            logger.info("  ✓ No concentration risk: portfolio Sharpe ≥ min(individual)")

    # ── 7. Build summary ────────────────────────────────────────
    summary_metrics = {}
    for sym in SYMBOLS:
        if sym in indiv_metrics:
            summary_metrics[sym] = {k: v for k, v in indiv_metrics[sym].items()
                                    if k != "name"}

    summary = {
        "phase": "quant_validation_v2_phase5",
        "protocol": {
            "lookahead": 5,
            "stride": 5,
            "embargo": 1,
            "cost_per_side": COST_PER_SIDE,
            "symbols": SYMBOLS,
        },
        "correlations": {
            "mean_signal_spearman": corr.get("mean_signal_corr"),
            "mean_return_spearman": corr.get("mean_return_corr"),
            "pairwise_signal": corr.get("signal_correlation"),
            "pairwise_return": corr.get("return_correlation"),
        },
        "best_asset": {"symbol": best, "sharpe": best_sharpe},
        "worst_asset": {"symbol": worst, "sharpe": worst_sharpe},
        "individual_metrics": summary_metrics,
        "portfolio_metrics": {k: v for k, v in portfolio_metrics.items() if k != "name"},
        "verdicts": {
            "diversification_benefit": diversification_benefit,
            "concentration_risk": concentration_risk,
            "portfolio_sharpe": port_sharpe,
            "mean_individual_sharpe": mean_indiv_sharpe,
            "min_individual_sharpe": min_indiv_sharpe,
        },
        "elapsed_s": round(time.time() - t0, 1),
    }

    save_report(summary, "summary.json")
    logger.info(f"\nPhase 5 summary saved — elapsed: {summary['elapsed_s']}s")

    # ── 8. Portfolio equity curve ────────────────────────────────
    eq = (1.0 + portfolio_ret).cumprod()
    eq.name = "portfolio"
    eq_path = REPORT_DIR / "portfolio_equity.csv"
    pd.DataFrame({"equity": eq}).to_csv(eq_path)
    logger.info(f"  Equity curve saved to {eq_path}")

    # ── 9. Per-asset equity curves ──────────────────────────────
    eqs = {}
    for sym, ret in indiv_returns.items():
        eq_sym = (1.0 + ret).cumprod()
        eq_sym.name = f"equity_{sym}"
        eqs[sym] = eq_sym
    eq_df = pd.DataFrame(eqs)
    eq_df.to_csv(REPORT_DIR / "per_asset_equity.csv")
    logger.info(f"  Per-asset equity curves saved")

    logger.info(f"\n{'='*60}")
    logger.info(f"Phase 5 Summary COMPLETE")
    logger.info(f"{'='*60}")


if __name__ == "__main__":
    main()
