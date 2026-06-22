from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base_event import TruthEvent, TruthEventType


@dataclass(frozen=True)
class ControlTruthEvent:
    """Truth event for a control layer decision."""

    run_id: str
    symbol: str
    action: str  # HALT | REDUCE | CONTINUE
    h_interno: float
    h_externo: float
    hazard_immediate: float
    hazard_cumulative: float
    reason: str
    days_in_reduce: int
    timestamp: str = ""

    def to_truth_event(self, parent_hash: str = "") -> TruthEvent:
        payload = {
            "action": self.action,
            "h_interno": self.h_interno,
            "h_externo": self.h_externo,
            "hazard_immediate": self.hazard_immediate,
            "hazard_cumulative": self.hazard_cumulative,
            "reason": self.reason,
            "days_in_reduce": self.days_in_reduce,
        }
        ts = self.timestamp if self.timestamp else TruthEvent.now_utc()
        event = TruthEvent(
            event_type=TruthEventType.CONTROL,
            timestamp=ts,
            run_id=self.run_id,
            symbol=self.symbol,
            payload=payload,
        )
        return event.with_chain(parent_hash)

    def to_payload(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "h_interno": self.h_interno,
            "h_externo": self.h_externo,
            "hazard_immediate": self.hazard_immediate,
            "hazard_cumulative": self.hazard_cumulative,
            "reason": self.reason,
            "days_in_reduce": self.days_in_reduce,
        }
