"""E_e — Execution Edge projection.

Measures cost efficiency vs null execution path. Positive edge means
the model achieves lower costs than the counterfactual execution baseline.

Spec reference: Section 16.6(3), Section 17.6(3).
"""

from __future__ import annotations

from typing import Any

import numpy as np


def estimate_execution(
    model_trajectory: Any,
    null_trajectories: list[Any],
    rng: np.random.Generator | None = None,
) -> dict[str, float]:
    """Estimate execution edge E_e.

    Compares model costs against null trajectory cost distribution.

    Parameters
    ----------
    model_trajectory : Any
        Observed strategy trajectory (list of trade dicts with costs).
    null_trajectories : list[Any]
        Counterfactual trajectories with cost noise injected.

    Returns
    -------
    dict with keys: mean, std, model_cost, null_cost_mean.
    """
    model_trades = list(model_trajectory)
    model_costs = np.array([t.get("costs", 0.0) for t in model_trades])
    model_total_cost = float(model_costs.sum())

    # Null cost distribution
    null_costs = []
    for n_traj in null_trajectories:
        n_list = list(n_traj)
        costs = np.array([t.get("costs", 0.0) for t in n_list])
        null_costs.append(float(costs.sum()))

    null_costs = np.array(null_costs)
    if len(null_costs) == 0:
        return {"mean": 0.0, "std": 0.0, "model_cost": 0.0, "null_cost_mean": 0.0}

    # Execution edge = null_cost - model_cost, positive = model executes cheaper
    exec_edge = null_costs.mean() - model_total_cost
    std = null_costs.std() / np.sqrt(len(null_costs))

    return {
        "mean": float(exec_edge),
        "std": float(std),
        "model_cost": float(model_total_cost),
        "null_cost_mean": float(null_costs.mean()),
    }
