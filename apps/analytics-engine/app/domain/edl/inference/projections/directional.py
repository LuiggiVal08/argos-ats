"""E_d — Directional Edge projection.

Measures deviation from random directional exposure using binomial
contrast over matched trajectory pairs.

Proxy: P(U(τ_model) > U(τ_H₀₁)) where H₀₁ permutes sides.

Spec reference: Section 16.6(1), Section 17.6(1).
"""

from __future__ import annotations

from typing import Any

import numpy as np


def estimate_directional(
    model_trajectory: Any,
    null_samples: list[Any],
    utility_fn: Any,
    rng: np.random.Generator | None = None,
) -> dict[str, float]:
    """Estimate directional edge E_d.

    Parameters
    ----------
    model_trajectory : Any
        Observed strategy trajectory.
    null_samples : list[Any]
        Counterfactual trajectories from H₀₁ (or any null).
    utility_fn : callable
        U(τ) functional.

    Returns
    -------
    dict with keys: mean, std, n_wins, n_total.
    """
    if rng is None:
        rng = np.random.default_rng()

    u_model = utility_fn(model_trajectory)
    u_nulls = np.array([utility_fn(t) for t in null_samples])

    n_wins = int((u_model > u_nulls).sum())
    n_total = len(null_samples)

    # Binomial proportion
    p_edge = n_wins / n_total if n_total > 0 else 0.0
    std = np.sqrt(p_edge * (1 - p_edge) / n_total) if n_total > 0 else 0.0

    return {
        "mean": float(p_edge),
        "std": float(std),
        "n_wins": n_wins,
        "n_total": n_total,
    }
