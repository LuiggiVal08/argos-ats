"""CcxtBalanceProvider: implements BalanceProvider via ccxt.async_support.

Wraps a single exchange instance and queries `fetch_balance` on
each call. The exchange is provided by the composition root (in
main.py) and shared across requests.

Sad path: ccxt raises `BaseError` subclasses (network, auth,
exchange error). We surface them as `BalanceProviderError` so the
use case can abort with a clean message.
"""
from __future__ import annotations

from decimal import Decimal

import ccxt.async_support as ccxt
import structlog

from ...application.ports.balance_provider import (
    BalanceProvider,
    BalanceProviderError,
)

log = structlog.get_logger()


class CcxtBalanceProvider(BalanceProvider):
    def __init__(self, exchange: ccxt.Exchange) -> None:
        self._exchange = exchange

    async def get_free_balance(self, quote: str = "USDT") -> Decimal:
        try:
            data = await self._exchange.fetch_balance()
        except Exception as e:
            log.error(
                "balance_fetch_failed",
                error=str(e),
                error_type=type(e).__name__,
                quote=quote,
            )
            raise BalanceProviderError(f"ccxt_fetch_balance_failed: {e}") from e

        free = data.get("free", {}) or {}
        total = data.get("total", {}) or {}
        free_keys = sorted(free.keys())
        total_keys = sorted(total.keys())

        raw = free.get(quote)
        source = "free"
        if raw is None:
            raw = total.get(quote)
            source = "total"
        if raw is None:
            log.warning(
                "balance_quote_not_found",
                quote=quote,
                free_keys=free_keys[:10],
                total_keys=total_keys[:10],
            )
            raise BalanceProviderError(
                f"balance_missing_quote: {quote} not in exchange balance"
            )

        log.info(
            "balance_fetch_ok",
            quote=quote,
            source=source,
            raw_value=str(raw),
            free_currencies=len(free_keys),
            total_currencies=len(total_keys),
        )
        try:
            value = Decimal(str(raw))
        except Exception as e:
            raise BalanceProviderError(f"balance_unparseable: {raw}") from e
        if value <= 0:
            raise BalanceProviderError(
                f"balance_invalid: free {quote} = {value} (must be > 0)"
            )
        return value
