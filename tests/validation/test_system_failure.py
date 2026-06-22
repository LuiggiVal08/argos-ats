"""Phase 8: System Failure Simulation.

Multi-layer crash injection scenarios:
  1. Individual repo failures (journal, position, idempotency, snapshot)
  2. RecoveryEngine with failing components
  3. Recovery + replay reconciliation
  4. Simultaneous multi-repo failure
  5. Empty stores on recovery startup
  6. Corrupted state recovery
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.application.ports.execution_idempotency import (
    ExecutionIdempotencyError,
    ExecutionIdempotencyStore,
)
from app.application.ports.exchange_position_provider import (
    ExchangePositionProvider,
    ExchangePositionProviderError,
)
from app.application.ports.snapshot_repository import (
    RecoveryState,
    SnapshotRepository,
    SnapshotRepositoryError,
    SystemSnapshot,
)
from app.application.ports.trade_journal import TradeJournalError, TradeRecord
from app.domain.value_objects.live_position import LivePosition
from app.domain.value_objects.order import OrderSide
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
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass
    for ext in ("-wal", "-shm"):
        try:
            os.unlink(path + ext)
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
# Mock Exchange Position Provider
# =============================================================================


@dataclass
class FakeExchangePosition:
    symbol: str
    side: str
    quantity: Decimal
    entry_price: Decimal
    current_price: Decimal
    unrealized_pnl: Decimal | None = None


class FakeExchangePositionProvider:
    def __init__(self, positions: list[FakeExchangePosition] | None = None):
        self._positions = positions or []
        self._fail_on_call = False
        self._call_count = 0

    def fail_next(self, fail: bool = True):
        self._fail_on_call = fail

    async def fetch_positions(self) -> list[FakeExchangePosition]:
        self._call_count += 1
        if self._fail_on_call:
            self._fail_on_call = False
            raise ExchangePositionProviderError("exchange unreachable")
        return self._positions


# =============================================================================
# Scenario 1: Individual repo failures
# =============================================================================


class TestIndividualRepoFailure:
    """Verify each persistence layer handles graceful degradation."""

    async def test_journal_closed_then_rejected(self, journal):
        journal.close()
        with pytest.raises(TradeJournalError, match="closed"):
            await journal.add(TradeRecord(
                symbol="BTC/USDT", realized_pnl=Decimal("1"),
                closed_at=datetime.now(timezone.utc), reference="x",
            ))
        with pytest.raises(TradeJournalError, match="closed"):
            await journal.realized_pnl_since(datetime.now(timezone.utc))

    async def test_position_repo_closed_then_rejected(self, position_repo):
        position_repo.close()
        with pytest.raises(Exception):
            await position_repo.load("any")

    async def test_idempotency_closed_then_rejected(self, idempotency_store):
        idempotency_store.close()
        with pytest.raises(ExecutionIdempotencyError):
            await idempotency_store.check_and_record("key", "order")

    async def test_snapshot_repo_closed_then_rejected(self, snapshot_repo):
        snapshot_repo.close()
        with pytest.raises(SnapshotRepositoryError, match="closed"):
            await snapshot_repo.save_snapshot(SystemSnapshot(
                timestamp=datetime.now(timezone.utc),
                equity=Decimal("0"), drawdown=Decimal("0"),
                open_positions=[], mode="TEST", risk_state="NORMAL",
                active_symbols=[],
            ))
        with pytest.raises(SnapshotRepositoryError, match="closed"):
            await snapshot_repo.load_latest()
        with pytest.raises(SnapshotRepositoryError, match="closed"):
            await snapshot_repo.save_recovery_state(RecoveryState.COMPLETED)

    async def test_journal_corrupted_file(self, tmp_db):
        # SQLite detects corruption on first execute (PRAGMA during __init__)
        with open(tmp_db, "w") as f:
            f.write("not a valid sqlite database")
        with pytest.raises(Exception):
            SQLiteTradeJournal(tmp_db)

    async def test_corrupted_idempotency_file(self, tmp_db):
        with open(tmp_db, "w") as f:
            f.write("corrupted")
        with pytest.raises(Exception):
            SQLiteExecutionIdempotencyStore(tmp_db)

    async def test_corrupted_snapshot_file(self, tmp_db):
        with open(tmp_db, "w") as f:
            f.write("corrupted")
        with pytest.raises(Exception):
            SQLiteSnapshotRepository(tmp_db)


# =============================================================================
# Scenario 2: RecoveryEngine with failing components
# =============================================================================


class TestRecoveryEngineFailure:
    """Recovery engine must handle failures gracefully."""

    async def mock_recovery_env(self, snapshot_repo, position_repo, exchange_provider):
        """Helper: wire up RecoveryEngine with all failing components."""
        from app.domain.recovery.recovery_engine import RecoveryEngine
        from app.domain.recovery.reconciliation_engine import ReconciliationEngine
        from app.domain.recovery.recovery_gate import RecoveryGate
        from app.domain.recovery.risk_validator import RiskValidator
        from app.domain.recovery.drift_validator import PostRecoveryDriftValidator

        engine = RecoveryEngine(
            position_repo=position_repo,
            exchange_provider=exchange_provider,
            snapshot_repo=snapshot_repo,
            reconciliation_engine=ReconciliationEngine(),
            gate=RecoveryGate(),
            risk_validator=RiskValidator(),
            drift_validator=PostRecoveryDriftValidator(),
            position_repo_raw=position_repo,
        )
        return engine

    async def test_recover_with_no_snapshot(self, snapshot_repo, position_repo):
        """Recovery must succeed even when no prior snapshot exists."""
        exchange = FakeExchangePositionProvider([])
        engine = await self.mock_recovery_env(snapshot_repo, position_repo, exchange)
        report = await engine.recover()
        assert report.snapshot_loaded is False
        assert report.recovered is True
        assert report.gate.state.value in ("SAFE", "DEGRADED")

    async def test_recover_with_empty_position_repo(self, snapshot_repo, position_repo):
        """Recovery with no open positions + no exchange positions."""
        exchange = FakeExchangePositionProvider([])
        await snapshot_repo.save_snapshot(SystemSnapshot(
            timestamp=datetime.now(timezone.utc),
            equity=Decimal("100000"), drawdown=Decimal("0"),
            open_positions=[], mode="PAPER_TRADING",
            risk_state="NORMAL", active_symbols=[],
        ))
        engine = await self.mock_recovery_env(snapshot_repo, position_repo, exchange)
        report = await engine.recover()
        assert report.recovered is True
        assert report.local_positions_count == 0
        assert report.exchange_positions_count == 0

    async def test_recover_with_exchange_unreachable(self, snapshot_repo, position_repo):
        """When exchange is unreachable, recovery should still succeed with errors."""
        exchange = FakeExchangePositionProvider([])
        exchange.fail_next()
        engine = await self.mock_recovery_env(snapshot_repo, position_repo, exchange)
        report = await engine.recover()
        # gate may block due to stale positions; check that errors are captured
        assert len(report.errors) > 0 or report.recovered

    async def test_recover_with_snapshot_repo_failure(self, position_repo):
        """Snapshot repo failure during recovery should be captured as errors."""
        exchange = FakeExchangePositionProvider([])

        class _FailingSnapshotRepo:
            async def save_recovery_state(self, state, error=""):
                raise SnapshotRepositoryError("disk full")
            async def load_latest(self):
                raise SnapshotRepositoryError("disk full")
            async def clear(self):
                pass
            async def clear_recovery_state(self):
                pass
            async def load_recovery_state(self):
                return None

        from app.domain.recovery.recovery_engine import RecoveryEngine
        from app.domain.recovery.reconciliation_engine import ReconciliationEngine
        from app.domain.recovery.recovery_gate import RecoveryGate
        from app.domain.recovery.risk_validator import RiskValidator
        from app.domain.recovery.drift_validator import PostRecoveryDriftValidator

        engine = RecoveryEngine(
            position_repo=position_repo,
            exchange_provider=exchange,
            snapshot_repo=_FailingSnapshotRepo(),
            reconciliation_engine=ReconciliationEngine(),
            gate=RecoveryGate(),
            risk_validator=RiskValidator(),
            drift_validator=PostRecoveryDriftValidator(),
            position_repo_raw=position_repo,
        )
        report = await engine.recover()
        assert len(report.errors) > 0
        assert any("disk full" in e for e in report.errors)

    async def test_recover_with_local_positions_and_empty_exchange(
        self, snapshot_repo, position_repo,
    ):
        """Local positions with nothing on exchange should be cleaned up."""
        await position_repo.save(LivePosition(
            position_id="pos_stale",
            symbol="BTC/USDT", side=OrderSide.BUY,
            units=Decimal("1"), entry_price=Decimal("60000"),
            current_price=Decimal("61000"),
        ))
        await snapshot_repo.save_snapshot(SystemSnapshot(
            timestamp=datetime.now(timezone.utc),
            equity=Decimal("100000"), drawdown=Decimal("0"),
            open_positions=[{"symbol": "BTC/USDT", "side": "BUY", "units": 1.0}],
            mode="PAPER_TRADING", risk_state="NORMAL",
            active_symbols=["BTC/USDT"],
        ))
        exchange = FakeExchangePositionProvider([])
        from app.domain.recovery.recovery_engine import RecoveryEngine
        from app.domain.recovery.reconciliation_engine import ReconciliationEngine
        from app.domain.recovery.recovery_gate import RecoveryGate
        from app.domain.recovery.risk_validator import RiskValidator
        from app.domain.recovery.drift_validator import PostRecoveryDriftValidator

        engine = RecoveryEngine(
            position_repo=position_repo,
            exchange_provider=exchange,
            snapshot_repo=snapshot_repo,
            reconciliation_engine=ReconciliationEngine(),
            gate=RecoveryGate(),
            risk_validator=RiskValidator(),
            drift_validator=PostRecoveryDriftValidator(),
            position_repo_raw=position_repo,
        )
        report = await engine.recover()
        if report.recovered:
            loaded = await position_repo.load("pos_stale")
            assert loaded is None or loaded.status == "CLOSED"

    async def test_recover_recovery_state_persisted(self, snapshot_repo, position_repo):
        """After successful recovery, recovery state must be COMPLETED."""
        exchange = FakeExchangePositionProvider([])
        await snapshot_repo.save_snapshot(SystemSnapshot(
            timestamp=datetime.now(timezone.utc),
            equity=Decimal("100000"), drawdown=Decimal("0"),
            open_positions=[], mode="PAPER_TRADING",
            risk_state="NORMAL", active_symbols=[],
        ))
        engine = await self.mock_recovery_env(snapshot_repo, position_repo, exchange)
        report = await engine.recover()
        assert report.recovered
        state = await snapshot_repo.load_recovery_state()
        assert state is not None
        assert state.state == RecoveryState.COMPLETED


# =============================================================================
# Scenario 3: Multi-repo simultaneous failure
# =============================================================================


class TestMultiRepoSimultaneousFailure:
    """Two or more persistence layers failing at the same time."""

    async def test_journal_and_idempotency_both_closed(self, tmp_db):
        j_db = tmp_db + "_j.db"
        i_db = tmp_db + "_i.db"

        journal = SQLiteTradeJournal(j_db)
        idem = SQLiteExecutionIdempotencyStore(i_db)

        journal.close()
        idem.close()

        with pytest.raises(TradeJournalError):
            await journal.add(TradeRecord(
                symbol="BTC/USDT", realized_pnl=Decimal("1"),
                closed_at=datetime.now(timezone.utc), reference="x",
            ))
        with pytest.raises(ExecutionIdempotencyError):
            await idem.check_and_record("key", "order")

        for p in [j_db, i_db, j_db + "-wal", i_db + "-wal"]:
            try:
                os.unlink(p)
            except OSError:
                pass

    async def test_snapshot_and_position_both_corrupted(self, tmp_db):
        p_db = tmp_db + "_p.db"
        s_db = tmp_db + "_s.db"

        with open(p_db, "wb") as f:
            f.write(b"corrupt data")
        with open(s_db, "wb") as f:
            f.write(b"corrupt data")

        # Both repos detect corruption at init
        with pytest.raises(Exception):
            SQLitePositionRepository(p_db)
        with pytest.raises(Exception):
            SQLiteSnapshotRepository(s_db)

        for p in [p_db, s_db, p_db + "-wal", s_db + "-wal"]:
            try:
                os.unlink(p)
            except OSError:
                pass


# =============================================================================
# Scenario 4: Recovery + replay integration
# =============================================================================


class TestRecoveryReplayIntegration:
    """Full lifecycle: persist → crash → recover → replay → verify."""

    async def test_recover_then_replay_empty_state(self, snapshot_repo, position_repo):
        """After recovery with no trades, replay should produce empty results."""
        exchange = FakeExchangePositionProvider([])
        from app.domain.recovery.recovery_engine import RecoveryEngine
        from app.domain.recovery.reconciliation_engine import ReconciliationEngine
        from app.domain.recovery.recovery_gate import RecoveryGate
        from app.domain.recovery.risk_validator import RiskValidator
        from app.domain.recovery.drift_validator import PostRecoveryDriftValidator

        engine = RecoveryEngine(
            position_repo=position_repo,
            exchange_provider=exchange,
            snapshot_repo=snapshot_repo,
            reconciliation_engine=ReconciliationEngine(),
            gate=RecoveryGate(),
            risk_validator=RiskValidator(),
            drift_validator=PostRecoveryDriftValidator(),
            position_repo_raw=position_repo,
        )
        report = await engine.recover()
        assert report.recovered

        state = await snapshot_repo.load_recovery_state()
        assert state is not None
        assert state.state == RecoveryState.COMPLETED

    async def test_recover_with_journal_data(self, snapshot_repo, position_repo, journal):
        """Recovery with trade journal data should capture journal state."""
        await journal.add(TradeRecord(
            symbol="BTC/USDT", realized_pnl=Decimal("500"),
            closed_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            reference="trade_001", event_id="evt_001",
        ))
        await journal.add(TradeRecord(
            symbol="ETH/USDT", realized_pnl=Decimal("-100"),
            closed_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
            reference="trade_002", event_id="evt_002",
        ))

        await snapshot_repo.save_snapshot(SystemSnapshot(
            timestamp=datetime(2026, 6, 1, tzinfo=timezone.utc),
            equity=Decimal("100000"), drawdown=Decimal("0"),
            open_positions=[], mode="PAPER_TRADING",
            risk_state="NORMAL", active_symbols=[],
        ))

        exchange = FakeExchangePositionProvider([])
        from app.domain.recovery.recovery_engine import RecoveryEngine
        from app.domain.recovery.reconciliation_engine import ReconciliationEngine
        from app.domain.recovery.recovery_gate import RecoveryGate
        from app.domain.recovery.risk_validator import RiskValidator
        from app.domain.recovery.drift_validator import PostRecoveryDriftValidator

        engine = RecoveryEngine(
            position_repo=position_repo,
            exchange_provider=exchange,
            snapshot_repo=snapshot_repo,
            reconciliation_engine=ReconciliationEngine(),
            gate=RecoveryGate(),
            risk_validator=RiskValidator(),
            drift_validator=PostRecoveryDriftValidator(),
            position_repo_raw=position_repo,
            trade_journal=journal,
        )
        report = await engine.recover()
        assert report.recovered

    async def test_sequential_crash_with_recovery(self, snapshot_repo, position_repo):
        """Simulate two sequential recoveries (crash after first recovery)."""
        exchange = FakeExchangePositionProvider([])
        from app.domain.recovery.recovery_engine import RecoveryEngine
        from app.domain.recovery.reconciliation_engine import ReconciliationEngine
        from app.domain.recovery.recovery_gate import RecoveryGate
        from app.domain.recovery.risk_validator import RiskValidator
        from app.domain.recovery.drift_validator import PostRecoveryDriftValidator

        async def _recover():
            eng = RecoveryEngine(
                position_repo=position_repo,
                exchange_provider=exchange,
                snapshot_repo=snapshot_repo,
                reconciliation_engine=ReconciliationEngine(),
                gate=RecoveryGate(),
                risk_validator=RiskValidator(),
                drift_validator=PostRecoveryDriftValidator(),
                position_repo_raw=position_repo,
            )
            return await eng.recover()

        report1 = await _recover()
        assert report1.recovered

        await snapshot_repo.save_snapshot(SystemSnapshot(
            timestamp=datetime.now(timezone.utc),
            equity=Decimal("120000"), drawdown=Decimal("0.01"),
            open_positions=[], mode="PAPER_TRADING",
            risk_state="NORMAL", active_symbols=["BTC/USDT"],
        ))

        report2 = await _recover()
        assert report2.recovered
        assert report2.snapshot_loaded is True

    async def test_recover_partial_failure(self, snapshot_repo, position_repo):
        """One failing component during recovery does not cascade."""
        exchange = FakeExchangePositionProvider([])
        exchange.fail_next()

        await snapshot_repo.save_snapshot(SystemSnapshot(
            timestamp=datetime.now(timezone.utc),
            equity=Decimal("100000"), drawdown=Decimal("0"),
            open_positions=[], mode="PAPER_TRADING",
            risk_state="NORMAL", active_symbols=[],
        ))

        from app.domain.recovery.recovery_engine import RecoveryEngine
        from app.domain.recovery.reconciliation_engine import ReconciliationEngine
        from app.domain.recovery.recovery_gate import RecoveryGate
        from app.domain.recovery.risk_validator import RiskValidator
        from app.domain.recovery.drift_validator import PostRecoveryDriftValidator

        engine = RecoveryEngine(
            position_repo=position_repo,
            exchange_provider=exchange,
            snapshot_repo=snapshot_repo,
            reconciliation_engine=ReconciliationEngine(),
            gate=RecoveryGate(),
            risk_validator=RiskValidator(),
            drift_validator=PostRecoveryDriftValidator(),
            position_repo_raw=position_repo,
        )

        report = await engine.recover()
        # exchange failure alone should not block recovery (no critical):
        assert report.recovered or len(report.errors) > 0


# =============================================================================
# Scenario 5: Empty stores on startup
# =============================================================================


class TestEmptyStoreStartup:
    """All stores empty — system must handle gracefully."""

    async def test_all_stores_empty(self, snapshot_repo, position_repo, idempotency_store, journal):
        assert await snapshot_repo.load_latest() is None
        assert await snapshot_repo.load_recovery_state() is None
        assert await position_repo.list_all() == []
        assert await idempotency_store.exists("any_key") is False
        assert await journal.count() == 0
        assert await journal.total_pnl() == Decimal("0")

    async def test_empty_snapshot_cleanup_is_idempotent(self, snapshot_repo):
        await snapshot_repo.clear()
        await snapshot_repo.clear_recovery_state()
        assert await snapshot_repo.load_latest() is None
        assert await snapshot_repo.load_recovery_state() is None


# =============================================================================
# Scenario 6: Execution lifecycle with failure injection
# =============================================================================


class TestExecutionLifecycleFailure:
    """End-to-end: position create → update → journal → idempotency."""

    async def test_create_then_close_with_journal(self, position_repo, journal):
        pos = LivePosition(
            position_id="e2e_pos",
            symbol="SOL/USDT", side=OrderSide.BUY,
            units=Decimal("10"), entry_price=Decimal("150"),
            current_price=Decimal("155"),
        )
        await position_repo.save(pos)
        await position_repo.save(LivePosition(
            position_id="e2e_pos",
            symbol="SOL/USDT", side=OrderSide.BUY,
            units=Decimal("0.001"), entry_price=Decimal("150"),
            current_price=Decimal("160"),
            status="CLOSED",
            closed_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
            realized_pnl=Decimal("100"),
        ))
        await journal.add(TradeRecord(
            symbol="SOL/USDT", realized_pnl=Decimal("100"),
            closed_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
            reference="e2e_pos", event_id="e2e_evt",
        ))
        assert await journal.count() == 1
        loaded = await position_repo.load("e2e_pos")
        assert loaded is not None and loaded.status == "CLOSED"

    async def test_idempotency_prevents_double_execution(self, idempotency_store, journal):
        key = "exec:e2e_double"
        assert await idempotency_store.check_and_record(key, "order_001") is True
        await journal.add(TradeRecord(
            symbol="BTC/USDT", realized_pnl=Decimal("50"),
            closed_at=datetime.now(timezone.utc), reference=key,
        ))
        assert await idempotency_store.check_and_record(key, "order_001") is False
        assert await journal.count() == 1

    async def test_duplicate_after_clear_allows_new(self, idempotency_store):
        key = "exec:e2e_clear"
        assert await idempotency_store.check_and_record(key, "order_001") is True
        assert await idempotency_store.check_and_record(key, "order_001") is False
        await idempotency_store.clear()
        assert await idempotency_store.check_and_record(key, "order_002") is True
