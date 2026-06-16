from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class AggTrade:
    symbol: str
    trade_id: str
    price: Decimal
    quantity: Decimal
    is_buyer_maker: bool
    ts: int
