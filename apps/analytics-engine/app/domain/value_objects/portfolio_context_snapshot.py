from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class TradeRecord:
    direction: str
    outcome: str | None
    regime: str
    timestamp: float
    confidence: float


@dataclass(frozen=True)
class SignalCluster:
    cluster_id: str
    direction: str
    first_timestamp: float
    last_timestamp: float
    signal_count: int


@dataclass(frozen=True)
class PortfolioContextSnapshot:
    symbol: str
    last_trade: TradeRecord | None = None
    active_cluster: SignalCluster | None = None
    same_direction_count_6h: int = 0
    last_same_direction_ts: float = 0.0
