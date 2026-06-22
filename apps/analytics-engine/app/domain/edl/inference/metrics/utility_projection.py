"""U(τ) projection layer — maps trajectories into utility space.

Bridges the trajectory manifold to the utility functional (spec Section 14),
ensuring all components are evaluated in the same utility space.
"""

from __future__ import annotations

from typing import Any, Callable

import numpy as np


class UtilityProjection:
    """Projects trajectory objects into U(τ) utility space.

    This adapter ensures any trajectory representation
    (list of trades, equity curve, etc.) can be evaluated
    under the same U(τ) functional.
    """

    def __init__(self, utility_fn: Callable[[Any], float]) -> None:
        self._utility_fn = utility_fn

    def project(self, trajectory: Any) -> float:
        return self._utility_fn(trajectory)

    def project_many(self, trajectories: list[Any]) -> np.ndarray:
        return np.array([self._utility_fn(t) for t in trajectories])
