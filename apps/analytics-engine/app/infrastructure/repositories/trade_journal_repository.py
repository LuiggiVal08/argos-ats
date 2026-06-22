"""SQLiteTradeJournal — append-only trade event log.

Schema:
  CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_price REAL,
    exit_price REAL,
    pnl REAL NOT NULL,
    fees REAL NOT NULL DEFAULT 0,
    reason TEXT,
    model_version TEXT
  );

Invariants:
  - Append-only: never UPDATE, never DELETE.
  - Immutable by design: once written, data is read-only.
  - The journal can be replayed for recovery or audit.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from ...application.ports.trade_journal import (
    TradeJournal,
    TradeJournalError,
    TradeRecord,
)


class SQLiteTradeJournal:
    """SQLite-backed append-only trade journal.

    Thread-safe via check_same_thread=False + lock.
    Every INSERT commits synchronously.

    Args:
        db_path: Path to the SQLite file. Default: data/journal.db
    """

    def __init__(self, db_path: str = "data/journal.db") -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price REAL,
                exit_price REAL,
                pnl REAL NOT NULL,
                fees REAL NOT NULL DEFAULT 0,
                reason TEXT,
                model_version TEXT,
                event_id TEXT NOT NULL DEFAULT ''
            )
        """)
        # Add event_id column if upgrading from old schema (idempotent)
        try:
            self._conn.execute("ALTER TABLE trades ADD COLUMN event_id TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # column already exists
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_timestamp
            ON trades(timestamp)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_event_id
            ON trades(event_id)
        """)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ── Append-only operations ────────────────────────────────────────────

    async def add(self, record: TradeRecord) -> None:
        if self._conn is None:
            raise TradeJournalError("journal closed")
        try:
            with self._lock:
                self._conn.execute(
                    """INSERT INTO trades
                    (trade_id, timestamp, symbol, side, entry_price, exit_price,
                     pnl, fees, reason, model_version, event_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        record.reference,
                        record.closed_at.isoformat(),
                        record.symbol,
                        "",   # side — not in TradeRecord yet
                        None,  # entry_price — not in TradeRecord
                        None,  # exit_price — not in TradeRecord
                        float(record.realized_pnl),
                        0.0,
                        "",
                        "",
                        record.event_id,
                    ),
                )
                self._conn.commit()
        except sqlite3.Error as e:
            raise TradeJournalError(f"journal append failed: {e}") from e

    async def add_detailed(
        self,
        trade_id: str,
        timestamp: datetime,
        symbol: str,
        side: str,
        entry_price: Decimal | None,
        exit_price: Decimal | None,
        pnl: Decimal,
        fees: Decimal = Decimal("0"),
        reason: str = "",
        model_version: str = "",
        event_id: str = "",
    ) -> None:
        """Append a detailed trade record with full metadata."""
        if self._conn is None:
            raise TradeJournalError("journal closed")
        try:
            with self._lock:
                self._conn.execute(
                    """INSERT INTO trades
                    (trade_id, timestamp, symbol, side, entry_price, exit_price,
                     pnl, fees, reason, model_version, event_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        trade_id,
                        timestamp.isoformat(),
                        symbol,
                        side,
                        float(entry_price) if entry_price else None,
                        float(exit_price) if exit_price else None,
                        float(pnl),
                        float(fees),
                        reason,
                        model_version,
                        event_id,
                    ),
                )
                self._conn.commit()
        except sqlite3.Error as e:
            raise TradeJournalError(f"journal append failed: {e}") from e

    async def realized_pnl_since(self, since_utc: datetime) -> Decimal:
        if self._conn is None:
            raise TradeJournalError("journal closed")
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT COALESCE(SUM(pnl), 0) AS total FROM trades "
                    "WHERE timestamp >= ?",
                    (since_utc.isoformat(),),
                ).fetchone()
            return Decimal(str(row["total"]))
        except sqlite3.Error as e:
            raise TradeJournalError(f"journal read failed: {e}") from e

    # ── Query helpers ─────────────────────────────────────────────────────

    async def list_recent(self, limit: int = 100) -> list[dict]:
        """Return recent trades for dashboard/audit."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    async def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS cnt FROM trades").fetchone()
        return row["cnt"]

    async def total_pnl(self) -> Decimal:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(pnl), 0) AS total FROM trades"
            ).fetchone()
        return Decimal(str(row["total"]))
