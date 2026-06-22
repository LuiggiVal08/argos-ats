from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base_event import TruthEvent, TruthEventType


@dataclass(frozen=True)
class TradeTruthEvent:
    """Truth event for a trade close/update."""

    run_id: str
    symbol: str
    entry_ts: str
    exit_ts: str
    side: str
    entry_price: float
    exit_price: float
    size: float
    gross_pnl: float
    costs: float
    net_pnl: float
    duration_bars: int
    exit_reason: str

    def to_truth_event(self, parent_hash: str = "") -> TruthEvent:
        payload = {
            "entry_ts": self.entry_ts,
            "exit_ts": self.exit_ts,
            "side": self.side,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "size": self.size,
            "gross_pnl": self.gross_pnl,
            "costs": self.costs,
            "net_pnl": self.net_pnl,
            "duration_bars": self.duration_bars,
            "exit_reason": self.exit_reason,
        }
        event = TruthEvent(
            event_type=TruthEventType.TRADE,
            timestamp=self.exit_ts,
            run_id=self.run_id,
            symbol=self.symbol,
            payload=payload,
        )
        return event.with_chain(parent_hash)

    def to_payload(self) -> dict[str, Any]:
        return {
            "entry_ts": self.entry_ts,
            "exit_ts": self.exit_ts,
            "side": self.side,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "size": self.size,
            "gross_pnl": self.gross_pnl,
            "costs": self.costs,
            "net_pnl": self.net_pnl,
            "duration_bars": self.duration_bars,
            "exit_reason": self.exit_reason,
        }
