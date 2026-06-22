"""EIA enforcement — tests if edge components satisfy identifiability.

Spec reference: Section 15.5 (EIA conditions).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ..edge_tensor import EdgeComponent


def check_separability(
    component_mean: float,
    null_variance: float,
    threshold: float = 1e-6,
) -> tuple[bool, float]:
    """EIA Condition (1): non-zero margin exists.

    Parameters
    ----------
    component_mean : float
        Estimated edge component value.
    null_variance : float
        Variance of component across null ensemble.
    threshold : float
        Minimum absolute value for non-zero margin.

    Returns
    -------
    (passed, margin)
    """
    margin = abs(component_mean)
    return margin > threshold, margin


def check_null_invariance(
    estimates_per_null: dict[str, float],
    epsilon: float = 0.1,
) -> tuple[bool, float]:
    """EIA Condition (2): stability across null selection.

    Parameters
    ----------
    estimates_per_null : dict[str, float]
        {null_name: component_estimate}.
    epsilon : float
        Maximum allowed variance relative to mean magnitude.

    Returns
    -------
    (passed, variance)
    """
    values = np.array(list(estimates_per_null.values()))
    variance = float(np.var(values))
    mean_abs = float(np.mean(np.abs(values)))
    if mean_abs < 1e-12:
        return True, variance  # no signal → trivially invariant
    return variance / mean_abs < epsilon, variance


def check_representation_stability(
    bootstrap_estimates: list[float],
    threshold_std_ratio: float = 2.0,
) -> tuple[bool, float]:
    """EIA Condition (3): stability under resampling.

    Parameters
    ----------
    bootstrap_estimates : list[float]
        Component estimates from each bootstrap/resample.
    threshold_std_ratio : float
        Maximum acceptable std/mean ratio.

    Returns
    -------
    (passed, std)
    """
    estimates = np.array(bootstrap_estimates)
    std = float(estimates.std())
    mean = float(abs(estimates.mean()))
    if mean < 1e-12:
        return True, std  # no signal → trivially stable
    return std / mean < threshold_std_ratio, std


def eia_all_conditions(
    component_mean: float,
    estimates_per_null: dict[str, float],
    bootstrap_estimates: list[float],
    separability_threshold: float = 1e-6,
    null_invariance_epsilon: float = 0.1,
    stability_threshold: float = 2.0,
) -> tuple[bool, dict[str, Any]]:
    """Check all three EIA conditions for a component.

    Returns
    -------
    (identifiable, details)
    """
    sep_ok, margin = check_separability(
        component_mean, 0.0, separability_threshold
    )
    inv_ok, null_var = check_null_invariance(
        estimates_per_null, null_invariance_epsilon
    )
    stab_ok, bs_std = check_representation_stability(
        bootstrap_estimates, stability_threshold
    )

    identifiable = sep_ok and inv_ok and stab_ok

    details = {
        "identifiable": identifiable,
        "separability": {"passed": sep_ok, "margin": margin},
        "null_invariance": {"passed": inv_ok, "variance": null_var},
        "representation_stability": {"passed": stab_ok, "std": bs_std},
    }
    return identifiable, details
