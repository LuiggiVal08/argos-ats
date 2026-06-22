"""Null invariance tests — verifies FEOT Null Stability condition.

Spec reference: Section 19.4(2).
"""

from __future__ import annotations

from typing import Any

import numpy as np


def null_variance_across_class(
    component_estimates: dict[str, dict[str, float]],
) -> dict[str, float]:
    """Compute variance of each component across the null class ℍ₀.

    Parameters
    ----------
    component_estimates : dict[str, dict[str, float]]
        {null_name: {component: value}}.

    Returns
    -------
    dict[str, float]
        {component: variance_across_nulls}.
    """
    if not component_estimates:
        return {}

    components = set()
    for _, estimates in component_estimates.items():
        components.update(estimates.keys())

    variances = {}
    for comp in components:
        values = [est[comp] for est in component_estimates.values()
                  if comp in est]
        if len(values) < 2:
            variances[comp] = 0.0
        else:
            variances[comp] = float(np.var(values))

    return variances


def null_stability_check(
    component_estimates: dict[str, dict[str, float]],
    threshold: float = 0.1,
) -> tuple[bool, dict[str, Any]]:
    """FEOT Null Stability test: variance across null class < threshold.

    Spec reference: Section 19.4(2): Varₖ(Φ(τ | Tₖ)) < δ.

    Parameters
    ----------
    component_estimates : dict[str, dict[str, float]]
        {null_name: {component: estimate}}.
    threshold : float
        Maximum allowed variance.

    Returns
    -------
    (passed, details)
    """
    variances = null_variance_across_class(component_estimates)
    failed = {comp: var for comp, var in variances.items()
              if var >= threshold}
    passed = len(failed) == 0
    return passed, {"variances": variances, "failed_components": failed}


def sign_consistency_check(
    component_estimates: dict[str, dict[str, float]],
) -> dict[str, bool]:
    """Check sign stability: sign of each component is constant across nulls.

    Spec reference: Section 16.7: sign(Φ(τ | H₁, H₀ᵢ)) = constant.

    Parameters
    ----------
    component_estimates : dict[str, dict[str, float]]
        {null_name: {component: estimate}}.

    Returns
    -------
    dict[str, bool]
        {component: sign_consistent}.
    """
    if not component_estimates:
        return {}

    components = set()
    for _, est in component_estimates.items():
        components.update(est.keys())

    consistency = {}
    for comp in components:
        signs = set()
        for est in component_estimates.values():
            if comp in est:
                val = est[comp]
                signs.add(1 if val > 0 else (-1 if val < 0 else 0))
        consistency[comp] = len(signs) <= 1

    return consistency
