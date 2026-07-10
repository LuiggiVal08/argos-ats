"""InMemoryTradeJournal: keeps a list of TradeRecord in process memory.

Used in BACKTESTING mode and unit tests. The list grows
unbounded for now; a long-running process should swap in a
SQLite/Redis adapter (H3-FU1).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from ...application.ports.trade_journal import (
    TradeJournal,
    TradeJournalError,
    TradeRecord,
)


class InMemoryTradeJournal(TradeJournal):
    def __init__(self) -> None:
        self._records: list[TradeRecord] = []

    async def add(self, record: TradeRecord) -> None:
        self._records.append(record)

    async def realized_pnl_since(self, since_utc: datetime) -> Decimal:
        total = Decimal("0")
        for r in self._records:
            if r.closed_at >= since_utc:
                total += r.realized_pnl
        return total

    async def get_all(self) -> list:
        return list(self._records)
