"""ShadowMetrics: aggregates shadow outcomes into health metrics.

Designed to be called on-demand (e.g. via /shadow/metrics endpoint)
or periodically. Returns a dict with expectancy, accuracy, profit factor,
regime stability, and distribution drift.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any


def _ks_test_p_value(sample_a: list[float], sample_b: list[float]) -> float:
    """Quick two-sample KS-test approximation. Returns D-stat (0-1).
    0 = identical distributions, 1 = completely different.
    """
    if not sample_a or not sample_b:
        return 0.0
    combined = sorted(set(sample_a + sample_b))
    if not combined:
        return 0.0
    max_d = 0.0
    for v in combined:
        cdf_a = sum(1 for x in sample_a if x <= v) / len(sample_a)
        cdf_b = sum(1 for x in sample_b if x <= v) / len(sample_b)
        max_d = max(max_d, abs(cdf_a - cdf_b))
    return max_d


def compute_shadow_metrics(outcomes: list[dict]) -> dict[str, Any]:
    if not outcomes:
        return {"error": "no_outcomes", "total": 0}

    close_pnls = [float(o.get("close_pnl_pct", 0)) for o in outcomes]
    r_multiples = [float(o.get("r_multiple", 0)) for o in outcomes]
    mae_values = [float(o.get("mae_pct", 0)) for o in outcomes]
    mfe_values = [float(o.get("mfe_pct", 0)) for o in outcomes]
    actions = [o.get("action", "") for o in outcomes]
    regimes = [o.get("regime", "unknown") for o in outcomes]

    wins = [p for p in close_pnls if p > 0]
    losses = [p for p in close_pnls if p <= 0]

    gross_wins = sum(wins) if wins else 0.0
    gross_losses = abs(sum(losses)) if losses else 0.0
    profit_factor = round(gross_wins / gross_losses, 4) if gross_losses > 0 else float("inf")

    expectancy = round(sum(close_pnls) / len(close_pnls), 4) if close_pnls else 0.0
    avg_r = round(sum(r_multiples) / len(r_multiples), 4) if r_multiples else 0.0

    correct = sum(1 for o in outcomes if _is_direction_correct(o))
    accuracy = round(correct / len(outcomes), 4) if outcomes else 0.0

    action_dist = dict(Counter(actions))

    # Regime stability: expectancy per regime, variance between regimes
    regime_expectancies: dict[str, float] = {}
    for regime in set(regimes):
        r_pnls = [p for o, p in zip(outcomes, close_pnls) if o.get("regime") == regime]
        regime_expectancies[regime] = round(sum(r_pnls) / len(r_pnls), 4) if r_pnls else 0.0
    regime_stability = round(
        (max(regime_expectancies.values()) - min(regime_expectancies.values()))
        if len(regime_expectancies) > 1 else 0.0,
        4,
    )

    # Distribution drift: compare action distribution last 24h vs all
    # Since we may have limited data, use last 20 vs all
    drift_d = _ks_test_p_value(
        [p for o, p in zip(outcomes, close_pnls) if o.get("candle_ts", 0) < max(
            o.get("candle_ts", 0) for o in outcomes
        ) - 86400_000],
        [p for p in close_pnls[-20:]],
    )

    return {
        "total_outcomes": len(outcomes),
        "expectancy_pct": expectancy,
        "avg_r_multiple": avg_r,
        "accuracy_directional": accuracy,
        "profit_factor": profit_factor,
        "win_rate": round(len(wins) / len(close_pnls), 4) if close_pnls else 0.0,
        "action_distribution": action_dist,
        "avg_win_pct": round(sum(wins) / len(wins), 4) if wins else 0.0,
        "avg_loss_pct": round(sum(losses) / len(losses), 4) if losses else 0.0,
        "avg_mae_pct": round(sum(mae_values) / len(mae_values), 4) if mae_values else 0.0,
        "avg_mfe_pct": round(sum(mfe_values) / len(mfe_values), 4) if mfe_values else 0.0,
        "regime_expectancies": regime_expectancies,
        "regime_stability_pct": regime_stability,
        "drift_ks_d": round(drift_d, 4),
        "best_trade_pct": round(max(close_pnls), 4) if close_pnls else 0.0,
        "worst_trade_pct": round(min(close_pnls), 4) if close_pnls else 0.0,
    }


def _is_direction_correct(outcome: dict) -> bool:
    action = outcome.get("action", "")
    close_pnl = float(outcome.get("close_pnl_pct", 0))
    if action == "BUY":
        return close_pnl > 0
    if action == "SELL":
        return close_pnl > 0
    return False
