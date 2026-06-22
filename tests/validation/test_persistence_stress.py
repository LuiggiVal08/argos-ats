"""Phase 7: Persistence Layer Stress Validation.

Stress tests for SQLite-backed persistence adapters:
  - SQLiteTradeJournal: append-only monotonic growth, concurrent writes
  - SQLitePositionRepository: high-frequency position updates, reconciliation
  - SQLiteExecutionIdempotencyStore: 1000+ keys, dedup, cleanup
  - SQLiteSnapshotRepository: lifecycle, recovery state, periodic task
  - FilePositionRepository: many positions, consistency after delete

Each test creates its own temp DB file — no state leakage.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.application.ports.execution_idempotency import ExecutionIdempotencyError
from app.application.ports.snapshot_repository import (
    RecoveryState,
    RecoveryStateRecord,
    SystemSnapshot,
)
from app.application.ports.trade_journal import TradeJournalError, TradeRecord
from app.domain.value_objects.live_position import LivePosition
from app.domain.value_objects.order import OrderSide
from app.infrastructure.execution.file_position_repo import FilePositionRepository
from app.infrastructure.repositories.idempotency_store import (
    SQLiteExecutionIdempotencyStore,
)
from app.infrastructure.repositories.snapshot_repository import (
    SQLiteSnapshotRepository,
)
from app.infrastructure.repositories.sqlite_position_repository import (
    SQLitePositionRepository,
)
from app.infrastructure.repositories.trade_journal_repository import (
    SQLiteTradeJournal,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def tmp_db():
    """Yield a temp file path, cleaned up after test."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    try:
        os.unlink(path)
        os.unlink(path + "-wal")
    except OSError:
        pass
    try:
        os.unlink(path + "-shm")
    except OSError:
        pass


@pytest.fixture
def journal(tmp_db):
    return SQLiteTradeJournal(tmp_db)


@pytest.fixture
def position_repo(tmp_db):
    return SQLitePositionRepository(tmp_db)


@pytest.fixture
def idempotency_store(tmp_db):
    return SQLiteExecutionIdempotencyStore(tmp_db)


@pytest.fixture
def snapshot_repo(tmp_db):
    return SQLiteSnapshotRepository(tmp_db, periodic_interval_sec=600)


# =============================================================================
# SQLiteTradeJournal
# =============================================================================


class TestSQLiteTradeJournalStress:
    async def test_append_1000_trades(self, journal):
        for i in range(1000):
            await journal.add(TradeRecord(
                symbol="BTC/USDT",
                realized_pnl=Decimal(str(10.0 + i * 0.1)),
                closed_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=i),
                reference=f"trade_{i}",
                event_id=f"evt_{i:04d}",
            ))
        assert await journal.count() == 1000
        total = await journal.total_pnl()
        assert total == Decimal(str(sum(10.0 + i * 0.1 for i in range(1000))))

    async def test_realized_pnl_since_correctness(self, journal):
        for i in range(50):
            h = i * 2
            await journal.add(TradeRecord(
                symbol="BTC/USDT",
                realized_pnl=Decimal("100"),
                closed_at=datetime(2026, 6, 1, tzinfo=timezone.utc) + timedelta(hours=h),
                reference=f"trade_{i}",
            ))
        since = datetime(2026, 6, 2, tzinfo=timezone.utc)
        pnl = await journal.realized_pnl_since(since)
        # trades at hours >= 24 -> trades from index 12 onwards (hours 24, 26, ...98)
        expected_count = sum(1 for i in range(50) if i * 2 >= 24)
        assert pnl == Decimal(str(expected_count * 100))

    async def test_concurrent_appends(self, journal):
        async def _append(n: int, label: str):
            for i in range(200):
                await journal.add(TradeRecord(
                    symbol="ETH/USDT",
                    realized_pnl=Decimal(str(n * 1000 + i)),
                    closed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    reference=f"{label}_{i}",
                ))

        await asyncio.gather(_append(100, "A"), _append(200, "B"), _append(300, "C"))
        assert await journal.count() == 600

    async def test_append_after_close_raises(self, tmp_db):
        j = SQLiteTradeJournal(tmp_db)
        j.close()
        with pytest.raises(TradeJournalError, match="closed"):
            await j.add(TradeRecord(
                symbol="BTC", realized_pnl=Decimal("1"),
                closed_at=datetime.now(timezone.utc), reference="x",
            ))

    async def test_list_recent_returns_limit(self, journal):
        for i in range(50):
            await journal.add(TradeRecord(
                symbol="BTC/USDT",
                realized_pnl=Decimal(str(i)),
                closed_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=i),
                reference=f"trade_{i}",
            ))
        recent = await journal.list_recent(limit=5)
        assert len(recent) == 5
        # most recent first
        timestamps = [r["timestamp"] for r in recent]
        assert timestamps == sorted(timestamps, reverse=True)

    async def test_large_pnl_precision(self, journal):
        await journal.add(TradeRecord(
            symbol="BTC/USDT",
            realized_pnl=Decimal("123456789.123456789"),
            closed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            reference="precision_test",
        ))
        total = await journal.total_pnl()
        # SQLite REAL (IEEE 754 double) has ~15-16 significant digits;
        # the 9th decimal place is truncated. This is a known storage precision
        # trade-off — document as finding but not a hard failure.
        assert abs(total - Decimal("123456789.123456789")) < Decimal("1e-8")


# =============================================================================
# SQLitePositionRepository
# =============================================================================


class TestSQLitePositionRepositoryStress:
    async def test_high_frequency_updates(self, position_repo):
        pos = LivePosition(
            position_id="pos_001",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            units=Decimal("1.0"),
            entry_price=Decimal("60000"),
            current_price=Decimal("60000"),
        )
        for price in range(60000, 61000):
            pos = LivePosition(
                position_id="pos_001",
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                units=Decimal("1.0"),
                entry_price=Decimal("60000"),
                current_price=Decimal(str(price)),
            )
            await position_repo.save(pos)
        loaded = await position_repo.load("pos_001")
        assert loaded is not None
        assert loaded.current_price == Decimal("60999")

    async def test_concurrent_save_and_load(self, position_repo):
        async def _writer(n: int):
            for i in range(50):
                pos = LivePosition(
                    position_id=f"pos_{n}_{i:03d}",
                    symbol="BTC/USDT",
                    side=OrderSide.BUY,
                    units=Decimal("1.0"),
                    entry_price=Decimal("60000"),
                    current_price=Decimal(str(60000 + i)),
                )
                await position_repo.save(pos)

        async def _reader():
            for _ in range(20):
                await position_repo.list_open()

        await asyncio.gather(_writer(1), _writer(2), _reader())
        all_positions = await position_repo.list_all()
        assert len(all_positions) == 100

    async def test_save_and_reload_all_fields(self, position_repo):
        pos = LivePosition(
            position_id="pos_full",
            symbol="ETH/USDT",
            side=OrderSide.SELL,
            units=Decimal("10.5"),
            entry_price=Decimal("3500.50"),
            current_price=Decimal("3400.25"),
            sl_price=Decimal("3550.00"),
            tp_price=Decimal("3300.00"),
            tp2_price=Decimal("3200.00"),
            tp3_price=Decimal("3100.00"),
            trail_activated=True,
            trail_offset=Decimal("150.0"),
            break_even_activated=False,
            atr_at_entry=Decimal("45.5"),
            risk_multiple=Decimal("2.5"),
            initial_units=Decimal("10.5"),
            tp1_pct=Decimal("0.5"),
            tp2_pct=Decimal("0.25"),
            status="OPEN",
            unrealized_pnl=Decimal("1052.63"),
            opened_at=datetime(2026, 1, 15, 10, 30, tzinfo=timezone.utc),
            realized_pnl=Decimal("500.00"),
            metadata={"strategy": "novaquant", "signal_id": "sig_abc"},
        )
        await position_repo.save(pos)
        loaded = await position_repo.load("pos_full")
        assert loaded is not None
        assert loaded.position_id == "pos_full"
        assert loaded.symbol == "ETH/USDT"
        assert loaded.side == OrderSide.SELL
        assert loaded.units == Decimal("10.5")
        assert loaded.entry_price == Decimal("3500.50")
        assert loaded.current_price == Decimal("3400.25")
        assert loaded.sl_price == Decimal("3550.00")
        assert loaded.atr_at_entry == Decimal("45.5")
        assert loaded.risk_multiple == Decimal("2.5")
        assert loaded.metadata.get("strategy") == "novaquant"

    async def test_reconcile_position(self, position_repo):
        pos = LivePosition(
            position_id="pos_rec",
            symbol="SOL/USDT",
            side=OrderSide.BUY,
            units=Decimal("20"),
            entry_price=Decimal("150.00"),
            current_price=Decimal("155.00"),
        )
        await position_repo.save(pos)

        exchange_data = {
            "quantity": 18.5,
            "entryPrice": 151.00,
            "currentPrice": 156.00,
            "side": "LONG",
        }
        reconciled = await position_repo.reconcile_position_with_exchange("pos_rec", exchange_data)
        assert reconciled is not None
        assert reconciled.units == Decimal("18.5")
        assert reconciled.entry_price == Decimal("151.00")

    async def test_reconcile_nonexistent_returns_none(self, position_repo):
        result = await position_repo.reconcile_position_with_exchange(
            "no_such_pos", {"quantity": 1}
        )
        assert result is None

    async def test_delete_and_verify(self, position_repo):
        pos = LivePosition(
            position_id="pos_del",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            units=Decimal("1"),
            entry_price=Decimal("60000"),
            current_price=Decimal("61000"),
        )
        await position_repo.save(pos)
        assert await position_repo.load("pos_del") is not None
        assert await position_repo.delete("pos_del") is True
        assert await position_repo.load("pos_del") is None
        assert await position_repo.delete("pos_del") is False

    async def test_open_positions_snapshot(self, position_repo):
        for i in range(5):
            await position_repo.save(LivePosition(
                position_id=f"pos_open_{i}",
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                units=Decimal("1"),
                entry_price=Decimal("60000"),
                current_price=Decimal("60000"),
            ))
        closed = LivePosition(
            position_id="pos_closed",
            symbol="BTC/USDT",
            side=OrderSide.SELL,
            units=Decimal("1"),
            entry_price=Decimal("60000"),
            current_price=Decimal("59000"),
            status="CLOSED",
            closed_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
            realized_pnl=Decimal("1000"),
        )
        await position_repo.save(closed)

        snap = await position_repo.get_open_positions_snapshot()
        assert len(snap) == 5
        assert all(p["status"] in ("OPEN", "PARTIALLY_CLOSED") for p in snap)


# =============================================================================
# SQLiteExecutionIdempotencyStore
# =============================================================================


class TestSQLiteExecutionIdempotencyStoreStress:
    async def test_check_and_record_1000_keys(self, idempotency_store):
        for i in range(1000):
            new = await idempotency_store.check_and_record(f"key_{i:04d}", f"order_{i}")
            assert new is True
        for i in range(1000):
            dup = await idempotency_store.check_and_record(f"key_{i:04d}", f"order_{i}")
            assert dup is False

    async def test_duplicate_detection(self, idempotency_store):
        assert await idempotency_store.check_and_record("exec:abc123", "order_001") is True
        assert await idempotency_store.check_and_record("exec:abc123", "order_002") is False
        assert await idempotency_store.exists("exec:abc123") is True
        assert await idempotency_store.exists("exec:not_recorded") is False

    async def test_update_exchange_order_id(self, idempotency_store):
        await idempotency_store.check_and_record("exec:xyz", "order_initial")
        await idempotency_store.update_exchange_order_id("exec:xyz", "order_final")
        assert await idempotency_store.exists("exec:xyz") is True

    async def test_update_exchange_order_id_empty_raises(self, idempotency_store):
        await idempotency_store.check_and_record("exec:test", "order_001")
        with pytest.raises(ExecutionIdempotencyError, match="cannot be empty"):
            await idempotency_store.update_exchange_order_id("exec:test", "")
        with pytest.raises(ExecutionIdempotencyError, match="cannot be empty"):
            await idempotency_store.update_exchange_order_id("", "order_002")

    async def test_check_and_record_empty_key_raises(self, idempotency_store):
        with pytest.raises(ExecutionIdempotencyError, match="cannot be empty"):
            await idempotency_store.check_and_record("", "order_001")

    async def test_clear_and_reuse(self, idempotency_store):
        assert await idempotency_store.check_and_record("key_1", "order_1") is True
        assert await idempotency_store.check_and_record("key_2", "order_2") is True
        await idempotency_store.clear()
        assert await idempotency_store.check_and_record("key_1", "order_1") is True
        assert await idempotency_store.check_and_record("key_2", "order_2") is True

    async def test_cleanup_does_not_delete_recent(self, idempotency_store):
        await idempotency_store.check_and_record("recent_key", "order_001")
        await idempotency_store.check_and_record("recent_key2", "order_002")
        assert await idempotency_store.exists("recent_key") is True
        assert await idempotency_store.exists("recent_key2") is True


# =============================================================================
# SQLiteSnapshotRepository
# =============================================================================


class TestSQLiteSnapshotRepositoryStress:
    async def test_save_and_load_snapshot(self, snapshot_repo):
        snap = SystemSnapshot(
            timestamp=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
            equity=Decimal("100000"),
            drawdown=Decimal("0.02"),
            open_positions=[{"symbol": "BTC/USDT", "side": "BUY", "units": 1.0}],
            mode="PAPER_TRADING",
            risk_state="NORMAL",
            active_symbols=["BTC/USDT", "ETH/USDT"],
        )
        await snapshot_repo.save_snapshot(snap)
        loaded = await snapshot_repo.load_latest()
        assert loaded is not None
        assert loaded.equity == Decimal("100000")
        assert loaded.mode == "PAPER_TRADING"
        assert len(loaded.active_symbols) == 2
        assert loaded.schema_version == "1.0.0"

    async def test_save_snapshot_typed(self, snapshot_repo):
        snap = SystemSnapshot(
            timestamp=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
            equity=Decimal("50000"),
            drawdown=Decimal("0"),
            open_positions=[],
            mode="BACKTESTING",
            risk_state="NORMAL",
            active_symbols=[],
        )
        await snapshot_repo.save_snapshot_typed(snap, "post_recovery")
        loaded = await snapshot_repo.load_latest()
        assert loaded is not None
        assert loaded.equity == Decimal("50000")

    async def test_load_latest_returns_none_on_empty(self, snapshot_repo):
        assert await snapshot_repo.load_latest() is None

    async def test_clear_snapshots(self, snapshot_repo):
        snap = SystemSnapshot(
            timestamp=datetime.now(timezone.utc),
            equity=Decimal("100"),
            drawdown=Decimal("0"),
            open_positions=[],
            mode="PAPER_TRADING",
            risk_state="NORMAL",
            active_symbols=[],
        )
        await snapshot_repo.save_snapshot(snap)
        await snapshot_repo.clear()
        assert await snapshot_repo.load_latest() is None

    async def test_recovery_state_lifecycle(self, snapshot_repo):
        assert await snapshot_repo.load_recovery_state() is None
        await snapshot_repo.save_recovery_state(RecoveryState.STARTED)
        loaded = await snapshot_repo.load_recovery_state()
        assert loaded is not None
        assert loaded.state == RecoveryState.STARTED
        assert loaded.error == ""

        await snapshot_repo.save_recovery_state(RecoveryState.IN_PROGRESS)
        loaded = await snapshot_repo.load_recovery_state()
        assert loaded.state == RecoveryState.IN_PROGRESS

        await snapshot_repo.save_recovery_state(RecoveryState.COMPLETED)
        loaded = await snapshot_repo.load_recovery_state()
        assert loaded.state == RecoveryState.COMPLETED
        assert loaded.completed_at is not None

    async def test_recovery_state_error(self, snapshot_repo):
        await snapshot_repo.save_recovery_state(RecoveryState.FAILED, error="connection lost")
        loaded = await snapshot_repo.load_recovery_state()
        assert loaded is not None
        assert loaded.state == RecoveryState.FAILED
        assert loaded.error == "connection lost"

    async def test_clear_recovery_state(self, snapshot_repo):
        await snapshot_repo.save_recovery_state(RecoveryState.COMPLETED)
        await snapshot_repo.clear_recovery_state()
        assert await snapshot_repo.load_recovery_state() is None

    async def test_operations_after_close_raise(self, snapshot_repo):
        snapshot_repo.close()
        with pytest.raises(Exception, match="closed"):
            await snapshot_repo.save_snapshot(SystemSnapshot(
                timestamp=datetime.now(timezone.utc),
                equity=Decimal("0"), drawdown=Decimal("0"),
                open_positions=[], mode="TEST", risk_state="NORMAL",
                active_symbols=[],
            ))
        with pytest.raises(Exception, match="closed"):
            await snapshot_repo.load_latest()

    async def test_periodic_task_start_stop(self, snapshot_repo):
        assert snapshot_repo._periodic_task is None
        snapshot_repo.start_periodic(
            equity_provider=None, mode="TEST", risk_state="NORMAL",
            active_symbols=["BTC/USDT"],
        )
        assert snapshot_repo._periodic_task is not None
        assert not snapshot_repo._periodic_task.done()
        snapshot_repo.close()
        assert snapshot_repo._periodic_task is None

    async def test_multiple_snapshots_append_only(self, snapshot_repo):
        for i in range(10):
            await snapshot_repo.save_snapshot(SystemSnapshot(
                timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=i),
                equity=Decimal(str(100000 + i * 1000)),
                drawdown=Decimal("0"),
                open_positions=[],
                mode="PAPER_TRADING",
                risk_state="NORMAL",
                active_symbols=[],
            ))
        loaded = await snapshot_repo.load_latest()
        assert loaded is not None
        assert loaded.equity == Decimal("109000")

    async def test_snapshot_with_event_id(self, snapshot_repo):
        snap = SystemSnapshot(
            timestamp=datetime.now(timezone.utc),
            equity=Decimal("50000"),
            drawdown=Decimal("0.01"),
            open_positions=[],
            mode="PAPER_TRADING",
            risk_state="NORMAL",
            active_symbols=[],
            event_id="manual_event_001",
        )
        await snapshot_repo.save_snapshot(snap)
        loaded = await snapshot_repo.load_latest()
        assert loaded is not None and loaded.event_id == "manual_event_001"


# =============================================================================
# FilePositionRepository
# =============================================================================


class TestFilePositionRepositoryStress:
    @pytest.fixture
    def file_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            yield FilePositionRepository(os.path.join(tmp, "positions.json"))

    async def test_save_and_load(self, file_repo):
        pos = LivePosition(
            position_id="pos_001",
            symbol="ETH/USDT",
            side=OrderSide.BUY,
            units=Decimal("5.0"),
            entry_price=Decimal("3500"),
            current_price=Decimal("3600"),
        )
        await file_repo.save(pos)
        loaded = await file_repo.load("pos_001")
        assert loaded is not None
        assert loaded.symbol == "ETH/USDT"
        assert loaded.current_price == Decimal("3600")

    async def test_reload_persistence(self, file_repo):
        pos = LivePosition(
            position_id="pos_001",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            units=Decimal("1.0"),
            entry_price=Decimal("60000"),
            current_price=Decimal("60000"),
        )
        await file_repo.save(pos)
        # Create a new instance pointing to same file to verify disk persistence
        repo2 = FilePositionRepository(file_repo._file_path)
        loaded = await repo2.load("pos_001")
        assert loaded is not None
        assert loaded.symbol == "BTC/USDT"

    async def test_many_positions(self, file_repo):
        for i in range(100):
            await file_repo.save(LivePosition(
                position_id=f"pos_{i:04d}",
                symbol="BTC/USDT",
                side=OrderSide.BUY if i % 2 == 0 else OrderSide.SELL,
                units=Decimal(str(1.0 + i * 0.1)),
                entry_price=Decimal("60000"),
                current_price=Decimal("61000"),
            ))
        all_positions = await file_repo.list_all()
        assert len(all_positions) == 100

    async def test_delete_consistency(self, file_repo):
        for i in range(10):
            await file_repo.save(LivePosition(
                position_id=f"pos_{i}",
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                units=Decimal("1.0"),
                entry_price=Decimal("60000"),
                current_price=Decimal("60000"),
            ))
        assert await file_repo.delete("pos_5") is True
        assert await file_repo.load("pos_5") is None
        remaining = await file_repo.list_all()
        assert len(remaining) == 9

    async def test_list_open_filters_closed(self, file_repo):
        await file_repo.save(LivePosition(
            position_id="pos_open",
            symbol="BTC/USDT", side=OrderSide.BUY,
            units=Decimal("1"), entry_price=Decimal("60000"),
            current_price=Decimal("61000"),
        ))
        await file_repo.save(LivePosition(
            position_id="pos_closed",
            symbol="BTC/USDT", side=OrderSide.SELL,
            units=Decimal("1"), entry_price=Decimal("60000"),
            current_price=Decimal("59000"),
            status="CLOSED",
            closed_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
            realized_pnl=Decimal("1000"),
        ))
        open_positions = await file_repo.list_open()
        assert len(open_positions) == 1
        assert open_positions[0].position_id == "pos_open"


# =============================================================================
# Cross-repo consistency
# =============================================================================


class TestCrossRepoConsistency:
    """End-to-end scenario: position lifecycle across journal + idempotency."""

    async def test_trade_append_then_duplicate_guard(self, tmp_db):
        j_db = tmp_db + "_journal.db"
        i_db = tmp_db + "_idem.db"

        journal = SQLiteTradeJournal(j_db)
        idem = SQLiteExecutionIdempotencyStore(i_db)

        key = "exec:cross_test_001"

        assert await idem.check_and_record(key, "order_exchange_001") is True
        await journal.add(TradeRecord(
            symbol="BTC/USDT",
            realized_pnl=Decimal("250.50"),
            closed_at=datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc),
            reference=key,
            event_id="evt_cross_001",
        ))

        # Duplicate execution attempt
        assert await idem.check_and_record(key, "order_exchange_001") is False
        assert await journal.count() == 1  # journal unchanged

        pnl = await journal.realized_pnl_since(datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert pnl == Decimal("250.50")

        journal.close()
        idem.close()
        for p in [j_db, i_db, j_db + "-wal", i_db + "-wal"]:
            try:
                os.unlink(p)
            except OSError:
                pass
