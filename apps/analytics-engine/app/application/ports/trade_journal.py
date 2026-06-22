"""TradeJournal port.

Append-only log of closed trades with realised P&L. The drawdown
use case sums the day's P&L events to compute the current
balance from the snapshot's starting balance.

Sad path: any I/O failure (write to disk, network to remote
store) raises TradeJournalError. The use case aborts the
drawdown check on this error.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable


class TradeJournalError(RuntimeError):
    """Raised when the journal can't be read or written."""


@dataclass(frozen=True)
class TradeRecord:
    """A single closed trade with realised P&L in quote currency."""
    symbol: str
    realized_pnl: Decimal       # positive = gain, negative = loss
    closed_at: datetime
    reference: str              # exchange order id or local tag
    event_id: str = ""          # UUID v7 for event sourcing linkage (Fix 4)


@runtime_checkable
class TradeJournal(Protocol):
    async def add(self, record: TradeRecord) -> None:
        """Append a closed trade. Raises TradeJournalError on I/O."""
        ...

    async def realized_pnl_since(
        self, since_utc: datetime
    ) -> Decimal:
        """Sum of `realized_pnl` for records with `closed_at >= since_utc`.
        Raises TradeJournalError on I/O. Returns 0 if no records."""
        ...
