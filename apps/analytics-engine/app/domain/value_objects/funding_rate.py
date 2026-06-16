from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class FundingRate:
    symbol: str
    funding_rate: Decimal
    mark_price: Decimal
    index_price: Decimal
    next_funding_time: int
    ts: int
