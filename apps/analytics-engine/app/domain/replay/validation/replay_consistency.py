"""ReplayConsistencyReport — reporte de consistencia del replay.

Compara trades, episodes, tensors y decisiones de control
reconstruidos contra los originales y produce métricas
de consistencia agregadas.
"""

from __future__ import annotations

from typing import Any


class ReplayConsistencyReport:
    """Agrega métricas de consistencia de todos los reconstructores.

    Produce un reporte unificado con:
    - trade_match_rate
    - episode_match_rate
    - tensor_match_rate
    - control_match_rate
    - drift_score
    """

    ACCEPTANCE_DRIFT_THRESHOLD: float = 1e-6

    def __init__(self) -> None:
        self._results: dict[str, Any] = {}

    def add_result(self, name: str, result: dict[str, Any]) -> None:
        self._results[name] = result

    def summary(self) -> dict[str, Any]:
        """Compute aggregate consistency metrics."""
        rates = []
        for name, result in self._results.items():
            rate = result.get("match_rate", 0.0)
            rates.append(rate)

        trade_rate = self._results.get("trades", {}).get("match_rate", 0.0)
        episode_rate = self._results.get("episodes", {}).get("match_rate", 0.0)
        tensor_rate = self._results.get("tensors", {}).get("match_rate", 0.0)
        control_rate = self._results.get("control", {}).get("match_rate", 0.0)

        # Drift score: weighted average of 1 - match_rate
        n_components = sum(1 for r in rates if r > 0)
        drift_score = (
            (1 - trade_rate)
            + (1 - episode_rate)
            + (1 - tensor_rate)
            + (1 - control_rate)
        ) / max(1, n_components)

        passed = drift_score < self.ACCEPTANCE_DRIFT_THRESHOLD

        return {
            "trade_match_rate": trade_rate,
            "episode_match_rate": episode_rate,
            "tensor_match_rate": tensor_rate,
            "control_match_rate": control_rate,
            "drift_score": drift_score,
            "acceptance_threshold": self.ACCEPTANCE_DRIFT_THRESHOLD,
            "passed": passed,
            "veredict": "PASS" if passed else "FAIL",
            "details": self._results,
        }

    @property
    def passed(self) -> bool:
        return self.summary()["passed"]
