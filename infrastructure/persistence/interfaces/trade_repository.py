from __future__ import annotations

from datetime import datetime
from typing import Protocol

from infrastructure.persistence.dto.trade_dto import TradeDTO


class TradeRepository(Protocol):
    def append_trade(self, trade: TradeDTO) -> None: ...
    def get_trades(self, symbol: str, start: datetime, end: datetime) -> list[TradeDTO]: ...
    def replay(self) -> list[TradeDTO]: ...
