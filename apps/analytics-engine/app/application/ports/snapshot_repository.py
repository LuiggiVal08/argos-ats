"""SnapshotRepository port — full system state snapshot for recovery.

Version 1.0.0:
  - timestamp, equity, drawdown, open_positions, mode, risk_state, active_symbols
  - event_id (UUID v7) for event sourcing linkage
  - version + schema_version for forward compatibility

Snapshots capture the complete operational state of the engine so it
can be reconstructed after restart without replaying the entire journal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable


SNAPSHOT_SCHEMA_VERSION = "1.0.0"
SNAPSHOT_VERSION = 1


class SnapshotRepositoryError(RuntimeError):
    """Raised when snapshot I/O fails."""


@dataclass(frozen=True)
class SystemSnapshot:
    timestamp: datetime
    equity: Decimal
    drawdown: Decimal
    open_positions: list[dict]
    mode: str
    risk_state: str
    active_symbols: list[str]
    event_id: str = ""
    version: int = SNAPSHOT_VERSION
    schema_version: str = SNAPSHOT_SCHEMA_VERSION


# Recovery state tracking — persisted in same DB as snapshots
class RecoveryState:
    STARTED = "STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class RecoveryStateRecord:
    state: str
    started_at: datetime
    completed_at: datetime | None = None
    error: str = ""


@runtime_checkable
class SnapshotRepository(Protocol):
    async def save_snapshot(self, snapshot: SystemSnapshot) -> None:
        """Persist a system snapshot. Overwrites previous snapshot."""
        ...

    async def load_latest(self) -> SystemSnapshot | None:
        """Load the most recent snapshot, or None if none exists."""
        ...

    async def clear(self) -> None:
        """Remove all snapshots."""
        ...

    # Recovery state (Fix 1: recovery atómico)
    async def save_recovery_state(self, state: str, error: str = "") -> None:
        """Persist recovery state: STARTED → IN_PROGRESS → COMPLETED | FAILED."""
        ...

    async def load_recovery_state(self) -> RecoveryStateRecord | None:
        """Load the most recent recovery state record."""
        ...

    async def clear_recovery_state(self) -> None:
        """Clear recovery state after successful startup."""
        ...
