from __future__ import annotations

from abc import ABC, abstractmethod

from ...domain.value_objects.portfolio_context_snapshot import (
    PortfolioContextSnapshot,
    TradeRecord,
)


class PortfolioContextStore(ABC):
    @abstractmethod
    async def load_context(self, symbol: str) -> PortfolioContextSnapshot:
        ...

    @abstractmethod
    async def record_signal(
        self, symbol: str, direction: str, timestamp: float
    ) -> None:
        ...

    @abstractmethod
    async def save_trade_record(
        self, symbol: str, record: TradeRecord
    ) -> None:
        ...

    @abstractmethod
    async def clear_cluster(self, symbol: str) -> None:
        ...
