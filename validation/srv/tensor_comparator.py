"""TensorComparator — component-by-component Φ comparison at EPSILON.

Spec §16: Φ(D) = [E_d, E_t, E_e, E_s]ᵀ.

Comparison is exact: EPSILON = 1e-9 because replay must be
a BIT-EXACT reconstruction (same algorithm, same data, same seed).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


EPSILON = 1e-9


@dataclass(frozen=True)
class TensorDrift:
    """Drift measurement for a single Φ component."""

    component: str
    live_mean: float
    replay_mean: float
    drift: float
    within_tolerance: bool
    live_identifiable: bool
    replay_identifiable: bool
    identifiability_match: bool
    live_std: float = 0.0
    replay_std: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "live": {"mean": self.live_mean, "std": self.live_std,
                     "identifiable": self.live_identifiable},
            "replay": {"mean": self.replay_mean, "std": self.replay_std,
                       "identifiable": self.replay_identifiable},
            "drift": self.drift,
            "within_tolerance": self.within_tolerance,
            "identifiability_match": self.identifiability_match,
        }


class TensorComparator:
    """Compares two EdgeTensor instances component by component.

    Usage:
        result = TensorComparator.compare(phi_live, phi_replay)
        result["all_within_tolerance"]  → bool
        result["components"]            → list[TensorDrift]
    """

    @staticmethod
    def compare(
        phi_live: Any,
        phi_replay: Any,
        epsilon: float = EPSILON,
    ) -> dict[str, Any]:
        """Compare two Φ tensors.

        Accepts either EdgeTensor domain objects or dicts with the
        same schema ({"directional": {"mean": ..., ...}, ...}).

        Returns a dict with:
        - components: list[TensorDrift]
        - all_within_tolerance: bool
        - total_drift: float (sum of abs drifts across all components)
        - n_components: int
        - n_failures: int
        """
        live = TensorComparator._normalize(phi_live)
        replay = TensorComparator._normalize(phi_replay)

        comp_names = ["directional", "timing", "execution", "structural"]
        drifts: list[TensorDrift] = []
        total_drift = 0.0
        n_failures = 0

        for name in comp_names:
            lc = live.get(name, {})
            rc = replay.get(name, {})

            lm = float(lc.get("mean", 0.0))
            rm = float(rc.get("mean", 0.0))
            if math.isnan(lm) or math.isnan(rm):
                drift = float("inf")
            else:
                drift = abs(lm - rm)
            total_drift += drift

            comp = TensorDrift(
                component=name,
                live_mean=lm,
                replay_mean=rm,
                drift=drift,
                within_tolerance=drift <= epsilon,
                live_identifiable=bool(lc.get("identifiable", False)),
                replay_identifiable=bool(rc.get("identifiable", False)),
                live_std=float(lc.get("std", 0.0)),
                replay_std=float(rc.get("std", 0.0)),
                identifiability_match=(
                    bool(lc.get("identifiable", False))
                    == bool(rc.get("identifiable", False))
                ),
            )
            if not comp.within_tolerance or not comp.identifiability_match:
                n_failures += 1
            drifts.append(comp)

        return {
            "components": drifts,
            "all_within_tolerance": all(d.within_tolerance for d in drifts),
            "all_identifiability_match": all(d.identifiability_match for d in drifts),
            "total_drift": total_drift,
            "n_components": len(comp_names),
            "n_failures": n_failures,
        }

    @staticmethod
    def _normalize(phi: Any) -> dict[str, Any]:
        """Convert EdgeTensor or dict to normalized dict form."""
        if hasattr(phi, "to_dict"):
            return phi.to_dict()
        if isinstance(phi, dict):
            return phi
        return {}
