from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base_event import TruthEvent, TruthEventType


@dataclass(frozen=True)
class EpisodeTruthEvent:
    """Truth event for episode creation or settlement."""

    run_id: str
    symbol: str
    episode_id: str
    t_entry: str
    t_exit: str | None = None
    side: str = ""
    pnl: float = 0.0
    regime_at_entry: str = ""
    event_subtype: str = "created"  # "created" | "settled"

    def to_truth_event(self, parent_hash: str = "") -> TruthEvent:
        payload = {
            "episode_id": self.episode_id,
            "t_entry": self.t_entry,
            "t_exit": self.t_exit,
            "side": self.side,
            "pnl": self.pnl,
            "regime_at_entry": self.regime_at_entry,
            "event_subtype": self.event_subtype,
        }
        ts = self.t_exit if self.t_exit else self.t_entry
        event = TruthEvent(
            event_type=TruthEventType.EPISODE,
            timestamp=ts,
            run_id=self.run_id,
            symbol=self.symbol,
            payload=payload,
        )
        return event.with_chain(parent_hash)

    def to_payload(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "t_entry": self.t_entry,
            "t_exit": self.t_exit,
            "side": self.side,
            "pnl": self.pnl,
            "regime_at_entry": self.regime_at_entry,
            "event_subtype": self.event_subtype,
        }
