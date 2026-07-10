from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True)
class PositionState:
    """Estado inmutable de una posición individual.

    Transiciones válidas:
      NONE → OPEN (PositionOpened)
      OPEN → OPEN (PositionUpdated — SL/TP adjustment)
      OPEN → CLOSED (PositionClosed)
    """
    status: str = "NONE"  # NONE | OPEN | CLOSED
    position_id: str = ""
    symbol: str = ""
    side: str = ""  # LONG | SHORT
    entry_price: float = 0.0
    quantity: float = 0.0
    sl_price: float | None = None
    tp_price: float | None = None
    pnl: float = 0.0
    close_price: float | None = None
    close_reason: str = ""
    hold_bars: int = 0

    @property
    def is_open(self) -> bool:
        return self.status == "OPEN"

    @property
    def is_closed(self) -> bool:
        return self.status == "CLOSED"


@dataclass(frozen=True)
class RiskState:
    """Estado inmutable del risk engine.

    Transiciones válidas:
      ACTIVE ↔ DEGRADED (RiskStateChanged)
      ACTIVE/DEGRADED → HALT (RiskStateChanged)
      HALT → RECOVERY_PENDING (RiskStateChanged)
      RECOVERY_PENDING → ACTIVE | HALT (RiskStateChanged)
      * → RiskEvaluated (no cambia status, solo métricas)
    """
    status: str = "ACTIVE"  # ACTIVE | DEGRADED | HALT | RECOVERY_PENDING
    drawdown_pct: float = 0.0
    daily_pnl: float = 0.0
    current_balance: float = 0.0
    starting_balance: float = 0.0
    circuit_breaker: str = "NORMAL"  # NORMAL | WARN | TRIP
    p_failure_rolling: tuple[float, ...] = ()

    @property
    def is_halted(self) -> bool:
        return self.status == "HALT"


@dataclass(frozen=True)
class SignalState:
    """Última señal generada/rechazada por symbol.

    No es un state machine — es un último-valor-conocido.
    """
    signal_id: str = ""
    side: str = "HOLD"  # BUY | SELL | HOLD
    confidence: float = 0.0
    model_version: str = ""
    regime: str = ""
    rejected: bool = False
    reject_reason: str = ""


@dataclass(frozen=True)
class OutputState:
    """Estado global reconstruido por el ReplayEngine.

    Contiene todos los substates por entidad. Es un snapshot
    determinista: mismo set de eventos → mismo OutputState.

    Los MappingProxyType garantizan inmutabilidad de contenido.
    """
    positions: Any = MappingProxyType({})  # dict[str, PositionState]
    signals: Any = MappingProxyType({})    # dict[str, SignalState]
    risk: RiskState = field(default_factory=lambda: RiskState())

    @classmethod
    def empty(cls) -> OutputState:
        return cls(
            positions=MappingProxyType({}),
            signals=MappingProxyType({}),
            risk=RiskState(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_positions": len(self.positions),
            "n_signals": len(self.signals),
            "risk_status": self.risk.status,
            "risk_circuit_breaker": self.risk.circuit_breaker,
            "risk_drawdown_pct": round(self.risk.drawdown_pct, 4),
            "open_positions": [
                {"id": p.position_id, "symbol": p.symbol, "side": p.side, "qty": p.quantity}
                for p in self.positions.values()
                if p.is_open
            ],
            "last_signals": {
                sym: {"side": s.side, "confidence": s.confidence}
                for sym, s in self.signals.items()
            },
        }


def _entity_id(event: Any) -> str:
    """Extract entity_id from a DomainEvent for partitioning.

    Rules:
      - Position events → data.position_id
      - Signal events → data.symbol (or 'unknown')
      - Order events → data.order_id (or data.symbol)
      - Risk events → 'system'
      - Config/Simulation events → 'system'
    """
    from ...domain.value_objects.domain_event import DomainEvent

    if not isinstance(event, DomainEvent):
        return "unknown"

    et = event.event_type
    d = event.data or {}

    if et in ("PositionOpened", "PositionUpdated", "PositionClosed"):
        return d.get("position_id", "unknown_pos")
    elif et in ("SignalGenerated", "SignalRejected"):
        return d.get("symbol", "unknown_sym")
    elif et in ("OrderCreated", "OrderFilled", "OrderCancelled"):
        return d.get("order_id", d.get("symbol", "unknown_order"))
    else:
        return "system"


STREAM_PRECEDENCE: dict[str, int] = {
    "RiskEvaluated": 0,
    "RiskLimitBreached": 0,
    "RiskStateChanged": 0,
    "SimulationEvaluated": 0,
    "MonteCarloCompleted": 0,
    "SignalGenerated": 1,
    "SignalRejected": 1,
    "OrderCreated": 2,
    "OrderFilled": 2,
    "OrderCancelled": 2,
    "PositionOpened": 3,
    "PositionUpdated": 3,
    "PositionClosed": 3,
    "ConfigChanged": 4,
    "FillConfirmed": 5,
    "BalanceChangeDetected": 5,
}


def _stream_priority(event_type: str) -> int:
    return STREAM_PRECEDENCE.get(event_type, 99)
