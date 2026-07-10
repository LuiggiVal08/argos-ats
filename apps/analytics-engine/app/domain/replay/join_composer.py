"""JoinComposer — composición pura de RiskState + PositionState → SystemState.

Contrato (EMC v1 §7.2):
  - PURA:         Join(P, R) siempre produce el mismo SystemState para los mismos inputs.
  - SIN EFECTOS:  Join no escribe eventos, no muta estado externo, no hace I/O.
  - SIN DECISIÓN: Join no evalúa si HALT > ACTIVE. Solo refleja el RiskState recibido.
  - SIN EXCEPCIÓN: Join no puede fallar. Si un input es inválido, el error se detecta
                   en Validator, no en Join.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ...domain.entities.replay_state import OutputState, RiskState


@dataclass(frozen=True)
class SystemState:
    """Estado compuesto del sistema.

    Composición pura de PositionState + RiskState.
    """
    risk: RiskState
    positions: dict[str, Any]
    signals: dict[str, Any]
    mode: str
    version: str = "1.0"
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "mode": self.mode,
            "risk": {
                "status": self.risk.status,
                "drawdown_pct": round(self.risk.drawdown_pct, 4),
                "daily_pnl": round(self.risk.daily_pnl, 2),
                "current_balance": round(self.risk.current_balance, 2),
                "starting_balance": round(self.risk.starting_balance, 2),
                "circuit_breaker": self.risk.circuit_breaker,
            },
            "positions": {
                pid: {
                    "symbol": p.symbol,
                    "side": p.side,
                    "quantity": p.quantity,
                    "entry_price": p.entry_price,
                    "pnl": round(p.pnl, 2),
                    "status": p.status,
                    "hold_bars": p.hold_bars,
                }
                for pid, p in self.positions.items()
            },
            "signals": {
                sym: {
                    "side": s.side,
                    "confidence": s.confidence,
                    "rejected": s.rejected,
                }
                for sym, s in self.signals.items()
            },
            "n_positions": len(self.positions),
            "n_signals": len(self.signals),
        }


def compose(
    output_state: OutputState,
    mode: str = "UNKNOWN",
    generated_at: str | None = None,
) -> SystemState:
    """Componer un SystemState a partir del OutputState del replay.

    Args:
        output_state: Estado del replay (contiene risk + positions + signals).
        mode: Environment mode (LIVE_SIMULATION, PAPER_TRADING, etc.).
        generated_at: ISO timestamp (None = auto).

    Returns:
        SystemState — composición pura, sin efectos secundarios.

    Contrato:
      compose(a, generated_at=T) == compose(a, generated_at=T)  # determinista
      compose no toca disco, red, ni random
      compose no evalúa decisiones de riesgo
    """
    if generated_at is None:
        generated_at = datetime.now(timezone.utc).isoformat()

    return SystemState(
        risk=RiskState(
            status=output_state.risk.status,
            drawdown_pct=output_state.risk.drawdown_pct,
            daily_pnl=output_state.risk.daily_pnl,
            current_balance=output_state.risk.current_balance,
            starting_balance=output_state.risk.starting_balance,
            circuit_breaker=output_state.risk.circuit_breaker,
            p_failure_rolling=output_state.risk.p_failure_rolling,
        ),
        positions=dict(output_state.positions),
        signals=dict(output_state.signals),
        mode=mode,
        version="1.0",
        generated_at=generated_at,
    )
