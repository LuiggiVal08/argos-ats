"""Reconciliation Drift Report — aggregates drift events.

Source: existing CRITICAL / DRIFT log events only.
No logic change — pure log aggregation.

Metrics:
- drift_count_last_1h
- drift_count_last_24h
- max_drift_pct
- drift_per_position_ratio
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .schema import emit_metric


@dataclass
class DriftEvent:
    timestamp: float
    event_type: str  # duplicate_symbol, missing_on_exchange, missing_local, partial_mismatch
    symbol: str
    detail: str | None = None
    local_units: str = ""
    exchange_units: str = ""


@dataclass
class ReconciliationDriftTracker:
    """Tracks reconciliation drift events.
    
    Pure aggregation — never influences reconciliation logic.
    """
    _events: list[DriftEvent] = field(default_factory=list)
    _window_start: float = 0.0

    def __post_init__(self) -> None:
        if self._window_start == 0.0:
            object.__setattr__(self, "_window_start", datetime.now(timezone.utc).timestamp())

    def record_drift(
        self,
        event_type: str,
        symbol: str,
        detail: str | None = None,
        local_units: str = "",
        exchange_units: str = "",
    ) -> None:
        now = datetime.now(timezone.utc).timestamp()
        event = DriftEvent(
            timestamp=now,
            event_type=event_type,
            symbol=symbol,
            detail=detail,
            local_units=local_units,
            exchange_units=exchange_units,
        )
        self._events.append(event)

        level = "CRITICAL" if event_type in ("duplicate_symbol",) else "WARNING"
        emit_metric(
            f"reconciliation.drift.{event_type}",
            value=symbol,
            threshold="0 drift events",
            level=level,
            symbol=symbol,
            detail=detail or "",
            local_units=local_units,
            exchange_units=exchange_units,
            total_drift=len(self._events),
        )

    def _events_since(self, from_ts: float) -> list[DriftEvent]:
        return [e for e in self._events if e.timestamp >= from_ts]

    @property
    def drift_count_last_1h(self) -> int:
        cutoff = datetime.now(timezone.utc).timestamp() - 3600
        return len(self._events_since(cutoff))

    @property
    def drift_count_last_24h(self) -> int:
        cutoff = datetime.now(timezone.utc).timestamp() - 86400
        return len(self._events_since(cutoff))

    @property
    def max_drift_pct(self) -> float:
        if not self._events:
            return 0.0
        ratios: list[float] = []
        for e in self._events:
            if e.local_units and e.exchange_units:
                try:
                    local = float(e.local_units)
                    exchange = float(e.exchange_units)
                    if local > 0 and exchange > 0:
                        ratios.append(abs(local - exchange) / max(local, exchange))
                except (ValueError, ZeroDivisionError):
                    pass
        return max(ratios) if ratios else 0.0

    @property
    def total_drift(self) -> int:
        return len(self._events)

    def snapshot(self) -> dict:
        return {
            "drift_count_last_1h": self.drift_count_last_1h,
            "drift_count_last_24h": self.drift_count_last_24h,
            "max_drift_pct": round(self.max_drift_pct, 4),
            "total_drift": self.total_drift,
        }

    def emit_snapshot(self) -> None:
        data = self.snapshot()
        level = "CRITICAL" if data["total_drift"] > 0 else "INFO"
        emit_metric(
            "reconciliation.drift.snapshot",
            value=data,
            threshold="0 drift events",
            level=level,
            **data,
        )


# Default singleton — import directly for zero-constructor-change instrumentation
default_drift_tracker = ReconciliationDriftTracker()
