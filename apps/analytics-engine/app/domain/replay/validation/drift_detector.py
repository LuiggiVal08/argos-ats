"""DriftDetector — detecta divergencias entre replay y original.

Identifica:
- Divergencia en U(τ) entre original y reconstruido
- Divergencia en componentes del Edge Tensor
- Divergencia en decisiones de control
- Inconsistencias en ordenamiento temporal
"""

from __future__ import annotations

from typing import Any


class DriftDetector:
    """Detects structural drift between original and replay.

    Each detection method returns a DriftReport entry.
    """

    def __init__(self, tolerance: float = 1e-6) -> None:
        self._tolerance = tolerance

    def check_utility_drift(
        self,
        original_utility: float | None,
        reconstructed_utility: float | None,
    ) -> dict[str, Any]:
        """Check for drift in U(τ)."""
        if original_utility is None or reconstructed_utility is None:
            return {
                "type": "utility_drift",
                "detected": False,
                "reason": "No utility data to compare",
                "drift": 0.0,
            }
        drift = abs(original_utility - reconstructed_utility)
        return {
            "type": "utility_drift",
            "detected": drift > self._tolerance,
            "drift": drift,
            "tolerance": self._tolerance,
            "original": original_utility,
            "reconstructed": reconstructed_utility,
        }

    def check_tensor_drift(
        self,
        original_tensor: dict[str, Any] | None,
        reconstructed_tensor: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Check for drift in Edge Tensor components."""
        if original_tensor is None or reconstructed_tensor is None:
            return {
                "type": "tensor_drift",
                "detected": False,
                "reason": "No tensor data to compare",
                "drift": 0.0,
            }

        components = ["directional", "timing", "execution", "structural"]
        total_drift = 0.0
        drifts: dict[str, float] = {}

        for comp in components:
            o = original_tensor.get(comp, {})
            r = reconstructed_tensor.get(comp, {})
            o_mean = o.get("mean", 0.0) if isinstance(o, dict) else 0.0
            r_mean = r.get("mean", 0.0) if isinstance(r, dict) else 0.0
            drift = abs(o_mean - r_mean)
            drifts[comp] = drift
            total_drift += drift

        avg_drift = total_drift / len(components)
        return {
            "type": "tensor_drift",
            "detected": avg_drift > self._tolerance,
            "drift": avg_drift,
            "component_drifts": drifts,
            "tolerance": self._tolerance,
        }

    def check_control_drift(
        self,
        original_control: dict[str, Any] | None,
        reconstructed_control: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Check for drift in control decisions."""
        if original_control is None or reconstructed_control is None:
            return {
                "type": "control_drift",
                "detected": False,
                "reason": "No control data to compare",
                "drift": 0.0,
            }

        fields = ["action", "h_interno", "h_externo",
                  "hazard_immediate", "hazard_cumulative"]
        drifts: dict[str, Any] = {}
        for field in fields:
            o = original_control.get(field)
            r = reconstructed_control.get(field)
            if isinstance(o, float) and isinstance(r, float):
                drifts[field] = abs(o - r)
            else:
                drifts[field] = 0.0 if str(o) == str(r) else 1.0

        has_drift = any(
            v > self._tolerance for v in drifts.values() if isinstance(v, float)
        )
        avg_drift = sum(
            v for v in drifts.values() if isinstance(v, float)
        ) / len(fields)

        return {
            "type": "control_drift",
            "detected": has_drift,
            "drift": avg_drift,
            "field_drifts": drifts,
            "tolerance": self._tolerance,
        }

    def check_temporal_consistency(
        self,
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Check for temporal ordering inconsistencies."""
        issues = []
        for i in range(1, len(events)):
            ts_i = events[i].get("timestamp", "")
            ts_prev = events[i - 1].get("timestamp", "")
            if ts_i and ts_prev and ts_i < ts_prev:
                issues.append({
                    "index": i,
                    "current_ts": ts_i,
                    "previous_ts": ts_prev,
                })

        return {
            "type": "temporal_drift",
            "detected": len(issues) > 0,
            "n_issues": len(issues),
            "total_events": len(events),
            "issues": issues[:5],
        }
