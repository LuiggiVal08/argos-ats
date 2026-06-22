"""CcxtExchangePositionProvider — fetches positions from CCXT exchange.

Implements ExchangePositionProvider port using ccxt.async_support.

Used by the RecoveryEngine to reconcile local state vs exchange state.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import ccxt.async_support as ccxt

from ...application.ports.exchange_position_provider import (
    ExchangePosition,
    ExchangePositionProvider,
    ExchangePositionProviderError,
)


class CcxtExchangePositionProvider:
    """Fetches live positions from a CCXT exchange.

    Args:
        exchange: CCXT exchange instance (futures or spot).
        symbols: Tuple of symbols to fetch positions for.
    """

    def __init__(
        self,
        exchange: ccxt.Exchange,
        symbols: tuple[str, ...] = ("BTC/USDT",),
    ) -> None:
        self._exchange = exchange
        self._symbols = symbols

    async def fetch_positions(self) -> list[ExchangePosition]:
        """Fetch all current positions from the exchange.

        Returns a list of ExchangePosition for non-zero positions.
        """
        positions: list[ExchangePosition] = []
        for symbol in self._symbols:
            try:
                raw_positions: list[dict[str, Any]] = await self._exchange.fetch_positions([symbol])
            except Exception as e:
                raise ExchangePositionProviderError(
                    f"fetch_positions_failed: {symbol}: {e}"
                ) from e

            for p in raw_positions:
                amt = Decimal(str(p.get("contracts") or p.get("amount") or 0))
                if amt == 0:
                    continue

                side = str(p.get("side") or "long").lower()
                entry = Decimal(str(p.get("entryPrice") or 0))
                current = Decimal(str(p.get("markPrice") or p.get("lastPrice") or 0))
                upnl = Decimal(str(p.get("unrealizedPnl") or 0))

                positions.append(ExchangePosition(
                    symbol=symbol,
                    side=side,
                    quantity=amt,
                    entry_price=entry,
                    current_price=current,
                    unrealized_pnl=upnl,
                ))

        return positions
