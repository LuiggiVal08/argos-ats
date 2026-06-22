"""Φ(D) — Edge Tensor core object (spec Section 16).

Represents the vector of invariant asymmetries between observed
trajectories and a class of admissible counterfactual worlds.

E(D) = [E_d, E_t, E_e, E_s]ᵀ = Φ(τ | H₁, ℍ₀)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass(frozen=True)
class EdgeComponent:
    """A single identifiable edge component.

    If the component fails EIA (Section 15), `identifiable` is False
    and the mean/std represent the null variance floor, not edge.
    """

    mean: float = 0.0
    std: float = 0.0
    ci_lower: float = 0.0
    ci_upper: float = 0.0
    identifiable: bool = False

    def __bool__(self) -> bool:
        """A component is truthy iff it is identifiable and non-zero."""
        return self.identifiable and abs(self.mean) > self.std * 2.0

    @property
    def signal_to_noise(self) -> float:
        if self.std == 0.0:
            return float("inf")
        return abs(self.mean) / self.std


@dataclass(frozen=True)
class EdgeTensor:
    """The Edge Tensor Φ(D) = [E_d, E_t, E_e, E_s]ᵀ.

    This is the system's complete measurement of edge under the
    current hypothesis class.
    """

    directional: EdgeComponent = field(default_factory=EdgeComponent)
    timing: EdgeComponent = field(default_factory=EdgeComponent)
    execution: EdgeComponent = field(default_factory=EdgeComponent)
    structural: EdgeComponent = field(default_factory=EdgeComponent)

    def components(self) -> dict[str, EdgeComponent]:
        return {
            "directional": self.directional,
            "timing": self.timing,
            "execution": self.execution,
            "structural": self.structural,
        }

    def norm(self) -> dict[str, float]:
        return {
            "directional": float(self.directional.mean),
            "timing": float(self.timing.mean),
            "execution": float(self.execution.mean),
            "structural": float(self.structural.mean),
        }

    def identifiability(self) -> dict[str, bool]:
        return {
            "directional": self.directional.identifiable,
            "timing": self.timing.identifiable,
            "execution": self.execution.identifiable,
            "structural": self.structural.identifiable,
        }

    @property
    def has_any_edge(self) -> bool:
        """True iff at least one component is identifiable and non-zero."""
        return any(
            comp for comp in
            [self.directional, self.timing, self.execution, self.structural]
        )

    @property
    def has_full_edge(self) -> bool:
        """True iff ALL components are identifiable and non-zero."""
        return all(
            comp for comp in
            [self.directional, self.timing, self.execution, self.structural]
        )

    def to_dict(self) -> dict:
        return {
            "directional": {
                "mean": round(self.directional.mean, 6),
                "std": round(self.directional.std, 6),
                "ci": [
                    round(self.directional.ci_lower, 6),
                    round(self.directional.ci_upper, 6),
                ],
            },
            "timing": {
                "mean": round(self.timing.mean, 6),
                "std": round(self.timing.std, 6),
                "ci": [
                    round(self.timing.ci_lower, 6),
                    round(self.timing.ci_upper, 6),
                ],
            },
            "execution": {
                "mean": round(self.execution.mean, 6),
                "std": round(self.execution.std, 6),
                "ci": [
                    round(self.execution.ci_lower, 6),
                    round(self.execution.ci_upper, 6),
                ],
            },
            "structural": {
                "mean": round(self.structural.mean, 6),
                "std": round(self.structural.std, 6),
                "ci": [
                    round(self.structural.ci_lower, 6),
                    round(self.structural.ci_upper, 6),
                ],
            },
            "identifiability": self.identifiability(),
            "has_any_edge": self.has_any_edge,
            "has_full_edge": self.has_full_edge,
        }
