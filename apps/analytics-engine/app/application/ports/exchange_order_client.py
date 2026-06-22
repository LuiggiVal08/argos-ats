"""ExchangeOrderClient port.

The trip use case calls this to:
  - cancel_all_orders(): bulk-cancel every open order on the
    exchange for the configured symbols.
  - close_all_positions(): flatten every active position with a
    market order.

The order-execution use case (H4) calls:
  - place_composite_order(): places a market entry + SL + TP
    bracket order.
  - place_emergency_market(): last-resort liquidation if SL
    placement fails after retry exhaustion.

Sad path: any I/O failure (CCXT timeout, exchange 5xx, auth)
raises ExchangeOrderClientError.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from ...domain.value_objects.order import (
    CompositeOrder,
    OrderResult,
    OrderSide,
    OrderType,
)


class ExchangeOrderClientError(RuntimeError):
    """Raised when any exchange call can't complete."""


class SlPlacementError(ExchangeOrderClientError):
    """Raised when the stop-loss leg of a composite order fails
    after retry exhaustion. The market entry was already placed,
    so the caller MUST issue an emergency close.

    Attributes:
        entry_order: the successful entry OrderResult.
    """

    def __init__(self, message: str, entry_order: OrderResult | None = None) -> None:
        super().__init__(message)
        self.entry_order = entry_order


@dataclass(frozen=True)
class PositionSummary:
    symbol: str
    side: str           # "long" or "short"
    quantity: Decimal
    entry_price: Decimal


@runtime_checkable
class ExchangeOrderClient(Protocol):
    async def cancel_all_orders(self) -> int:
        """Cancel every open order on the configured exchange.
        Returns the number of orders cancelled. Raises
        ExchangeOrderClientError on infrastructure failure."""
        ...

    async def close_all_positions(self) -> list[PositionSummary]:
        """Flatten every active position with a market order.
        Returns the list of positions that were closed. Raises
        ExchangeOrderClientError on infrastructure failure."""
        ...

    async def close_position(self, symbol: str) -> PositionSummary:
        """Close the active position for a single symbol.
        Returns a PositionSummary describing the closed position.
        Raises ExchangeOrderClientError on infrastructure failure."""
        ...

    async def close_partial(self, symbol: str, quantity: Decimal) -> None:
        """Close a partial quantity of a position at market.
        Raises ExchangeOrderClientError on infrastructure failure."""
        ...

    async def place_composite_order(
        self, order: CompositeOrder
    ) -> OrderResult:
        """Place a bracket order: market entry + stop loss +
        take profit linked.

        On entry success but SL failure after retry exhaustion,
        raises SlPlacementError. The caller must call
        place_emergency_market to liquidate the position."""
        ...

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel a single order by its exchange ID.
        Returns True if the order was cancelled, False if already gone.
        Raises ExchangeOrderClientError on infrastructure failure."""
        ...

    async def place_stop_loss_order(
        self,
        symbol: str,
        side: OrderSide,
        amount: Decimal,
        stop_price: Decimal,
    ) -> OrderResult:
        """Place a standalone stop loss (stop_market) order.
        Used to replace SL during break-even / trail updates.
        Raises ExchangeOrderClientError on placement failure."""
        ...

    async def place_emergency_market(
        self, symbol: str, side: OrderSide, amount: Decimal
    ) -> OrderResult:
        """Fire-and-forget market order to liquidate a position.
        No retries. Raises ExchangeOrderClientError on failure."""
        ...
