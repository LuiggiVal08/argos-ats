from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from .signal_side import SignalSide


@dataclass(frozen=True)
class ExecutionSignal:
    """Señal validada y lista para ejecutar.

    A diferencia de TradingSignal (salida cruda del modelo), esta
    señal ya pasó por validación (confidence >= threshold, cooldown,
    dedup) y está lista para ser ejecutada por el orquestador.

    Attributes:
        signal_id:    ID único para deduplicación.
        lineage_id:   GlobalEventChainID — todos los eventos derivados
                      comparten este ID (Fix 2).
        side:         Dirección (BUY/SELL — nunca HOLD).
        confidence:   Confianza post-validación (threshold aplicado).
        symbol:       Par a operar (ej. BTC/USDT).
        strategy_id:  Estrategia que generó la señal (ej. "novaquant").
        price:        Precio estimado de entrada (opcional, del tick).
        timestamp:    Momento de generación (UTC).
        metadata:     Dict opcional para trazabilidad.
    """

    side: SignalSide
    confidence: float
    symbol: str
    signal_id: str = field(default_factory=lambda: uuid4().hex[:12])
    lineage_id: str = ""
    strategy_id: str = ""
    price: Decimal | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"confidence must be in [0, 1], got {self.confidence}"
            )
        if self.side == SignalSide.HOLD:
            raise ValueError("ExecutionSignal must not be HOLD")
        if not self.symbol or not isinstance(self.symbol, str):
            raise ValueError(f"symbol must be a non-empty string, got {self.symbol!r}")
