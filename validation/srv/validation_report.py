"""ValidationReport — structured SRV output.

Four drift categories with pass/fail per category.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


class SrvStatus:
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"


class SrvCategory:
    TENSOR = "tensor_drift"
    PROBABILITY = "probability_drift"
    CONTROL = "control_drift"
    EIA = "eia_drift"


@dataclass
class SrvReport:
    """Complete SRV validation report."""

    run_id: str
    symbol: str
    timestamp: str = ""

    tensor_drift: dict[str, Any] = field(default_factory=dict)
    probability_drift: dict[str, Any] = field(default_factory=dict)
    control_drift: dict[str, Any] = field(default_factory=dict)
    eia_drift: dict[str, Any] = field(default_factory=dict)

    n_live_trades: int = 0
    n_replay_trades: int = 0
    n_events_replayed: int = 0

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )

    @property
    def tensor_pass(self) -> bool:
        return self.tensor_drift.get("all_within_tolerance", False) and (
            not self.tensor_drift.get("components")
            or all(
                c.get("identifiability_match", True)
                for c in self.tensor_drift.get("components", [])
            )
        )

    @property
    def probability_pass(self) -> bool:
        pd_ = self.probability_drift
        return all(
            pd_.get(k, 0.0) <= 1e-9
            for k in ("p_edge_absolute_drift", "p_edge_relative_drift", "p_failure_drift")
        )

    @property
    def control_pass(self) -> bool:
        cd = self.control_drift
        return cd.get("action_match", False)

    @property
    def eia_pass(self) -> bool:
        return self.eia_drift.get("identifiability_match", False)

    def all_pass(self) -> bool:
        return all([
            self.tensor_pass,
            self.probability_pass,
            self.control_pass,
            self.eia_pass,
        ])

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "summary": {
                "all_pass": self.all_pass(),
                SrvCategory.TENSOR: SrvStatus.PASS if self.tensor_pass else SrvStatus.FAIL,
                SrvCategory.PROBABILITY: SrvStatus.PASS if self.probability_pass else SrvStatus.FAIL,
                SrvCategory.CONTROL: SrvStatus.PASS if self.control_pass else SrvStatus.FAIL,
                SrvCategory.EIA: SrvStatus.PASS if self.eia_pass else SrvStatus.FAIL,
            },
            "details": {
                SrvCategory.TENSOR: self.tensor_drift,
                SrvCategory.PROBABILITY: self.probability_drift,
                SrvCategory.CONTROL: self.control_drift,
                SrvCategory.EIA: self.eia_drift,
            },
            "sizes": {
                "live_trades": self.n_live_trades,
                "replay_trades": self.n_replay_trades,
                "events_replayed": self.n_events_replayed,
            },
        }
