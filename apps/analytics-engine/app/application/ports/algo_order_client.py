"""AlgoOrderClient port — Binance Futures Algo API (conditional orders).

Binance Futures has two separate order systems:
  - Regular API  (/fapi/v1/order):  MARKET, LIMIT
  - Algo API     (/fapi/v1/algo/order): STOP_MARKET, TAKE_PROFIT_MARKET

STOP_MARKET and TAKE_PROFIT_MARKET orders (SL/TP) live in the Algo API.
They CANNOT be fetched, cancelled, or verified via the Regular API.

This port exposes only Algo API operations. The concrete adapter uses
CCXT's fapiPrivate* methods (not create_order/cancel_order/fetch_order)
to hit the correct Algo API endpoints.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AlgoOrderClient(Protocol):
    """Client for Binance Futures Algo API (conditional orders).

    All methods raise AlgoOrderClientError on infrastructure failure.
    """

    async def get_algo_order(self, algo_id: str) -> dict:
        """Fetch a conditional order by its algoId.

        Returns the raw Algo API response dict with keys:
          algoId, algoStatus, orderType, symbol, side,
          triggerPrice, quantity, reduceOnly, etc.

        Raises AlgoOrderClientError if the order doesn't exist or
        the API call fails.
        """
        ...

    async def cancel_algo_order(self, algo_id: str) -> bool:
        """Cancel a conditional order by its algoId.

        Returns True if the order was cancelled successfully.
        Returns False if the order was already gone (idempotent).

        Raises AlgoOrderClientError on infrastructure failure.
        """
        ...

    async def list_open_algo_orders(self, symbol: str | None = None) -> list[dict]:
        """List all open conditional orders.

        If symbol is provided, filters to that symbol client-side
        (the Algo API does not support server-side symbol filtering).

        Returns a list of Algo API response dicts.
        Raises AlgoOrderClientError on failure.
        """
        ...

    async def is_order_active(self, algo_id: str) -> bool:
        """Check if a conditional order is still active (algoStatus=NEW).

        Returns True if the order exists and has algoStatus=NEW.
        Returns False if the order doesn't exist or was triggered/cancelled.
        Does NOT raise on "order not found" — only on infra failures.
        """
        ...


class AlgoOrderClientError(RuntimeError):
    """Raised when any Algo API call can't complete."""
