"""ReplayState — estado reconstruido del sistema durante el replay.

Mantiene el estado actual del engine durante la reconstrucción,
incluyendo posición actual, trades vistos, episodes abiertos/settled,
y el último tensor/control conocido.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReplayPosition:
    """Estado de la posición actual durante el replay."""
    in_position: bool = False
    side: int = 0          # 1=LONG, -1=SHORT, 0=NONE
    entry_bar: int = 0
    entry_price: float = 0.0
    position_size: float = 0.0
    entry_cost: float = 0.0


@dataclass
class ReplayState:
    """Estado completo reconstruido del forward test.

    Se actualiza incrementalmente a medida que se reproducen eventos.
    """
    symbol: str = ""
    experiment_id: str = ""

    # Progresión
    current_bar: int = 0
    last_timestamp: str = ""
    total_bars: int = 0

    # Posición
    position: ReplayPosition = field(default_factory=ReplayPosition)

    # Balances
    free_balance: float = 0.0
    peak_equity: float = 0.0

    # Contadores
    total_trades: int = 0
    total_costs: float = 0.0

    # Últimos eventos
    last_trade: dict[str, Any] | None = None
    last_episode: dict[str, Any] | None = None
    last_tensor: dict[str, Any] | None = None
    last_control: dict[str, Any] | None = None

    # Modo replay
    mode: str = "full"

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "experiment_id": self.experiment_id,
            "current_bar": self.current_bar,
            "last_timestamp": self.last_timestamp,
            "total_bars": self.total_bars,
            "in_position": self.position.in_position,
            "position_side": self.position.side,
            "free_balance": round(self.free_balance, 2),
            "peak_equity": round(self.peak_equity, 2),
            "total_trades": self.total_trades,
            "total_costs": round(self.total_costs, 4),
            "has_last_trade": self.last_trade is not None,
            "has_last_tensor": self.last_tensor is not None,
            "has_last_control": self.last_control is not None,
        }
