"""SQLiteEventStore — deterministic append-only event log.

Schema:
  CREATE TABLE events (
    event_index INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    version INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    timestamp_bucket TEXT NOT NULL,
    data TEXT NOT NULL  -- JSON-encoded
  );

Invariants:
  - Append-only: never UPDATE, never DELETE.
  - Idempotent: INSERT OR IGNORE on event_id.
  - Deterministic: event_id is a SHA-256 hash of (event_type, source,
    version, timestamp_bucket, canonical(data)).
  - WAL + synchronous FULL for crash safety.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, AsyncIterator

from ...application.ports.event_store import EventStore, EventStoreError
from ...domain.value_objects.domain_event import DomainEvent


class SQLiteEventStore:
    """SQLite-backed deterministic event log.

    Thread-safe via check_same_thread=False + lock.

    Args:
        db_path: Path to the SQLite file. Default: data/events.db
    """

    def __init__(self, db_path: str = "data/events.db") -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=DELETE")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                event_index INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                event_type TEXT NOT NULL,
                source TEXT NOT NULL,
                version INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                timestamp_bucket TEXT NOT NULL,
                data TEXT NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_event_type
            ON events(event_type)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_timestamp
            ON events(timestamp)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_timestamp_bucket
            ON events(timestamp_bucket)
        """)
        # Migration: ensure event_index column exists on old schemas
        self._migrate_add_event_index()
        self._conn.commit()

    def _migrate_add_event_index(self) -> None:
        """Add event_index column to existing databases created before F2."""
        try:
            self._conn.execute(
                "ALTER TABLE events ADD COLUMN event_index INTEGER"
            )
        except sqlite3.OperationalError:
            pass  # column already exists — fine

    def close(self) -> None:
        self._conn.close()

    # ── EventStore protocol ──────────────────────────────────────────────

    async def write(self, event: DomainEvent) -> bool:
        """Atomically append an event if it does not exist.

        Returns:
            True if event was appended (first write).
            False if event_id already exists (duplicate).
        """
        try:
            with self._lock:
                cursor = self._conn.execute(
                    """INSERT OR IGNORE INTO events
                    (event_id, event_type, source, version,
                     timestamp, timestamp_bucket, data)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event.event_id,
                        event.event_type,
                        event.source,
                        event.version,
                        event.timestamp,
                        event.timestamp_bucket,
                        json.dumps(event.data),
                    ),
                )
                self._conn.commit()
                return cursor.rowcount > 0
        except sqlite3.Error as e:
            raise EventStoreError(
                f"event write failed for {event.event_id}: {e}"
            ) from e

    async def exists(self, event_id: str) -> bool:
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT 1 FROM events WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
            return row is not None
        except sqlite3.Error as e:
            raise EventStoreError(
                f"event exists check failed for {event_id}: {e}"
            ) from e

    async def read(self, event_id: str) -> DomainEvent | None:
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT event_index, event_id, event_type, source, "
                    "version, timestamp, timestamp_bucket, data "
                    "FROM events WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
            return DomainEvent.from_row(dict(row)) if row else None
        except sqlite3.Error as e:
            raise EventStoreError(
                f"event read failed for {event_id}: {e}"
            ) from e

    async def list_by_type(
        self,
        event_type: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DomainEvent]:
        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT event_index, event_id, event_type, source, "
                    "version, timestamp, timestamp_bucket, data "
                    "FROM events WHERE event_type = ? "
                    "ORDER BY timestamp ASC LIMIT ? OFFSET ?",
                    (event_type, limit, offset),
                ).fetchall()
            return [DomainEvent.from_row(dict(r)) for r in rows]
        except sqlite3.Error as e:
            raise EventStoreError(
                f"event list_by_type failed for {event_type}: {e}"
            ) from e

    async def list_since(
        self,
        since_timestamp: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DomainEvent]:
        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT event_index, event_id, event_type, source, "
                    "version, timestamp, timestamp_bucket, data "
                    "FROM events WHERE timestamp >= ? "
                    "ORDER BY timestamp ASC LIMIT ? OFFSET ?",
                    (since_timestamp, limit, offset),
                ).fetchall()
            return [DomainEvent.from_row(dict(r)) for r in rows]
        except sqlite3.Error as e:
            raise EventStoreError(
                f"event list_since failed: {e}"
            ) from e

    async def count(self) -> int:
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS cnt FROM events"
                ).fetchone()
            return row["cnt"]
        except sqlite3.Error as e:
            raise EventStoreError(f"event count failed: {e}") from e

    async def count_by_type(self, event_type: str) -> int:
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS cnt FROM events WHERE event_type = ?",
                    (event_type,),
                ).fetchone()
            return row["cnt"]
        except sqlite3.Error as e:
            raise EventStoreError(
                f"event count_by_type failed for {event_type}: {e}"
            ) from e

    async def stream_by_index_range(
        self,
        from_index: int,
        to_index: int | None = None,
    ) -> AsyncIterator[DomainEvent]:
        """Stream events by event_index range, ordered ASC.

        Uses yield-based async generator (not yield from) for SQLite
        cursor streaming, yielding one DomainEvent at a time.
        """
        try:
            with self._lock:
                if to_index is not None:
                    rows = self._conn.execute(
                        "SELECT event_index, event_id, event_type, source, "
                        "version, timestamp, timestamp_bucket, data "
                        "FROM events "
                        "WHERE event_index >= ? AND event_index <= ? "
                        "ORDER BY event_index ASC",
                        (from_index, to_index),
                    )
                else:
                    rows = self._conn.execute(
                        "SELECT event_index, event_id, event_type, source, "
                        "version, timestamp, timestamp_bucket, data "
                        "FROM events "
                        "WHERE event_index >= ? "
                        "ORDER BY event_index ASC",
                        (from_index,),
                    )
                for row in rows:
                    yield DomainEvent.from_row(dict(row))
        except sqlite3.Error as e:
            raise EventStoreError(
                f"event stream_by_index_range failed: {e}"
            ) from e

    async def max_event_index(self) -> int:
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT COALESCE(MAX(event_index), 0) AS max_idx "
                    "FROM events"
                ).fetchone()
            return row["max_idx"]
        except sqlite3.Error as e:
            raise EventStoreError(
                f"event max_event_index failed: {e}"
            ) from e

    async def clear(self) -> None:
        try:
            with self._lock:
                self._conn.execute("DELETE FROM events")
                self._conn.commit()
        except sqlite3.Error as e:
            raise EventStoreError(f"event clear failed: {e}") from e
