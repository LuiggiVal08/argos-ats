"""ExchangePositionProvider port — fetch positions from exchange for reconciliation."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ExchangePosition:
    symbol: str
    side: str
    quantity: Decimal
    entry_price: Decimal
    current_price: Decimal
    unrealized_pnl: Decimal | None = None


class ExchangePositionProviderError(RuntimeError):
    """Raised when exchange position fetch fails."""


@runtime_checkable
class ExchangePositionProvider(Protocol):
    async def fetch_positions(self) -> list[ExchangePosition]:
        """Fetch all current positions from the exchange.
        Raises ExchangePositionProviderError on I/O failure."""
        ...
