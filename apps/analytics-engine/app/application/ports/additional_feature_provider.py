"""AdditionalFeatureProvider port.

Proporciona features de mercado en tiempo real (funding rate, OI,
order flow) que no se pueden computar desde OHLCV.

Estas features se inyectan en el pipeline de predicción como
contexto adicional del MetaModel, complementando las features
técnicas base del DataPreprocessor.

Sad paths:
  - AdditionalFeatureError: fallo al obtener datos del broker
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


class AdditionalFeatureError(RuntimeError):
    """Raised when fetching additional features fails."""


@runtime_checkable
class AdditionalFeatureProvider(Protocol):
    """Provides real-time market features for prediction enrichment.

    Implementaciones concretas leen de Redis streams (funding rates,
    open interest, order flow) y retornan un dict con las features
    disponibles. Si no hay datos para el symbol, retorna dict vacio.
    """

    async def get_features(self, symbol: str) -> dict[str, float]:
        """Get additional market features for a symbol.

        Returns dict like:
            funding_rate:        tasa de funding actual
            funding_momentum:    cambio relativo en funding (EMA diff)
            oi_change_pct:       cambio % en open interest (24h)
            orderflow_imbalance: (buy_vol - sell_vol) / (buy_vol + sell_vol)
            orderflow_buy_vol:   volumen de compra agresiva
            orderflow_sell_vol:  volumen de venta agresiva
            orderflow_trade_count: numero total de trades agresivos

        Returns empty dict if no data available for the symbol.
        """
        ...
