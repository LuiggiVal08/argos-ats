"""BinanceAlgoAdapter: implements AlgoOrderClient via CCXT fapiPrivate methods.

Uses the Binance Futures Algo API endpoints directly (NOT create_order
or cancel_order, which route to the Regular API).

Endpoints used:
  GET    /fapi/v1/algo/order          → fapiPrivateGetAlgoOrder
  DELETE /fapi/v1/algo/order          → fapiPrivateDeleteAlgoOrder
  GET    /fapi/v1/algo/openOrders     → fapiPrivateGetOpenAlgoOrders

The regular CcxtOrderClient/CcxtBinanceTestnetAdapter continue to use
create_order(type='stop_market') for PLACEMENT — CCXT correctly routes
that to POST /fapi/v1/algo/order/new. Only FETCH/CANCEL need this adapter.
"""
from __future__ import annotations

import structlog
from typing import Any

import ccxt.async_support as ccxt

from ...application.ports.algo_order_client import (
    AlgoOrderClient,
    AlgoOrderClientError,
)

log = structlog.get_logger()


class BinanceAlgoAdapter(AlgoOrderClient):
    """Adapter for Binance Futures Algo API (conditional orders).

    Args:
        exchange: A CCXT exchange instance (binanceusdm or testnet).
    """

    def __init__(self, exchange: ccxt.Exchange) -> None:
        self._exchange = exchange

    async def get_algo_order(self, algo_id: str) -> dict:
        """Fetch a conditional order by algoId.

        Raises AlgoOrderClientError if the API call fails.
        """
        try:
            raw = await self._exchange.fapiPrivateGetAlgoOrder({
                "algoId": algo_id,
            })
            return raw
        except Exception as e:
            raise AlgoOrderClientError(
                f"get_algo_order_failed: algoId={algo_id}: {e}"
            ) from e

    async def cancel_algo_order(self, algo_id: str) -> bool:
        """Cancel a conditional order by algoId.

        Returns True on success (code=200), False if already gone.
        Raises AlgoOrderClientError on infrastructure failure.
        """
        try:
            raw = await self._exchange.fapiPrivateDeleteAlgoOrder({
                "algoId": algo_id,
            })
            return raw.get("code") == "200"
        except Exception as e:
            err_str = str(e).lower()
            if "unknown order" in err_str or "does not exist" in err_str:
                return False
            raise AlgoOrderClientError(
                f"cancel_algo_order_failed: algoId={algo_id}: {e}"
            ) from e

    async def list_open_algo_orders(self, symbol: str | None = None) -> list[dict]:
        """List all open conditional orders.

        The Algo API does not support server-side symbol filtering,
        so we filter client-side when symbol is provided.
        """
        try:
            raw_list = await self._exchange.fapiPrivateGetOpenAlgoOrders()
        except Exception as e:
            raise AlgoOrderClientError(
                f"list_open_algo_orders_failed: {e}"
            ) from e

        if not raw_list:
            return []

        orders: list[dict] = list(raw_list)
        if symbol is not None:
            normalized = symbol.replace("/", "").upper()
            orders = [o for o in orders if o.get("symbol", "").upper() == normalized]

        return orders

    async def is_order_active(self, algo_id: str) -> bool:
        """Check if a conditional order is active (algoStatus=NEW).

        Returns False (not an error) if the order doesn't exist or
        has already been triggered/cancelled.

        Only raises AlgoOrderClientError on actual infrastructure failure.
        """
        try:
            raw = await self._exchange.fapiPrivateGetAlgoOrder({
                "algoId": algo_id,
            })
            status = raw.get("algoStatus", "")
            return status == "NEW"
        except Exception as e:
            err_str = str(e).lower()
            if "unknown order" in err_str or "does not exist" in err_str:
                return False
            raise AlgoOrderClientError(
                f"is_order_active_failed: algoId={algo_id}: {e}"
            ) from e
