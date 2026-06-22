"""TensorRebuilder — reconstruye Edge Tensors (Φ) desde el ledger.

Consume EdgeTensorDTOs (o dicts) y reconstruye la serie temporal,
validando consistencia con los flags de EIA.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReplayComponent:
    mean: float = 0.0
    std: float = 0.0
    ci_lower: float = 0.0
    ci_upper: float = 0.0
    identifiable: bool = False


@dataclass(frozen=True)
class ReplayTensor:
    experiment_id: str = ""
    timestamp: str = ""
    symbol: str = ""
    n_trades: int = 0
    directional: ReplayComponent = field(default_factory=ReplayComponent)
    timing: ReplayComponent = field(default_factory=ReplayComponent)
    execution: ReplayComponent = field(default_factory=ReplayComponent)
    structural: ReplayComponent = field(default_factory=ReplayComponent)


def _parse_component(d: Any) -> ReplayComponent:
    if isinstance(d, dict):
        return ReplayComponent(
            mean=float(d.get("mean", 0.0)),
            std=float(d.get("std", 0.0)),
            ci_lower=float(d.get("ci_lower", 0.0)),
            ci_upper=float(d.get("ci_upper", 0.0)),
            identifiable=bool(d.get("identifiable", False)),
        )
    return ReplayComponent(
        mean=float(getattr(d, "mean", 0.0)),
        std=float(getattr(d, "std", 0.0)),
        ci_lower=float(getattr(d, "ci_lower", 0.0)),
        ci_upper=float(getattr(d, "ci_upper", 0.0)),
        identifiable=bool(getattr(d, "identifiable", False)),
    )


def _tensor_to_replay(dto: Any) -> ReplayTensor:
    if isinstance(dto, dict):
        return ReplayTensor(
            experiment_id=dto.get("experiment_id", ""),
            timestamp=dto.get("timestamp", ""),
            symbol=dto.get("symbol", ""),
            n_trades=int(dto.get("n_trades", 0)),
            directional=_parse_component(dto.get("directional", {})),
            timing=_parse_component(dto.get("timing", {})),
            execution=_parse_component(dto.get("execution", {})),
            structural=_parse_component(dto.get("structural", {})),
        )
    return ReplayTensor(
        experiment_id=getattr(dto, "experiment_id", ""),
        timestamp=getattr(dto, "timestamp", ""),
        symbol=getattr(dto, "symbol", ""),
        n_trades=int(getattr(dto, "n_trades", 0)),
        directional=_parse_component(getattr(dto, "directional", {})),
        timing=_parse_component(getattr(dto, "timing", {})),
        execution=_parse_component(getattr(dto, "execution", {})),
        structural=_parse_component(getattr(dto, "structural", {})),
    )


class TensorRebuilder:
    """Rebuilds Edge Tensor time series from persisted tensor data."""

    def __init__(self) -> None:
        self._tensors: list[ReplayTensor] = []

    def rebuild(self, tensor_dtos: list[Any]) -> list[ReplayTensor]:
        """Rebuild tensor time series sorted by timestamp."""
        converted = [_tensor_to_replay(t) for t in tensor_dtos]
        sorted_tensors = sorted(converted, key=lambda t: t.timestamp)
        self._tensors = sorted_tensors
        return self._tensors

    @property
    def tensors(self) -> list[ReplayTensor]:
        return list(self._tensors)

    @property
    def latest(self) -> ReplayTensor | None:
        if not self._tensors:
            return None
        return self._tensors[-1]

    def validate_consistency(
        self,
        original_tensor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate tensor series consistency."""
        import math
        issues = []

        for i, tensor in enumerate(self._tensors):
            for comp_name, comp in [
                ("directional", tensor.directional),
                ("timing", tensor.timing),
                ("execution", tensor.execution),
                ("structural", tensor.structural),
            ]:
                if any(math.isnan(v) or math.isinf(v)
                       for v in [comp.mean, comp.std,
                                 comp.ci_lower, comp.ci_upper]):
                    issues.append(
                        f"Index {i}: {comp_name} has invalid values"
                    )

        for i in range(1, len(self._tensors)):
            if self._tensors[i].timestamp < self._tensors[i - 1].timestamp:
                issues.append(
                    f"Non-monotonic timestamp at index {i}"
                )

        match_rate = 1.0
        mismatches = []
        if original_tensor is not None and self._tensors:
            latest = self._tensors[-1]
            for comp in ["directional", "timing", "execution", "structural"]:
                orig_comp = original_tensor.get(comp, {})
                reb_comp = getattr(latest, comp, None)
                if reb_comp is None:
                    mismatches.append(f"{comp}: missing")
                    continue
                for field in ["mean", "std", "identifiable"]:
                    o = orig_comp.get(field)
                    r = getattr(reb_comp, field)
                    if isinstance(o, (int, float)) and isinstance(r, (int, float)):
                        if abs(o - r) > 1e-4:
                            mismatches.append(f"{comp}.{field}: {o} vs {r}")
                    elif bool(o) != bool(r):
                        mismatches.append(f"{comp}.{field}: {o} vs {r}")
                match_rate = 1.0 - (len(mismatches) / max(1, 4 * 3))

        return {
            "is_consistent": len(issues) == 0,
            "n_tensors": len(self._tensors),
            "match_rate": match_rate,
            "issues": issues,
            "mismatches": mismatches,
        }
