from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TradeDTO:
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
    experiment_id: str = ""
