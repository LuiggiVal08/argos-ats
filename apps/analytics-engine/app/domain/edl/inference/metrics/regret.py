"""Regret-based metrics for timing edge estimation.

Measures the regret-distance between actual entry times and
the optimal stopping distribution under H₀.
"""

from __future__ import annotations

from typing import Any

import numpy as np


def compute_entry_regret(
    entry_times: np.ndarray,
    optimal_times: np.ndarray,
) -> float:
    """Compute mean regret between actual and optimal entry times.

    Regret = E[|t_entry - t*_optimal|] where t*_optimal is the
    expected optimal entry under H₀ stopping distribution.

    Parameters
    ----------
    entry_times : np.ndarray
        Actual entry times (normalized, e.g. bar indices).
    optimal_times : np.ndarray
        Optimal entry times under H₀.

    Returns
    -------
    float
        Mean absolute regret. Lower is better (better timing).
    """
    if len(entry_times) == 0 or len(optimal_times) == 0:
        return 0.0
    regrets = np.abs(entry_times - optimal_times)
    return float(regrets.mean())


def optimal_stopping_under_h0(
    trades: list[dict[str, Any]],
    n_simulations: int = 1000,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """Estimate optimal entry times under H₀ stopping distribution.

    Uses the distribution of entry times under H₀ to define
    the expected optimal entry for each trade.

    Parameters
    ----------
    trades : list[dict]
        Trade episodes with bar_index entries.
    n_simulations : int
        Number of H₀ simulations.
    rng : np.random.Generator, optional

    Returns
    -------
    np.ndarray
        Expected optimal entry bar indices.
    """
    if rng is None:
        rng = np.random.default_rng()

    entry_indices = np.array([t.get("bar_index", 0) for t in trades])
    if len(entry_indices) == 0:
        return np.array([])

    # Under H₀, entry times are samples from the same distribution
    # with noise: we estimate the mean entry time as the optimal
    h0_samples = [entry_indices + rng.normal(0, 1, size=len(entry_indices))
                  for _ in range(n_simulations)]
    return np.mean(h0_samples, axis=0)
