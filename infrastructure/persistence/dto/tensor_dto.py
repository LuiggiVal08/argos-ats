from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EdgeComponentDTO:
    mean: float = 0.0
    std: float = 0.0
    ci_lower: float = 0.0
    ci_upper: float = 0.0
    identifiable: bool = False


@dataclass(frozen=True)
class EdgeTensorDTO:
    experiment_id: str
    timestamp: str
    symbol: str = ""
    n_trades: int = 0
    directional: EdgeComponentDTO = EdgeComponentDTO()
    timing: EdgeComponentDTO = EdgeComponentDTO()
    execution: EdgeComponentDTO = EdgeComponentDTO()
    structural: EdgeComponentDTO = EdgeComponentDTO()
