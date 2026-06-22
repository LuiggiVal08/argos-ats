"""SQLiteExecutionIdempotencyStore — durable dedup for executions.

Schema:
  CREATE TABLE execution_idempotency (
    idempotency_key TEXT PRIMARY KEY,
    exchange_order_id TEXT NOT NULL DEFAULT '',
    event_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
  );

Invariant:
  - INSERT OR IGNORE on idempotency_key
  - check_and_record returns True if newly inserted, False if duplicate
  - WAL + synchronous FULL for crash safety
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from ...application.ports.execution_idempotency import (
    ExecutionIdempotencyError,
    ExecutionIdempotencyStore,
)


class SQLiteExecutionIdempotencyStore:
    """SQLite-backed idempotency store.

    Thread-safe via check_same_thread=False + lock.

    Args:
        db_path: Path to the SQLite file. Default: data/idempotency.db
    """

    def __init__(self, db_path: str = "data/idempotency.db") -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS execution_idempotency (
                idempotency_key TEXT PRIMARY KEY,
                exchange_order_id TEXT NOT NULL DEFAULT '',
                event_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    async def check_and_record(
        self,
        idempotency_key: str,
        exchange_order_id: str = "",
        event_id: str = "",
    ) -> bool:
        """Atomically check-and-set idempotency key.

        Returns:
            True if key was inserted (first time), False if duplicate.
        """
        if not idempotency_key:
            raise ExecutionIdempotencyError("idempotency_key cannot be empty")
        try:
            with self._lock:
                cursor = self._conn.execute(
                    """INSERT OR IGNORE INTO execution_idempotency
                    (idempotency_key, exchange_order_id, event_id, created_at)
                    VALUES (?, ?, ?, ?)""",
                    (
                        idempotency_key,
                        exchange_order_id,
                        event_id,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                self._conn.commit()
                return cursor.rowcount > 0
        except sqlite3.Error as e:
            raise ExecutionIdempotencyError(
                f"idempotency check failed for key={idempotency_key}: {e}"
            ) from e

    async def exists(self, idempotency_key: str) -> bool:
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT 1 FROM execution_idempotency WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
            return row is not None
        except sqlite3.Error as e:
            raise ExecutionIdempotencyError(
                f"idempotency exists check failed: {e}"
            ) from e

    async def update_exchange_order_id(
        self,
        idempotency_key: str,
        exchange_order_id: str,
    ) -> None:
        if not idempotency_key:
            raise ExecutionIdempotencyError("idempotency_key cannot be empty")
        if not exchange_order_id:
            raise ExecutionIdempotencyError("exchange_order_id cannot be empty")
        try:
            with self._lock:
                self._conn.execute(
                    """UPDATE execution_idempotency
                    SET exchange_order_id = ?
                    WHERE idempotency_key = ?""",
                    (exchange_order_id, idempotency_key),
                )
                self._conn.commit()
        except sqlite3.Error as e:
            raise ExecutionIdempotencyError(
                f"idempotency update failed for key={idempotency_key}: {e}"
            ) from e

    async def clear(self) -> None:
        try:
            with self._lock:
                self._conn.execute("DELETE FROM execution_idempotency")
                self._conn.commit()
        except sqlite3.Error as e:
            raise ExecutionIdempotencyError(
                f"idempotency clear failed: {e}"
            ) from e

    async def cleanup_old_entries(self, retention_hours: int = 24) -> int:
        try:
            with self._lock:
                cursor = self._conn.execute(
                    """DELETE FROM execution_idempotency
                    WHERE created_at < datetime('now', ?)""",
                    (f"-{retention_hours} hours",),
                )
                self._conn.commit()
                deleted = cursor.rowcount
                if deleted > 0:
                    self._conn.execute("PRAGMA optimize")
                return deleted
        except sqlite3.Error as e:
            raise ExecutionIdempotencyError(
                f"idempotency cleanup failed: {e}"
            ) from e
