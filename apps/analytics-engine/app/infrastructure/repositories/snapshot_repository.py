"""SQLiteSnapshotRepository — full system state snapshots for recovery.

Schema (v1):
  system_snapshots — append-only snapshot log
  recovery_state  — single-row recovery tracking

Features:
  - Versioned snapshots (Fix 2: backward compat)
  - Recovery state table (Fix 1: atomic recovery)
  - event_id in each snapshot (Fix 4: event sourcing)
  - Periodic snapshots via background task
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from datetime import datetime, timezone
from decimal import Decimal

import structlog
from pathlib import Path
from typing import Any

from ...application.ports.snapshot_repository import (
    SNAPSHOT_SCHEMA_VERSION,
    SNAPSHOT_VERSION,
    RecoveryState,
    RecoveryStateRecord,
    SnapshotRepositoryError,
    SystemSnapshot,
)
from ...domain.value_objects.event_id import uuid7

log = structlog.get_logger()


class SQLiteSnapshotRepository:
    """SQLite-backed system snapshot store with recovery state tracking.

    Args:
        db_path: Path to the SQLite file. Default: data/snapshots.db
        periodic_interval_sec: Seconds between periodic snapshots (default 300).
    """

    def __init__(
        self, db_path: str = "data/snapshots.db", periodic_interval_sec: int = 300
    ) -> None:
        self._db_path = db_path
        self._periodic_interval = periodic_interval_sec
        self._lock = threading.Lock()
        self._periodic_task: asyncio.Task | None = None
        self._closed = False
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS system_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                equity REAL NOT NULL,
                drawdown REAL NOT NULL,
                open_positions TEXT NOT NULL,
                mode TEXT NOT NULL,
                risk_state TEXT NOT NULL,
                active_symbols TEXT NOT NULL,
                snapshot_type TEXT NOT NULL DEFAULT 'periodic',
                event_id TEXT NOT NULL DEFAULT '',
                version INTEGER NOT NULL DEFAULT 1,
                schema_version TEXT NOT NULL DEFAULT '1.0.0'
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp
            ON system_snapshots(timestamp DESC)
        """)
        # Fix 1: recovery_state table (single row, updated in place)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS recovery_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                state TEXT NOT NULL DEFAULT 'STARTED',
                started_at TEXT NOT NULL,
                completed_at TEXT,
                error TEXT NOT NULL DEFAULT ''
            )
        """)
        self._conn.commit()

    def close(self) -> None:
        self._closed = True
        self._stop_periodic()
        self._conn.close()

    # ── Snapshot operations ───────────────────────────────────────────────

    async def save_snapshot(self, snapshot: SystemSnapshot) -> None:
        if self._closed:
            raise SnapshotRepositoryError("snapshot store closed")
        event_id = snapshot.event_id or str(uuid7())
        try:
            with self._lock:
                self._conn.execute(
                    """INSERT INTO system_snapshots
                    (timestamp, equity, drawdown, open_positions, mode,
                     risk_state, active_symbols, snapshot_type,
                     event_id, version, schema_version)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        snapshot.timestamp.isoformat(),
                        float(snapshot.equity),
                        float(snapshot.drawdown),
                        json.dumps(snapshot.open_positions),
                        snapshot.mode,
                        snapshot.risk_state,
                        json.dumps(snapshot.active_symbols),
                        "manual",
                        event_id,
                        snapshot.version,
                        snapshot.schema_version,
                    ),
                )
                self._conn.commit()
        except sqlite3.Error as e:
            raise SnapshotRepositoryError(f"snapshot save failed: {e}") from e

    async def save_snapshot_typed(
        self,
        snapshot: SystemSnapshot,
        snapshot_type: str = "periodic",
    ) -> None:
        """Save with explicit type tag and auto-generated event_id."""
        if self._closed:
            raise SnapshotRepositoryError("snapshot store closed")
        event_id = str(uuid7())
        try:
            with self._lock:
                self._conn.execute(
                    """INSERT INTO system_snapshots
                    (timestamp, equity, drawdown, open_positions, mode,
                     risk_state, active_symbols, snapshot_type,
                     event_id, version, schema_version)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        snapshot.timestamp.isoformat(),
                        float(snapshot.equity),
                        float(snapshot.drawdown),
                        json.dumps(snapshot.open_positions),
                        snapshot.mode,
                        snapshot.risk_state,
                        json.dumps(snapshot.active_symbols),
                        snapshot_type,
                        event_id,
                        snapshot.version,
                        snapshot.schema_version,
                    ),
                )
                self._conn.commit()
        except sqlite3.Error as e:
            raise SnapshotRepositoryError(f"snapshot save failed: {e}") from e

    async def load_latest(self) -> SystemSnapshot | None:
        if self._closed:
            raise SnapshotRepositoryError("snapshot store closed")
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT * FROM system_snapshots ORDER BY id DESC LIMIT 1"
                ).fetchone()
            if row is None:
                return None
            return self._row_to_snapshot(row)
        except sqlite3.Error as e:
            raise SnapshotRepositoryError(f"snapshot load failed: {e}") from e

    async def clear(self) -> None:
        if self._closed:
            raise SnapshotRepositoryError("snapshot store closed")
        try:
            with self._lock:
                self._conn.execute("DELETE FROM system_snapshots")
                self._conn.commit()
        except sqlite3.Error as e:
            raise SnapshotRepositoryError(f"snapshot clear failed: {e}") from e

    # ── Recovery state (Fix 1) ───────────────────────────────────────────

    async def save_recovery_state(self, state: str, error: str = "") -> None:
        if self._closed:
            raise SnapshotRepositoryError("snapshot store closed")
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._lock:
                existing = self._conn.execute(
                    "SELECT id FROM recovery_state WHERE id = 1"
                ).fetchone()
                if existing:
                    self._conn.execute(
                        """UPDATE recovery_state
                        SET state = ?, completed_at = ?, error = ?
                        WHERE id = 1""",
                        (state, now if state in (RecoveryState.COMPLETED, RecoveryState.FAILED) else None, error),
                    )
                else:
                    self._conn.execute(
                        """INSERT INTO recovery_state (id, state, started_at, completed_at, error)
                        VALUES (1, ?, ?, ?, ?)""",
                        (state, now,
                         now if state in (RecoveryState.COMPLETED, RecoveryState.FAILED) else None,
                         error),
                    )
                self._conn.commit()
        except sqlite3.Error as e:
            raise SnapshotRepositoryError(f"recovery state save failed: {e}") from e

    async def load_recovery_state(self) -> RecoveryStateRecord | None:
        if self._closed:
            raise SnapshotRepositoryError("snapshot store closed")
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT * FROM recovery_state WHERE id = 1"
                ).fetchone()
            if row is None:
                return None
            return RecoveryStateRecord(
                state=row["state"],
                started_at=datetime.fromisoformat(row["started_at"]),
                completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
                error=row["error"] if row["error"] else "",
            )
        except sqlite3.Error as e:
            raise SnapshotRepositoryError(f"recovery state load failed: {e}") from e

    async def clear_recovery_state(self) -> None:
        if self._closed:
            raise SnapshotRepositoryError("snapshot store closed")
        try:
            with self._lock:
                self._conn.execute("DELETE FROM recovery_state WHERE id = 1")
                self._conn.commit()
        except sqlite3.Error as e:
            raise SnapshotRepositoryError(f"recovery state clear failed: {e}") from e

    # ── Periodic snapshots ───────────────────────────────────────────────

    def start_periodic(
        self,
        equity_provider: Any,
        mode: str,
        risk_state: str,
        active_symbols: list[str],
    ) -> None:
        """Start the periodic snapshot background task."""

        async def _loop() -> None:
            while not self._closed:
                try:
                    await asyncio.sleep(self._periodic_interval)
                    equity = Decimal("0")
                    try:
                        equity = await equity_provider.get_free_balance("USDT")
                    except Exception as e:
                        log.warning("periodic_equity_fetch_failed", error=str(e))
                    snapshot = SystemSnapshot(
                        timestamp=datetime.now(timezone.utc),
                        equity=equity,
                        drawdown=Decimal("0"),
                        open_positions=[],
                        mode=mode,
                        risk_state=risk_state,
                        active_symbols=active_symbols,
                    )
                    await self.save_snapshot_typed(snapshot, "periodic")
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    log.warning("periodic_snapshot_loop_failed", error=str(e))

        self._periodic_task = asyncio.create_task(_loop())

    def _stop_periodic(self) -> None:
        if self._periodic_task is not None:
            self._periodic_task.cancel()
            self._periodic_task = None

    # ── Internal ──────────────────────────────────────────────────────────

    def _row_to_snapshot(self, row: sqlite3.Row) -> SystemSnapshot:
        open_positions: list[dict] = []
        try:
            open_positions = json.loads(row["open_positions"]) if row["open_positions"] else []
        except (json.JSONDecodeError, TypeError):
            open_positions = []

        active_symbols: list[str] = []
        try:
            active_symbols = json.loads(row["active_symbols"]) if row["active_symbols"] else []
        except (json.JSONDecodeError, TypeError):
            active_symbols = []

        return SystemSnapshot(
            timestamp=datetime.fromisoformat(row["timestamp"]),
            equity=Decimal(str(row["equity"])),
            drawdown=Decimal(str(row["drawdown"])),
            open_positions=open_positions,
            mode=row["mode"],
            risk_state=row["risk_state"],
            active_symbols=active_symbols,
            event_id=row["event_id"] if row["event_id"] else "",
            version=int(row["version"]) if row["version"] else 1,
            schema_version=str(row["schema_version"]) if row["schema_version"] else "1.0.0",
        )
