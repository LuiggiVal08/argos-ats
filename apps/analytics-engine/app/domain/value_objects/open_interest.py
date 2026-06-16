from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class OpenInterest:
    symbol: str
    open_interest: Decimal
    ts: int
