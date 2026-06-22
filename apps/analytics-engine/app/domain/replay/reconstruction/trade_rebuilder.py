"""TradeRebuilder — reconstruye trades desde el ledger.

Consume TradeDTOs (o dicts compatibles) del repositorio y reconstruye
la secuencia ordenada de trades, validando consistencia de PnL.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReplayTrade:
    """Internal representation of a replayed trade.

    Matches the fields from TradeDTO but lives in the domain layer
    to avoid infrastructure coupling.
    """
    symbol: str = ""
    entry_ts: str = ""
    exit_ts: str = ""
    side: str = ""
    entry_price: float = 0.0
    exit_price: float = 0.0
    size: float = 0.0
    gross_pnl: float = 0.0
    costs: float = 0.0
    net_pnl: float = 0.0
    duration_bars: int = 0
    exit_reason: str = ""
    experiment_id: str = ""


def _dto_to_replay(dto: Any) -> ReplayTrade:
    """Convert a TradeDTO-like object to ReplayTrade."""
    if isinstance(dto, dict):
        return ReplayTrade(**{k: v for k, v in dto.items()
                            if k in ReplayTrade.__dataclass_fields__})
    return ReplayTrade(
        symbol=getattr(dto, "symbol", ""),
        entry_ts=getattr(dto, "entry_ts", ""),
        exit_ts=getattr(dto, "exit_ts", ""),
        side=getattr(dto, "side", ""),
        entry_price=float(getattr(dto, "entry_price", 0.0)),
        exit_price=float(getattr(dto, "exit_price", 0.0)),
        size=float(getattr(dto, "size", 0.0)),
        gross_pnl=float(getattr(dto, "gross_pnl", 0.0)),
        costs=float(getattr(dto, "costs", 0.0)),
        net_pnl=float(getattr(dto, "net_pnl", 0.0)),
        duration_bars=int(getattr(dto, "duration_bars", 0)),
        exit_reason=getattr(dto, "exit_reason", ""),
        experiment_id=getattr(dto, "experiment_id", ""),
    )


class TradeRebuilder:
    """Rebuilds trade sequence from persisted trade data.

    Deterministic: same input → same trade sequence out.
    """

    def __init__(self) -> None:
        self._trades: list[ReplayTrade] = []

    def rebuild(self, trade_dtos: list[Any]) -> list[ReplayTrade]:
        """Rebuild sorted trade list from DTOs or dicts.

        Sorts by entry_ts, exit_ts to ensure deterministic order.
        """
        converted = [_dto_to_replay(t) for t in trade_dtos]
        sorted_trades = sorted(
            converted,
            key=lambda t: (t.entry_ts, t.exit_ts, t.side),
        )
        self._trades = sorted_trades
        return self._trades

    @property
    def trades(self) -> list[ReplayTrade]:
        return list(self._trades)

    def validate_consistency(
        self,
        original_trades: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Compare rebuilt trades against original CSV records."""
        if not self._trades:
            return {"match_rate": 0.0, "n_original": len(original_trades),
                    "n_rebuilt": 0, "mismatches": []}

        mismatches = []
        matched = 0

        original_sorted = sorted(
            original_trades,
            key=lambda t: (t.get("entry_ts", ""), t.get("exit_ts", "")),
        )

        for i, (orig, reb) in enumerate(
            zip(original_sorted, self._trades)
        ):
            fields = ["side", "entry_price", "exit_price", "size",
                      "gross_pnl", "costs", "net_pnl", "duration_bars",
                      "exit_reason"]
            diff = {}
            for f in fields:
                o_val = orig.get(f)
                r_val = getattr(reb, f, None)
                if f in ("gross_pnl", "costs", "net_pnl",
                         "entry_price", "exit_price", "size"):
                    o_val = float(o_val) if o_val is not None else None
                    r_val = float(r_val) if r_val is not None else None
                    if o_val is not None and r_val is not None:
                        if abs(o_val - r_val) > 1e-4:
                            diff[f] = {"original": o_val, "rebuilt": r_val}
                else:
                    if str(o_val) != str(r_val):
                        diff[f] = {"original": o_val, "rebuilt": r_val}

            if diff:
                mismatches.append({
                    "index": i,
                    "entry_ts": orig.get("entry_ts"),
                    "differences": diff,
                })
            else:
                matched += 1

        total = max(len(original_sorted), len(self._trades))
        return {
            "match_rate": matched / total if total > 0 else 0.0,
            "n_original": len(original_sorted),
            "n_rebuilt": len(self._trades),
            "n_matched": matched,
            "n_mismatches": len(mismatches),
            "mismatches": mismatches[:10],
        }
