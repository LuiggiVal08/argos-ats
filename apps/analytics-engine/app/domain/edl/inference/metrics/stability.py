"""Stability metrics — variance of edge components over regimes and resampling.

Spec reference: Section 17.8 (Horizon Consistency Constraint).
"""

from __future__ import annotations

from typing import Any

import numpy as np


def variance_across_regimes(
    component_values: dict[str, float],
) -> float:
    """Compute variance of a component across regime slices.

    If this variance exceeds threshold, edge is regime-bound
    rather than system-level.

    Parameters
    ----------
    component_values : dict[str, float]
        Mapping {regime_name: component_value}.

    Returns
    -------
    float
        Variance across regimes.
    """
    values = list(component_values.values())
    if len(values) < 2:
        return 0.0
    return float(np.var(values))


def variance_across_bootstrap(
    bootstrap_estimates: list[float],
) -> float:
    """Compute variance of a component across bootstrap resamples.

    Used to enforce Inference Consistency (Section 19.4(3)).

    Parameters
    ----------
    bootstrap_estimates : list[float]
        Component estimates from each bootstrap sample.

    Returns
    -------
    float
        Variance across resamples.
    """
    if len(bootstrap_estimates) < 2:
        return 0.0
    return float(np.var(bootstrap_estimates))


def horizon_consistency_check(
    rolling_estimates: list[float],
    threshold: float = 0.1,
) -> tuple[bool, float]:
    """Check if a component is stable across rolling windows.

    Returns (passed, variance).

    Spec reference: Section 17.8.
    """
    var = variance_across_bootstrap(rolling_estimates)
    return var < threshold, var
