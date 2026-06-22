from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from .order import OrderSide


@dataclass(frozen=True)
class LivePosition:
    """Posición abierta en el exchange, trackeada por el execution engine.

    Attributes:
        position_id:   ID único de la posición.
        symbol:        Par operado.
        side:          BUY (long) o SELL (short).
        units:         Cantidad de contratos/unidades actual (puede
                       reducirse tras cierres parciales).
        entry_price:   Precio de entrada promedio.
        current_price: Último precio conocido (se actualiza vía tick).
        sl_price:      Stop Loss actual.
        tp_price:      Take Profit 1 (50% de la posición por defecto).
        tp2_price:     Take Profit 2 (25% si se configura).
        tp3_price:     Take Profit 3 (25% trailing stop, opcional).
        trail_activated: Si el trailing stop está activo.
        trail_offset:  Distancia del trailing stop (ej: 1.5 × ATR).
        break_even_activated: SL movido a entry price.
        atr_at_entry:  ATR al momento de abrir la posición.
        risk_multiple: Múltiplo de riesgo alcanzado (1R, 2R, etc).
        initial_units: Cantidad original antes de cierres parciales.
        tp1_pct:       % a cerrar en TP1 (default 0.5 = 50%).
        tp2_pct:       % a cerrar en TP2 (default 0.25 = 25%).
        unrealized_pnl: P&L no realizado en USD (aprox).
        opened_at:     Timestamp de apertura (UTC).
        closed_at:     Timestamp de cierre (UTC), None si abierta.
        realized_pnl:  P&L realizado al cierre (None si abierta).
        status:        "OPEN" | "PARTIALLY_CLOSED" | "CLOSED" | "SL_HIT" | "TP_HIT".
        metadata:      Dict opcional.
    """

    position_id: str
    symbol: str
    side: OrderSide
    units: Decimal
    entry_price: Decimal
    current_price: Decimal
    sl_price: Decimal | None = None
    tp_price: Decimal | None = None
    tp2_price: Decimal | None = None
    tp3_price: Decimal | None = None
    trail_activated: bool = False
    trail_offset: Decimal | None = None
    break_even_activated: bool = False
    atr_at_entry: Decimal | None = None
    risk_multiple: Decimal = Decimal("0")
    initial_units: Decimal | None = None
    tp1_pct: Decimal = Decimal("0.5")
    tp2_pct: Decimal = Decimal("0.25")
    unrealized_pnl: Decimal = Decimal("0")
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at: datetime | None = None
    realized_pnl: Decimal | None = None
    status: str = "OPEN"
    lineage_id: str = ""
    sl_order_id: str | None = None
    tp_order_id: str | None = None
    metadata: dict = field(default_factory=dict)

    VALID_STATUSES = ("OPEN", "PARTIALLY_CLOSED", "CLOSED", "SL_HIT", "TP_HIT", "CRITICAL_UNPROTECTED")

    def __post_init__(self) -> None:
        if self.units <= 0:
            raise ValueError(f"units must be > 0, got {self.units}")
        if self.entry_price <= 0:
            raise ValueError(f"entry_price must be > 0, got {self.entry_price}")
        if self.current_price <= 0:
            raise ValueError(f"current_price must be > 0, got {self.current_price}")
        if self.status not in self.VALID_STATUSES:
            raise ValueError(
                f"status must be {'/'.join(self.VALID_STATUSES)}, got {self.status!r}"
            )
        if self.sl_price is not None and self.sl_price <= 0:
            raise ValueError(f"sl_price must be > 0 or None, got {self.sl_price}")
        if self.tp2_price is not None and self.tp2_price <= 0:
            raise ValueError(f"tp2_price must be > 0 or None, got {self.tp2_price}")
        if self.tp3_price is not None and self.tp3_price <= 0:
            raise ValueError(f"tp3_price must be > 0 or None, got {self.tp3_price}")
        if self.trail_offset is not None and self.trail_offset <= 0:
            raise ValueError(f"trail_offset must be > 0 or None, got {self.trail_offset}")
        if self.initial_units is None:
            object.__setattr__(self, "initial_units", self.units)
        if not (Decimal("0") <= self.tp1_pct <= Decimal("1")):
            raise ValueError(f"tp1_pct must be in [0, 1], got {self.tp1_pct}")
        if not (Decimal("0") <= self.tp2_pct <= Decimal("1")):
            raise ValueError(f"tp2_pct must be in [0, 1], got {self.tp2_pct}")

    @property
    def is_open(self) -> bool:
        return self.status in ("OPEN", "PARTIALLY_CLOSED")

    @property
    def remaining_pct(self) -> Decimal:
        """Fraction of initial position still open."""
        if self.initial_units and self.initial_units > 0:
            return self.units / self.initial_units
        return Decimal("1")

    @property
    def pnl_pct(self) -> Decimal:
        if self.entry_price == 0:
            return Decimal("0")
        if self.side == OrderSide.BUY:
            return (self.current_price - self.entry_price) / self.entry_price
        return (self.entry_price - self.current_price) / self.entry_price

    def is_sl_hit(self, price: Decimal) -> bool:
        if self.sl_price is None:
            return False
        if self.side == OrderSide.BUY:
            return price <= self.sl_price
        return price >= self.sl_price

    def is_tp_hit(self, price: Decimal, tp_level: int = 1) -> bool:
        target = {1: self.tp_price, 2: self.tp2_price, 3: self.tp3_price}.get(tp_level)
        if target is None:
            return False
        if self.side == OrderSide.BUY:
            return price >= target
        return price <= target

    def compute_pnl_at(self, price: Decimal) -> Decimal:
        if self.side == OrderSide.BUY:
            return (price - self.entry_price) * self.units
        return (self.entry_price - price) * self.units

    def compute_risk_amount(self) -> Decimal | None:
        if self.atr_at_entry is None or self.atr_at_entry <= 0:
            return None
        return self.atr_at_entry * self.units
