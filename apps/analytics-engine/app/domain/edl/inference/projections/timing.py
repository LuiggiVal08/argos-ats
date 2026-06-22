"""E_t — Timing Edge projection.

Measures regret-distance between actual entry times and the optimal
stopping distribution under H₀.

Spec reference: Section 16.6(2), Section 17.6(2).
"""

from __future__ import annotations

from typing import Any, Callable

import numpy as np

from ..metrics.regret import compute_entry_regret, optimal_stopping_under_h0


def estimate_timing(
    model_trajectory: Any,
    null_trajectories: list[Any],
    rng: np.random.Generator | None = None,
) -> dict[str, float]:
    """Estimate timing edge E_t.

    Compares model entry regret against H₀ entry regret distribution.

    Parameters
    ----------
    model_trajectory : Any
        Observed strategy trajectory (list of trade dicts with bar_index).
    null_trajectories : list[Any]
        Counterfactual trajectories from any H₀ transform.

    Returns
    -------
    dict with keys: mean, std, model_regret, null_regret_mean.
    """
    if rng is None:
        rng = np.random.default_rng()

    # Model regret: distance to H₀ optimal stopping
    model_trades = list(model_trajectory)
    entry_times = np.array([t.get("bar_index", 0) for t in model_trades])
    optimal_times = optimal_stopping_under_h0(
        model_trades, n_simulations=500, rng=rng
    )
    model_regret = compute_entry_regret(entry_times, optimal_times)

    # Null regret distribution: compute for each null trajectory
    null_regrets = []
    for n_traj in null_trajectories:
        n_trades = list(n_traj)
        if not n_trades:
            continue
        n_entry = np.array([t.get("bar_index", 0) for t in n_trades])
        n_opt = optimal_stopping_under_h0(
            n_trades, n_simulations=200, rng=rng
        )
        null_regrets.append(compute_entry_regret(n_entry, n_opt))

    null_regrets = np.array(null_regrets)
    if len(null_regrets) == 0:
        return {"mean": 0.0, "std": 0.0, "model_regret": 0.0, "null_regret_mean": 0.0}

    # Timing edge = (null regret) - (model regret), i.e., positive = model has better timing
    timing_edge = null_regrets.mean() - model_regret
    std = null_regrets.std() / np.sqrt(len(null_regrets))

    return {
        "mean": float(timing_edge),
        "std": float(std),
        "model_regret": float(model_regret),
        "null_regret_mean": float(null_regrets.mean()),
    }
