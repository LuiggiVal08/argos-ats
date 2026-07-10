"""VirtualBalanceProvider: SQLite-backed paper-trading portfolio.

Implements BalanceProvider for PAPER_TRADING mode. Tracks:
  - initial_capital (set once at creation / reset)
  - realized_pnl (cumulative from closed trades)
  - unrealized_pnl (cached, updated by the position monitor loop)
  - peak_equity (for drawdown calculations)
  - trade_count, wins, losses (for winrate/expectancy/profit-factor stats)

Equity = cash_balance + realized_pnl + unrealized_pnl
Cash balance starts at initial_capital and is only reduced by the
position's margin when a trade is entered. For simplicity in this
paper-trading version, cash_balance = initial_capital — we treat
realized_pnl as the running P&L and do not deduct entry costs.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import structlog

from ...application.ports.balance_provider import (
    BalanceProvider,
    BalanceProviderError,
)

log = structlog.get_logger()

INITIAL_CAPITAL = Decimal("10000")
VIRTUAL_PORTFOLIO_DB = Path("data/virtual_portfolio.db")


class VirtualBalanceProvider(BalanceProvider):
    def __init__(
        self,
        initial_capital: Decimal | None = None,
        db_path: Path = VIRTUAL_PORTFOLIO_DB,
    ) -> None:
        self._initial_capital = initial_capital or INITIAL_CAPITAL
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @staticmethod
    def _ensure_journal_delete(conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA journal_mode=DELETE")

    def _init_db(self) -> None:
        with closing(sqlite3.connect(str(self._db_path))) as conn:
            self._ensure_journal_delete(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS virtual_portfolio (
                    id TEXT PRIMARY KEY,
                    initial_capital TEXT NOT NULL,
                    realized_pnl TEXT NOT NULL DEFAULT '0',
                    unrealized_pnl TEXT NOT NULL DEFAULT '0',
                    peak_equity TEXT,
                    trade_count INTEGER NOT NULL DEFAULT 0,
                    wins INTEGER NOT NULL DEFAULT 0,
                    losses INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            conn.commit()

    def _ensure_row(self) -> dict:
        with closing(sqlite3.connect(str(self._db_path))) as conn:
            self._ensure_journal_delete(conn)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM virtual_portfolio LIMIT 1"
            ).fetchone()
            if row is not None:
                return dict(row)
            portfolio_id = uuid4().hex[:12]
            conn.execute(
                """
                INSERT INTO virtual_portfolio
                    (id, initial_capital, realized_pnl, unrealized_pnl,
                     peak_equity, trade_count, wins, losses)
                VALUES (?, ?, '0', '0', NULL, 0, 0, 0)
                """,
                (portfolio_id, str(self._initial_capital)),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM virtual_portfolio LIMIT 1"
            ).fetchone()
            return dict(row)  # type: ignore[arg-type]

    @property
    def _current_equity(self) -> Decimal:
        row = self._ensure_row()
        initial = Decimal(row["initial_capital"])
        realized = Decimal(row["realized_pnl"])
        unrealized = Decimal(row["unrealized_pnl"])
        return initial + realized + unrealized

    async def get_free_balance(self, quote: str = "USDT") -> Decimal:
        try:
            equity = self._current_equity
            self._update_peak(equity)
            return equity
        except Exception as e:
            raise BalanceProviderError(
                f"virtual_balance_error: {e}"
            ) from e

    def _update_peak(self, equity: Decimal) -> None:
        with closing(sqlite3.connect(str(self._db_path))) as conn:
            self._ensure_journal_delete(conn)
            row = conn.execute(
                "SELECT peak_equity FROM virtual_portfolio LIMIT 1"
            ).fetchone()
            if row is None:
                return
            current_peak = (
                Decimal(row[0]) if row[0] is not None else Decimal("0")
            )
            if equity > current_peak:
                conn.execute(
                    "UPDATE virtual_portfolio SET peak_equity = ?",
                    (str(equity),),
                )
                conn.commit()

    async def record_trade(self, realized_pnl: Decimal) -> None:
        with closing(sqlite3.connect(str(self._db_path))) as conn:
            self._ensure_journal_delete(conn)
            row = conn.execute(
                "SELECT realized_pnl, trade_count, wins, losses "
                "FROM virtual_portfolio LIMIT 1"
            ).fetchone()
            if row is None:
                return
            cumulative = Decimal(row[0]) + realized_pnl
            trade_count = row[1] + 1
            wins = row[2] + (1 if realized_pnl > 0 else 0)
            losses = row[3] + (1 if realized_pnl < 0 else 0)
            conn.execute(
                """
                UPDATE virtual_portfolio
                SET realized_pnl = ?, trade_count = ?, wins = ?, losses = ?,
                    updated_at = datetime('now')
                """,
                (str(cumulative), trade_count, wins, losses),
            )
            conn.commit()

    async def update_unrealized_pnl(self, unrealized_pnl: Decimal) -> None:
        with closing(sqlite3.connect(str(self._db_path))) as conn:
            self._ensure_journal_delete(conn)
            conn.execute(
                "UPDATE virtual_portfolio SET unrealized_pnl = ?, "
                "updated_at = datetime('now')",
                (str(unrealized_pnl),),
            )
            conn.commit()

    async def reset(
        self, initial_capital: Decimal | None = None
    ) -> None:
        if initial_capital is not None:
            self._initial_capital = initial_capital
        with closing(sqlite3.connect(str(self._db_path))) as conn:
            self._ensure_journal_delete(conn)
            conn.execute("DELETE FROM virtual_portfolio")
            conn.commit()
        self._ensure_row()
        log.info(
            "virtual_portfolio_reset",
            initial_capital=str(self._initial_capital),
        )

    async def get_stats(self) -> dict:
        row = self._ensure_row()
        equity = self._current_equity
        peak = (
            Decimal(row["peak_equity"])
            if row["peak_equity"]
            else equity
        )
        drawdown_pct = (
            (peak - equity) / peak * 100 if peak > 0 else Decimal("0")
        )
        trade_count = row["trade_count"]
        wins = row["wins"]
        losses = row["losses"]
        winrate = (
            Decimal(str(wins)) / Decimal(str(trade_count)) * 100
            if trade_count > 0
            else Decimal("0")
        )
        return {
            "initial_capital": str(self._initial_capital),
            "realized_pnl": row["realized_pnl"],
            "unrealized_pnl": row["unrealized_pnl"],
            "equity": str(equity),
            "peak_equity": str(peak),
            "drawdown_pct": str(drawdown_pct.quantize(Decimal("0.01"))),
            "trade_count": trade_count,
            "wins": wins,
            "losses": losses,
            "winrate_pct": str(winrate.quantize(Decimal("0.01"))),
        }
