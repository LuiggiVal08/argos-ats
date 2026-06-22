"""FundingRateProvider — AdditionalFeatureProvider para funding rate de futuros.

Usa CCXT sobre Binance Futures (testnet o mainnet) para obtener:
  - funding_rate: tasa actual
  - funding_momentum: rate - EMA suave (alpha=0.1)
  - funding_change: rate - rate_anterior

Cachea 5 minutos (la funding rate cambia cada 8h en Binance).
Si la consulta falla, retorna 0.0 (degradación controlada).
"""
from __future__ import annotations

import time
from typing import Any

import structlog

from ...application.ports.additional_feature_provider import (
    AdditionalFeatureProvider,
)

log = structlog.get_logger()

_CACHE_TTL_S = 300
_EMA_ALPHA = 0.1


class FundingRateProvider:
    def __init__(self, exchange: Any) -> None:
        self._exchange = exchange
        self._cached: dict[str, float] = {}
        self._last_fetch: float = 0.0
        self._ema_rate: float = 0.0
        self._prev_rate: float = 0.0
        self._has_history: bool = False

    async def get_features(self, symbol: str) -> dict[str, float]:
        now = time.time()
        if now - self._last_fetch > _CACHE_TTL_S:
            try:
                rate = await self._fetch_rate(symbol)
                self._last_fetch = now

                if self._has_history:
                    change = rate - self._prev_rate
                else:
                    change = 0.0

                if self._has_history:
                    self._ema_rate = _EMA_ALPHA * rate + (1 - _EMA_ALPHA) * self._ema_rate
                else:
                    self._ema_rate = rate

                momentum = rate - self._ema_rate

                self._prev_rate = rate
                self._has_history = True

                self._cached = {
                    "funding_rate": rate,
                    "funding_momentum": momentum,
                    "funding_change": change,
                }
            except Exception as exc:
                log.warning(
                    "funding_rate_fetch_failed",
                    symbol=symbol,
                    error=str(exc),
                )
                if not self._cached:
                    self._cached = {
                        "funding_rate": 0.0,
                        "funding_momentum": 0.0,
                        "funding_change": 0.0,
                    }

        return dict(self._cached)

    async def _fetch_rate(self, symbol: str) -> float:
        ccxt_symbol = symbol.replace("/", "/")
        ticker = await self._exchange.fetch_funding_rate(ccxt_symbol)
        rate = float(ticker.get("fundingRate", 0.0) or 0.0)
        return rate
