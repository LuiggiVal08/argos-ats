from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base_event import TruthEvent, TruthEventType


@dataclass(frozen=True)
class TensorTruthEvent:
    """Truth event for an Φ edge tensor snapshot."""

    run_id: str
    symbol: str
    n_trades: int
    directional_mean: float
    directional_identifiable: bool
    timing_mean: float
    timing_identifiable: bool
    execution_mean: float
    execution_identifiable: bool
    structural_mean: float
    structural_identifiable: bool
    timestamp: str = ""

    def to_truth_event(self, parent_hash: str = "") -> TruthEvent:
        payload = {
            "n_trades": self.n_trades,
            "directional": {
                "mean": self.directional_mean,
                "identifiable": self.directional_identifiable,
            },
            "timing": {
                "mean": self.timing_mean,
                "identifiable": self.timing_identifiable,
            },
            "execution": {
                "mean": self.execution_mean,
                "identifiable": self.execution_identifiable,
            },
            "structural": {
                "mean": self.structural_mean,
                "identifiable": self.structural_identifiable,
            },
        }
        ts = self.timestamp if self.timestamp else TruthEvent.now_utc()
        event = TruthEvent(
            event_type=TruthEventType.TENSOR,
            timestamp=ts,
            run_id=self.run_id,
            symbol=self.symbol,
            payload=payload,
        )
        return event.with_chain(parent_hash)

    def to_payload(self) -> dict[str, Any]:
        return {
            "n_trades": self.n_trades,
            "directional": {
                "mean": self.directional_mean,
                "identifiable": self.directional_identifiable,
            },
            "timing": {
                "mean": self.timing_mean,
                "identifiable": self.timing_identifiable,
            },
            "execution": {
                "mean": self.execution_mean,
                "identifiable": self.execution_identifiable,
            },
            "structural": {
                "mean": self.structural_mean,
                "identifiable": self.structural_identifiable,
            },
        }
