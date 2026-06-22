"""E_s — Structural Edge projection.

Measures mutual information between trajectory and regime sequence.
Positive structural edge indicates the model exploits regime structure
more than the null baseline.

Spec reference: Section 16.6(4), Section 17.6(4).
"""

from __future__ import annotations

from typing import Any

import numpy as np


def _mutual_information_knn(
    trajectory_returns: np.ndarray,
    regime_labels: np.ndarray,
    k: int = 5,
) -> float:
    """Estimate I(τ; R) using kNN-based mutual information.

    Uses the Kozachenko-Leonenko estimator adapted for the
    joint distribution of returns and regime labels.

    Parameters
    ----------
    trajectory_returns : np.ndarray
        Sequence of returns.
    regime_labels : np.ndarray
        Discrete regime labels (0, 1, 2, ...).
    k : int
        Number of nearest neighbors.

    Returns
    -------
    float
        Mutual information estimate in nats.
    """
    n = len(trajectory_returns)
    if n < k + 1:
        return 0.0

    # If all returns are identical, MI is trivially 0
    if np.nanmin(trajectory_returns) == np.nanmax(trajectory_returns):
        return 0.0

    # Discretize returns into bins for MI computation
    n_bins = min(50, n // 10)
    if n_bins < 2:
        return 0.0

    # Use histogram-based MI as a robust proxy
    ret_bins = np.linspace(
        trajectory_returns.min(), trajectory_returns.max(), n_bins + 1
    )
    ret_discrete = np.digitize(trajectory_returns, ret_bins) - 1

    # Contingency table
    n_regimes = len(np.unique(regime_labels))
    contingency = np.zeros((n_bins, n_regimes))
    for r, reg in zip(ret_discrete, regime_labels):
        if 0 <= r < n_bins and 0 <= reg < n_regimes:
            contingency[r, reg] += 1

    # Normalize to joint probability
    total = contingency.sum()
    if total == 0:
        return 0.0
    joint = contingency / total

    # Marginal probabilities
    p_ret = joint.sum(axis=1)
    p_reg = joint.sum(axis=0)

    # Mutual information
    mi = 0.0
    for i in range(n_bins):
        if p_ret[i] == 0:
            continue
        for j in range(n_regimes):
            if p_reg[j] == 0 or joint[i, j] == 0:
                continue
            mi += joint[i, j] * np.log(joint[i, j] / (p_ret[i] * p_reg[j]))

    return mi


def estimate_structural(
    model_trajectory: Any,
    null_trajectories: list[Any],
    rng: np.random.Generator | None = None,
) -> dict[str, float]:
    """Estimate structural edge E_s.

    Compares MI between model trajectory and regime sequence
    against the MI distribution under H₀.

    Parameters
    ----------
    model_trajectory : Any
        List of trade dicts with pnl and regime fields.
    null_trajectories : list[Any]
        Counterfactual trajectories.

    Returns
    -------
    dict with keys: mean, std, model_mi, null_mi_mean.
    """
    model_trades = list(model_trajectory)
    model_returns = np.array([t.get("net_pnl", 0.0) for t in model_trades])
    model_regimes = np.array([
        _regime_to_int(t.get("regime", "UNKNOWN")) for t in model_trades
    ])

    model_mi = _mutual_information_knn(model_returns, model_regimes)

    # Null MI distribution
    null_mis = []
    for n_traj in null_trajectories:
        n_list = list(n_traj)
        if not n_list:
            continue
        n_ret = np.array([t.get("net_pnl", 0.0) for t in n_list])
        n_reg = np.array([
            _regime_to_int(t.get("regime", "UNKNOWN")) for t in n_list
        ])
        null_mis.append(_mutual_information_knn(n_ret, n_reg))

    null_mis = np.array(null_mis)
    if len(null_mis) == 0 or len(null_mis) < 3:
        return {"mean": 0.0, "std": 0.0, "model_mi": 0.0, "null_mi_mean": 0.0}

    # Structural edge = model_MI - null_MI, positive = model better exploits regime
    struct_edge = model_mi - null_mis.mean()
    std = null_mis.std() / np.sqrt(len(null_mis))

    return {
        "mean": float(struct_edge),
        "std": float(std),
        "model_mi": float(model_mi),
        "null_mi_mean": float(null_mis.mean()),
    }


def _regime_to_int(regime: str) -> int:
    mapping = {
        "TRENDING": 0,
        "TRENDING_BULL": 0,
        "TRENDING_BEAR": 1,
        "RANGING": 2,
        "VOLATILE": 3,
        "UNKNOWN": 4,
    }
    return mapping.get(regime.upper(), 4)
