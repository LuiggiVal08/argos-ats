from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base_event import TruthEvent, TruthEventType


@dataclass(frozen=True)
class InferenceTruthEvent:
    """Truth event for inference engine output.

    Records the full inference state:
    - P(edge|D) absolute and relative
    - P(failure|D)
    - U(model) and U(H₀)
    - EIA validation status
    """

    run_id: str
    symbol: str
    p_edge_absolute: float
    p_edge_relative: float
    p_failure: float
    utility_model: float
    utility_h0_median: float
    eia_passed: bool
    eia_separability: bool
    eia_null_invariance: bool
    eia_representation_stability: bool
    n_trades: int
    n_bars: int
    timestamp: str = ""

    def to_truth_event(self, parent_hash: str = "") -> TruthEvent:
        payload = {
            "p_edge_absolute": self.p_edge_absolute,
            "p_edge_relative": self.p_edge_relative,
            "p_failure": self.p_failure,
            "utility_model": self.utility_model,
            "utility_h0_median": self.utility_h0_median,
            "eia": {
                "passed": self.eia_passed,
                "separability": self.eia_separability,
                "null_invariance": self.eia_null_invariance,
                "representation_stability": self.eia_representation_stability,
            },
            "n_trades": self.n_trades,
            "n_bars": self.n_bars,
        }
        ts = self.timestamp if self.timestamp else TruthEvent.now_utc()
        event = TruthEvent(
            event_type=TruthEventType.INFERENCE,
            timestamp=ts,
            run_id=self.run_id,
            symbol=self.symbol,
            payload=payload,
        )
        return event.with_chain(parent_hash)

    def to_payload(self) -> dict[str, Any]:
        return {
            "p_edge_absolute": self.p_edge_absolute,
            "p_edge_relative": self.p_edge_relative,
            "p_failure": self.p_failure,
            "utility_model": self.utility_model,
            "utility_h0_median": self.utility_h0_median,
            "eia": {
                "passed": self.eia_passed,
                "separability": self.eia_separability,
                "null_invariance": self.eia_null_invariance,
                "representation_stability": self.eia_representation_stability,
            },
            "n_trades": self.n_trades,
            "n_bars": self.n_bars,
        }
