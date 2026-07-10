"""SQLitePositionRepository — persistence layer for LivePosition.

Schema:
  CREATE TABLE positions (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_price REAL NOT NULL,
    quantity REAL NOT NULL,
    current_price REAL NOT NULL DEFAULT 0,
    sl_price REAL,
    tp_price REAL,
    tp2_price REAL,
    tp3_price REAL,
    trail_activated INTEGER NOT NULL DEFAULT 0,
    trail_offset REAL,
    break_even_activated INTEGER NOT NULL DEFAULT 0,
    atr_at_entry REAL,
    risk_multiple REAL NOT NULL DEFAULT 0,
    initial_units REAL,
    tp1_pct REAL NOT NULL DEFAULT 0.5,
    tp2_pct REAL NOT NULL DEFAULT 0.25,
    status TEXT NOT NULL DEFAULT 'OPEN',
    unrealized_pnl REAL NOT NULL DEFAULT 0,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    realized_pnl REAL,
    metadata TEXT NOT NULL DEFAULT '{}'
  );

Every state change is persisted immediately (synchronous commit).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from ...domain.value_objects.live_position import LivePosition
from ...domain.value_objects.order import OrderSide
from ...application.ports.position_repository import PositionRepository


class SQLitePositionRepository:
    """SQLite-backed position store.

    Thread-safe via check_same_thread=False + lock. Each write
    commits synchronously so no state change is lost on crash.

    Args:
        db_path: Path to the SQLite file. Default: data/positions.db
    """

    def __init__(self, db_path: str = "data/positions.db") -> None:
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
            CREATE TABLE IF NOT EXISTS positions (
                id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                quantity REAL NOT NULL,
                current_price REAL NOT NULL DEFAULT 0,
                sl_price REAL,
                tp_price REAL,
                tp2_price REAL,
                tp3_price REAL,
                trail_activated INTEGER NOT NULL DEFAULT 0,
                trail_offset REAL,
                break_even_activated INTEGER NOT NULL DEFAULT 0,
                atr_at_entry REAL,
                risk_multiple REAL NOT NULL DEFAULT 0,
                initial_units REAL,
                tp1_pct REAL NOT NULL DEFAULT 0.5,
                tp2_pct REAL NOT NULL DEFAULT 0.25,
                status TEXT NOT NULL DEFAULT 'OPEN',
                unrealized_pnl REAL NOT NULL DEFAULT 0,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                realized_pnl REAL,
                metadata TEXT NOT NULL DEFAULT '{}'
            )
        """)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ── CRUD ──────────────────────────────────────────────────────────────

    async def save(self, position: LivePosition) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO positions (
                    id, symbol, side, entry_price, quantity, current_price,
                    sl_price, tp_price, tp2_price, tp3_price,
                    trail_activated, trail_offset, break_even_activated,
                    atr_at_entry, risk_multiple, initial_units,
                    tp1_pct, tp2_pct, status, unrealized_pnl,
                    opened_at, closed_at, realized_pnl, metadata
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    position.position_id,
                    position.symbol,
                    position.side.value,
                    float(position.entry_price),
                    float(position.units),
                    float(position.current_price),
                    float(position.sl_price) if position.sl_price else None,
                    float(position.tp_price) if position.tp_price else None,
                    float(position.tp2_price) if position.tp2_price else None,
                    float(position.tp3_price) if position.tp3_price else None,
                    1 if position.trail_activated else 0,
                    float(position.trail_offset) if position.trail_offset else None,
                    1 if position.break_even_activated else 0,
                    float(position.atr_at_entry) if position.atr_at_entry else None,
                    float(position.risk_multiple),
                    float(position.initial_units) if position.initial_units else None,
                    float(position.tp1_pct),
                    float(position.tp2_pct),
                    position.status,
                    float(position.unrealized_pnl),
                    position.opened_at.isoformat(),
                    position.closed_at.isoformat() if position.closed_at else None,
                    float(position.realized_pnl) if position.realized_pnl else None,
                    json.dumps(position.metadata),
                ),
            )
            self._conn.commit()

    async def load(self, position_id: str) -> LivePosition | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM positions WHERE id = ?", (position_id,)
            ).fetchone()
        return self._row_to_position(row) if row else None

    async def list_open(self) -> list[LivePosition]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM positions WHERE status IN ('OPEN', 'PARTIALLY_CLOSED')"
            ).fetchall()
        return [self._row_to_position(r) for r in rows]

    async def list_all(self) -> list[LivePosition]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM positions ORDER BY opened_at DESC").fetchall()
        return [self._row_to_position(r) for r in rows]

    async def delete(self, position_id: str) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM positions WHERE id = ?", (position_id,)
            )
            self._conn.commit()
        return cursor.rowcount > 0

    # ── Reconciliation helper ─────────────────────────────────────────────

    async def get_open_positions_snapshot(self) -> list[dict[str, Any]]:
        """Return open positions as plain dicts for snapshot/reconciliation."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, symbol, side, entry_price AS entryPrice, "
                "quantity, current_price AS currentPrice, status "
                "FROM positions WHERE status IN ('OPEN', 'PARTIALLY_CLOSED')"
            ).fetchall()
        return [dict(r) for r in rows]

    async def reconcile_position_with_exchange(
        self, position_id: str, exchange_data: dict[str, Any]
    ) -> LivePosition | None:
        """Reconcile a local position with data from the exchange.

        Exchange data ALWAYS wins on quantity and price. Updates the
        local position and returns the reconciled version, or None if
        the position no longer exists locally.
        """
        local = await self.load(position_id)
        if local is None:
            return None

        ex_qty = Decimal(str(exchange_data.get("quantity", 0)))
        ex_price = Decimal(str(exchange_data.get("entryPrice", 0)))
        ex_side = exchange_data.get("side", "").upper()

        reconciled = LivePosition(
            position_id=local.position_id,
            symbol=local.symbol,
            side=OrderSide.BUY if ex_side == "LONG" else OrderSide.SELL,
            units=ex_qty if ex_qty > 0 else local.units,
            entry_price=ex_price if ex_price > 0 else local.entry_price,
            current_price=Decimal(str(exchange_data.get("currentPrice", local.current_price))),
            sl_price=local.sl_price,
            tp_price=local.tp_price,
            tp2_price=local.tp2_price,
            tp3_price=local.tp3_price,
            trail_activated=local.trail_activated,
            trail_offset=local.trail_offset,
            break_even_activated=local.break_even_activated,
            atr_at_entry=local.atr_at_entry,
            risk_multiple=local.risk_multiple,
            initial_units=local.initial_units,
            tp1_pct=local.tp1_pct,
            tp2_pct=local.tp2_pct,
            opened_at=local.opened_at,
            status="OPEN" if ex_qty > 0 else "CLOSED",
        )
        await self.save(reconciled)
        return reconciled

    # ── Internal ──────────────────────────────────────────────────────────

    def _row_to_position(self, row: sqlite3.Row) -> LivePosition:
        def d(val: Any) -> Decimal | None:
            return Decimal(str(val)) if val is not None else None

        metadata: dict = {}
        try:
            metadata = json.loads(row["metadata"]) if row["metadata"] else {}
        except (json.JSONDecodeError, TypeError):
            metadata = {}

        return LivePosition(
            position_id=row["id"],
            symbol=row["symbol"],
            side=OrderSide(row["side"]),
            units=d(row["quantity"]),
            entry_price=d(row["entry_price"]),
            current_price=d(row["current_price"]),
            sl_price=d(row["sl_price"]),
            tp_price=d(row["tp_price"]),
            tp2_price=d(row["tp2_price"]),
            tp3_price=d(row["tp3_price"]),
            trail_activated=bool(row["trail_activated"]),
            trail_offset=d(row["trail_offset"]),
            break_even_activated=bool(row["break_even_activated"]),
            atr_at_entry=d(row["atr_at_entry"]),
            risk_multiple=d(row["risk_multiple"]),
            initial_units=d(row["initial_units"]),
            tp1_pct=d(row["tp1_pct"]),
            tp2_pct=d(row["tp2_pct"]),
            status=row["status"],
            unrealized_pnl=d(row["unrealized_pnl"]),
            opened_at=datetime.fromisoformat(row["opened_at"]),
            closed_at=datetime.fromisoformat(row["closed_at"]) if row["closed_at"] else None,
            realized_pnl=d(row["realized_pnl"]),
            metadata=metadata,
        )
