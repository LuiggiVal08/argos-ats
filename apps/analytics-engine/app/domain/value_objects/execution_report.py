from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from .order import OrderSide, OrderStatus


@dataclass(frozen=True)
class ExecutionReport:
    """Reporte de una ejecución de señal.

    Attributes:
        report_id:     ID único del reporte.
        signal_id:     ID de la señal que originó la ejecución.
        symbol:        Par operado.
        side:          Dirección ejecutada.
        status:        Estado de la ejecución (FILLED, PARTIALLY_FILLED,
                       REJECTED, FAILED).
        filled_qty:    Cantidad realmente ejecutada.
        avg_price:     Precio promedio de ejecución (None si no se llenó).
        pnl:           P&L realizado de la operación (None si abierta).
        order_id:      ID de la orden en el exchange.
        position_id:   ID de la posición generada.
        errors:        Lista de errores si ocurrieron.
        timestamp:     Momento de la ejecución (UTC).
        metadata:      Dict opcional.
    """

    report_id: str
    signal_id: str
    symbol: str
    side: OrderSide
    status: str
    filled_qty: Decimal
    avg_price: Decimal | None = None
    pnl: Decimal | None = None
    order_id: str = ""
    position_id: str = ""
    lineage_id: str = ""
    errors: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.filled_qty < 0:
            raise ValueError(f"filled_qty must be >= 0, got {self.filled_qty}")
        if self.status not in ("FILLED", "PARTIALLY_FILLED", "REJECTED", "FAILED", "CANCELLED"):
            raise ValueError(
                f"invalid status {self.status!r}"
            )
        if self.avg_price is not None and self.avg_price <= 0:
            raise ValueError(f"avg_price must be > 0 or None, got {self.avg_price}")
