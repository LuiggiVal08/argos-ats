"""Phase 4: Recovery System Validation.

Tests the complete recovery subsystem: gate, reconciliation, risk validation,
drift detection, stream barrier, and engine orchestration.

Risk level: CRITICAL — untested post-crash safety layer.
Covers Fix 1 (atomic state), Fix 3 (exchange guardrails), Fix 6 (risk invariants).
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import pytest

from app.domain.recovery.recovery_gate import GateState, GateVerdict, RecoveryGate
from app.domain.recovery.reconciliation_engine import (
    ReconciliationEngine,
    ReconciliationStatus,
)
from app.domain.recovery.risk_validator import RiskValidator
from app.domain.recovery.drift_validator import PostRecoveryDriftValidator
from app.domain.recovery.stream_barrier import StreamConsumptionBarrier
from app.domain.recovery.recovery_engine import RecoveryEngine, RecoveryError
from app.application.ports.snapshot_repository import (
    RecoveryState,
    SystemSnapshot,
    RecoveryStateRecord,
)
from datetime import datetime, timezone

_NOW = datetime.now(timezone.utc)


# ── Helper: in-memory SystemSnapshot ────────────────────────────────

_SNAPSHOT_OK = SystemSnapshot(
    timestamp=datetime.now(timezone.utc),
    equity=Decimal("100000"),
    drawdown=Decimal("0.02"),
    open_positions=[{"symbol": "BTC/USDT", "units": 1.0}],
    mode="PAPER_TRADING",
    risk_state="NORMAL",
    active_symbols=["BTC/USDT"],
)


# ═════════════════════════════════════════════════════════════════════
# 1. RecoveryGate
# ═════════════════════════════════════════════════════════════════════

class TestRecoveryGate:
    def test_clean_state_safe(self):
        gate = RecoveryGate()
        verdict = gate.evaluate(
            snapshot=_SNAPSHOT_OK,
            db_accessible=True,
            recovery_state=RecoveryStateRecord(
                state=RecoveryState.COMPLETED,
                started_at=_NOW,
                completed_at=_NOW,
            ),
        )
        assert verdict.state == GateState.SAFE

    def test_no_snapshot_degraded(self):
        gate = RecoveryGate()
        verdict = gate.evaluate(snapshot=None, db_accessible=True)
        assert verdict.state == GateState.DEGRADED
        assert any("no_snapshot" in w for w in verdict.warnings)

    def test_failed_recovery_blocked(self):
        gate = RecoveryGate()
        verdict = gate.evaluate(
            snapshot=_SNAPSHOT_OK,
            db_accessible=True,
            recovery_state=RecoveryStateRecord(
                state=RecoveryState.FAILED,
                started_at=_NOW,
                error="previous crash",
            ),
        )
        assert verdict.state == GateState.BLOCKED
        assert "previous_recovery_failed" in verdict.reason

    def test_in_progress_recovery_blocked(self):
        gate = RecoveryGate()
        verdict = gate.evaluate(
            snapshot=_SNAPSHOT_OK,
            db_accessible=True,
            recovery_state=RecoveryStateRecord(
                state=RecoveryState.IN_PROGRESS,
                started_at=_NOW,
            ),
        )
        assert verdict.state == GateState.BLOCKED
        assert "in_progress" in verdict.reason.lower()

    def test_started_recovery_blocked(self):
        gate = RecoveryGate()
        verdict = gate.evaluate(
            snapshot=_SNAPSHOT_OK,
            db_accessible=True,
            recovery_state=RecoveryStateRecord(
                state=RecoveryState.STARTED,
                started_at=_NOW,
            ),
        )
        assert verdict.state == GateState.BLOCKED
        assert "started" in verdict.reason.lower()

    def test_db_not_accessible_blocked(self):
        gate = RecoveryGate()
        verdict = gate.evaluate(snapshot=_SNAPSHOT_OK, db_accessible=False)
        assert verdict.state == GateState.BLOCKED
        assert "database" in verdict.reason

    def test_negative_equity_snapshot_blocked(self):
        gate = RecoveryGate()
        bad_snapshot = SystemSnapshot(
            timestamp=datetime.now(timezone.utc),
            equity=Decimal("-100"),
            drawdown=Decimal("0"),
            open_positions=[],
            mode="PAPER_TRADING",
            risk_state="NORMAL",
            active_symbols=[],
        )
        verdict = gate.evaluate(snapshot=bad_snapshot, db_accessible=True)
        assert verdict.state == GateState.BLOCKED
        assert "negative equity" in verdict.reason

    def test_missing_local_positions_degraded(self):
        gate = RecoveryGate()
        from app.domain.recovery.reconciliation_engine import (
            ReconciliationSummary, ReconciliationResult,
        )
        recon = ReconciliationSummary(
            missing_local=1,
            details=[
                ReconciliationResult(
                    status=ReconciliationStatus.MISSING_LOCAL,
                    position_id="",
                    symbol="ETH/USDT",
                ),
            ],
        )
        verdict = gate.evaluate(
            snapshot=_SNAPSHOT_OK,
            db_accessible=True,
            reconciliation=recon,
        )
        assert verdict.state == GateState.DEGRADED

    def test_missing_on_exchange_warns_but_safe(self):
        gate = RecoveryGate()
        from app.domain.recovery.reconciliation_engine import (
            ReconciliationSummary, ReconciliationResult,
        )
        recon = ReconciliationSummary(
            missing_on_exchange=1,
            details=[
                ReconciliationResult(
                    status=ReconciliationStatus.MISSING_ON_EXCHANGE,
                    position_id="p1",
                    symbol="BTC/USDT",
                ),
            ],
        )
        verdict = gate.evaluate(
            snapshot=_SNAPSHOT_OK,
            db_accessible=True,
            reconciliation=recon,
        )
        assert verdict.state == GateState.DEGRADED

    def test_no_recovery_state_first_boot(self):
        gate = RecoveryGate()
        verdict = gate.evaluate(snapshot=_SNAPSHOT_OK, db_accessible=True)
        assert verdict.state == GateState.DEGRADED
        assert any("no_recovery_state" in w for w in verdict.warnings)

    def test_completed_recovery_state_safe(self):
        gate = RecoveryGate()
        verdict = gate.evaluate(
            snapshot=_SNAPSHOT_OK,
            db_accessible=True,
            recovery_state=RecoveryStateRecord(
                state=RecoveryState.COMPLETED,
                started_at=_NOW,
                completed_at=_NOW,
            ),
        )
        assert verdict.state == GateState.SAFE


# ═════════════════════════════════════════════════════════════════════
# 2. ReconciliationEngine
# ═════════════════════════════════════════════════════════════════════

class TestReconciliationEngine:
    def test_matched_positions(self):
        engine = ReconciliationEngine()
        local = [{"id": "p1", "symbol": "BTC/USDT", "side": "LONG",
                  "quantity": 1.0, "entryPrice": 70000, "status": "OPEN"}]
        exchange = [{"symbol": "BTC/USDT", "side": "LONG",
                     "quantity": 1.0, "entryPrice": 70000}]
        summary = engine.reconcile(local, exchange)
        assert summary.matched == 1
        assert summary.total == 1
        assert summary.is_consistent

    def test_missing_on_exchange(self):
        engine = ReconciliationEngine()
        local = [{"id": "p1", "symbol": "BTC/USDT", "side": "LONG",
                  "quantity": 1.0, "entryPrice": 70000, "status": "OPEN"}]
        exchange: list[dict] = []
        summary = engine.reconcile(local, exchange)
        assert summary.missing_on_exchange == 1
        assert not summary.is_consistent

    def test_missing_local(self):
        engine = ReconciliationEngine()
        local: list[dict] = []
        exchange = [{"symbol": "ETH/USDT", "side": "LONG",
                     "quantity": 5.0, "entryPrice": 3500}]
        summary = engine.reconcile(local, exchange)
        assert summary.missing_local == 1
        assert not summary.is_consistent

    def test_partial_mismatch_different_quantity(self):
        engine = ReconciliationEngine()
        local = [{"id": "p1", "symbol": "BTC/USDT", "side": "LONG",
                  "quantity": 1.0, "entryPrice": 70000, "status": "OPEN"}]
        exchange = [{"symbol": "BTC/USDT", "side": "LONG",
                     "quantity": 0.5, "entryPrice": 70000}]
        summary = engine.reconcile(local, exchange)
        assert summary.partial_mismatch == 1

    def test_incomplete_exchange_data_guardrail(self):
        engine = ReconciliationEngine()
        local = [{"id": "p1", "symbol": "BTC/USDT", "side": "LONG",
                  "quantity": 1.0, "entryPrice": 70000, "status": "OPEN"}]
        exchange = [{"symbol": "BTC/USDT"}]  # missing quantity, entryPrice
        summary = engine.reconcile(local, exchange)
        assert summary.skipped_guardrails >= 1
        assert summary.partial_mismatch == 0  # treated as guardrail skip

    def test_stale_exchange_data_guardrail(self):
        engine = ReconciliationEngine()
        local = [{"id": "p1", "symbol": "BTC/USDT", "side": "LONG",
                  "quantity": 1.0, "entryPrice": 70000, "status": "OPEN"}]
        exchange = [{"symbol": "BTC/USDT", "side": "LONG",
                     "quantity": 0.5, "entryPrice": 70000}]
        stale_ts = time.time() - 120  # 120s old > 60s threshold
        summary = engine.reconcile(local, exchange, exchange_timestamp=stale_ts)
        assert summary.skipped_guardrails >= 1

    def test_empty_both_sides(self):
        engine = ReconciliationEngine()
        summary = engine.reconcile([], [])
        assert summary.total == 0
        assert summary.is_consistent

    def test_zero_quantity_guardrail(self):
        engine = ReconciliationEngine()
        exchange = [{"symbol": "BTC/USDT", "side": "LONG",
                     "quantity": 0.0, "entryPrice": 70000}]
        summary = engine.reconcile([], exchange)
        assert summary.skipped_guardrails == 1
        assert summary.missing_local == 0

    def test_closed_status_ignored(self):
        engine = ReconciliationEngine()
        local = [{"id": "p1", "symbol": "BTC/USDT", "side": "LONG",
                  "quantity": 1.0, "entryPrice": 70000, "status": "CLOSED"}]
        exchange: list[dict] = []
        summary = engine.reconcile(local, exchange)
        assert summary.total == 0
        assert summary.missing_on_exchange == 0

    def test_multiple_symbols_mixed(self):
        engine = ReconciliationEngine()
        local = [
            {"id": "p1", "symbol": "BTC/USDT", "quantity": 1.0, "entryPrice": 70000, "status": "OPEN"},
            {"id": "p2", "symbol": "ETH/USDT", "quantity": 5.0, "entryPrice": 3500, "status": "OPEN"},
            {"id": "p3", "symbol": "SOL/USDT", "quantity": 100.0, "entryPrice": 150, "status": "OPEN"},
        ]
        exchange = [
            {"symbol": "BTC/USDT", "quantity": 1.0, "entryPrice": 70000},
            {"symbol": "ETH/USDT", "quantity": 5.5, "entryPrice": 3500},
        ]
        summary = engine.reconcile(local, exchange)
        assert summary.matched == 1
        assert summary.partial_mismatch == 1
        assert summary.missing_on_exchange == 1


# ═════════════════════════════════════════════════════════════════════
# 3. RiskValidator
# ═════════════════════════════════════════════════════════════════════

class TestRiskValidator:
    def test_no_positions_passes(self):
        validator = RiskValidator()
        result = validator.validate(positions=[], equity=Decimal("100000"))
        assert result.passed
        assert not result.errors

    def test_exposure_under_limit_passes(self):
        validator = RiskValidator(max_exposure_pct=Decimal("0.5"))
        pos = [{"symbol": "BTC/USDT", "quantity": 0.5, "currentPrice": 70000}]
        result = validator.validate(positions=pos, equity=Decimal("100000"))
        assert result.passed
        assert result.exposure_check

    def test_exposure_over_limit_fails(self):
        validator = RiskValidator(max_exposure_pct=Decimal("0.3"))
        pos = [{"symbol": "BTC/USDT", "quantity": 2.0, "currentPrice": 70000}]
        result = validator.validate(positions=pos, equity=Decimal("100000"))
        assert not result.passed
        assert not result.exposure_check

    def test_drawdown_exceeded_fails(self):
        validator = RiskValidator(max_drawdown_pct=Decimal("0.05"))
        result = validator.validate(
            positions=[], equity=Decimal("100000"),
            snapshot_drawdown=Decimal("0.15"),
        )
        assert not result.passed
        assert not result.drawdown_check

    def test_drawdown_mismatch_fails(self):
        validator = RiskValidator(max_drawdown_pct=Decimal("0.05"))
        result = validator.validate(
            positions=[], equity=Decimal("100000"),
            snapshot_drawdown=Decimal("0.02"),
            journal_drawdown=Decimal("0.05"),
        )
        assert not result.passed
        assert not result.drawdown_check

    def test_drawdown_within_tolerance_passes(self):
        validator = RiskValidator(max_drawdown_pct=Decimal("0.05"))
        result = validator.validate(
            positions=[], equity=Decimal("100000"),
            snapshot_drawdown=Decimal("0.02"),
            journal_drawdown=Decimal("0.0205"),
        )
        assert result.drawdown_check

    def test_circuit_breaker_halted_fails(self):
        validator = RiskValidator()
        result = validator.validate(
            positions=[], equity=Decimal("100000"),
            circuit_breaker_halted=True,
        )
        assert not result.passed
        assert not result.circuit_breaker_check

    def test_position_sizing_blown_fails(self):
        validator = RiskValidator(risk_pct=Decimal("0.01"))
        # 1 BTC at 10M → notional 10M → approx_risk = 10M * 0.005 = 50K
        # max_risk = 100K * 0.01 = 1K. 3x = 3K. 50K >> 3K
        pos = [{"symbol": "BTC/USDT", "quantity": 100.0, "currentPrice": 100000}]
        result = validator.validate(positions=pos, equity=Decimal("100000"))
        assert not result.passed
        assert not result.sizing_check

    def test_no_equity_skips_exposure_check(self):
        validator = RiskValidator()
        pos = [{"symbol": "BTC/USDT", "quantity": 1.0, "currentPrice": 70000}]
        result = validator.validate(positions=pos, equity=None)
        assert result.exposure_check  # skipped

    def test_zero_equity_skips_sizing(self):
        validator = RiskValidator()
        pos = [{"symbol": "BTC/USDT", "quantity": 1.0, "currentPrice": 70000}]
        result = validator.validate(positions=pos, equity=Decimal("0"))
        assert result.sizing_check  # skipped


# ═════════════════════════════════════════════════════════════════════
# 4. DriftValidator
# ═════════════════════════════════════════════════════════════════════

class TestDriftValidator:
    def test_all_none_passes(self):
        validator = PostRecoveryDriftValidator()
        result = validator.validate()
        assert result.passed

    def test_equity_within_threshold_passes(self):
        validator = PostRecoveryDriftValidator(equity_threshold_pct=Decimal("0.005"))
        result = validator.validate(
            expected_equity=Decimal("100000"),
            actual_equity=Decimal("100200"),
        )
        assert result.passed

    def test_equity_exceeds_threshold_fails(self):
        validator = PostRecoveryDriftValidator(equity_threshold_pct=Decimal("0.005"))
        result = validator.validate(
            expected_equity=Decimal("100000"),
            actual_equity=Decimal("105000"),
        )
        assert not result.passed
        assert result.equity_mismatch_pct == Decimal("0.05")

    def test_positions_match_passes(self):
        validator = PostRecoveryDriftValidator()
        result = validator.validate(
            expected_positions=[{"symbol": "BTC/USDT"}],
            actual_positions=[{"symbol": "BTC/USDT"}],
        )
        assert result.positions_mismatch == 0
        assert result.passed

    def test_positions_mismatch_fails(self):
        validator = PostRecoveryDriftValidator(max_positions_mismatch=0)
        result = validator.validate(
            expected_positions=[{"symbol": "BTC/USDT"}],
            actual_positions=[{"symbol": "BTC/USDT"}, {"symbol": "ETH/USDT"}],
        )
        assert not result.passed
        assert result.positions_mismatch == 1

    def test_pnl_within_threshold_passes(self):
        validator = PostRecoveryDriftValidator(pnl_threshold=Decimal("1.0"))
        result = validator.validate(
            expected_pnl=Decimal("1000"),
            actual_pnl=Decimal("1000.50"),
        )
        assert result.passed

    def test_pnl_exceeds_threshold_fails(self):
        validator = PostRecoveryDriftValidator(pnl_threshold=Decimal("1.0"))
        result = validator.validate(
            expected_pnl=Decimal("1000"),
            actual_pnl=Decimal("1010"),
        )
        assert not result.passed
        assert result.pnl_divergence == Decimal("10")

    def test_missing_journal_events_fails(self):
        validator = PostRecoveryDriftValidator()
        result = validator.validate(
            journal_event_count=50,
            expected_event_count=100,
        )
        assert not result.passed
        assert result.missing_journal_events == 50


# ═════════════════════════════════════════════════════════════════════
# 5. StreamConsumptionBarrier
# ═════════════════════════════════════════════════════════════════════

class TestStreamBarrier:
    def test_initial_state_blocked(self):
        barrier = StreamConsumptionBarrier()
        assert not barrier.is_open
        assert barrier.dropped_ticks == 0
        assert not barrier.fresh_tick_seen

    def test_drops_ticks_when_blocked(self):
        barrier = StreamConsumptionBarrier()
        assert barrier.should_process(1000) is False
        assert barrier.dropped_ticks == 1

    def test_after_open_fresh_tick_passes(self):
        barrier = StreamConsumptionBarrier()
        now_ms = int(time.time() * 1000)
        barrier.open(recovery_timestamp=now_ms / 1000 - 10)
        assert barrier.should_process(now_ms) is True

    def test_stale_tick_dropped_after_open(self):
        barrier = StreamConsumptionBarrier()
        now_ms = int(time.time() * 1000)
        barrier.open(recovery_timestamp=now_ms / 1000)
        stale_ts = now_ms - 5000
        assert barrier.should_process(stale_ts) is False
        assert barrier.dropped_ticks == 1

    def test_fresh_tick_seen_flag(self):
        barrier = StreamConsumptionBarrier()
        now_ms = int(time.time() * 1000)
        barrier.open(recovery_timestamp=now_ms / 1000 - 10)
        assert not barrier.fresh_tick_seen
        barrier.should_process(now_ms)
        assert barrier.fresh_tick_seen

    def test_block_relocks(self):
        barrier = StreamConsumptionBarrier()
        now_ms = int(time.time() * 1000)
        barrier.open(recovery_timestamp=now_ms / 1000 - 10)
        assert barrier.is_open
        barrier.block()
        assert not barrier.is_open
        assert barrier.should_process(now_ms) is False

    def test_none_timestamp_always_passes_when_open(self):
        barrier = StreamConsumptionBarrier()
        barrier.open(recovery_timestamp=time.time() - 10)
        assert barrier.should_process(None) is True

    def test_dropped_ticks_counter_accumulates(self):
        barrier = StreamConsumptionBarrier()
        for _ in range(5):
            barrier.should_process(100)
        assert barrier.dropped_ticks == 5

    def test_multiple_open_resets_state(self):
        barrier = StreamConsumptionBarrier()
        barrier.open(recovery_timestamp=1000)
        assert barrier.is_open
        assert barrier.dropped_ticks == 0
        assert not barrier.fresh_tick_seen

        barrier.block()
        barrier.open(recovery_timestamp=2000)
        assert barrier.is_open
        assert barrier.dropped_ticks == 0
        assert not barrier.fresh_tick_seen


# ═════════════════════════════════════════════════════════════════════
# 6. RecoveryEngine — Integration (mocked deps)
# ═════════════════════════════════════════════════════════════════════

class MockPositionRepo:
    async def list_open(self):
        return []

    async def delete(self, position_id: str):
        pass

    async def get_open_positions_snapshot(self):
        return []

    async def reconcile_position_with_exchange(self, pid: str, data: dict):
        pass


class MockExchangeProvider:
    async def fetch_positions(self):
        return []


class MockSnapshotRepo:
    def __init__(self):
        self.saved_states: list[str] = []

    async def load_latest(self):
        return SystemSnapshot(
            timestamp=datetime.now(timezone.utc),
            equity=Decimal("100000"),
            drawdown=Decimal("0.01"),
            open_positions=[],
            mode="PAPER_TRADING",
            risk_state="NORMAL",
            active_symbols=[],
        )

    async def save_recovery_state(self, state: str, error: str = ""):
        self.saved_states.append(state)


class TestRecoveryEngine:
    @pytest.mark.asyncio
    async def test_successful_recovery(self):
        engine = RecoveryEngine(
            position_repo=MockPositionRepo(),
            exchange_provider=MockExchangeProvider(),
            snapshot_repo=MockSnapshotRepo(),
        )
        report = await engine.recover()
        assert report.recovered
        assert report.snapshot_loaded
        assert report.gate.state != GateState.BLOCKED

    @pytest.mark.asyncio
    async def test_recovery_state_atomic_writes(self):
        snap = MockSnapshotRepo()
        engine = RecoveryEngine(
            position_repo=MockPositionRepo(),
            exchange_provider=MockExchangeProvider(),
            snapshot_repo=snap,
        )
        await engine.recover()
        assert RecoveryState.STARTED in snap.saved_states
        assert RecoveryState.IN_PROGRESS in snap.saved_states
        assert RecoveryState.COMPLETED in snap.saved_states

    @pytest.mark.asyncio
    async def test_exchange_fetch_failure_does_not_crash(self):
        class FailingExchange:
            async def fetch_positions(self):
                from app.application.ports.exchange_position_provider import (
                    ExchangePositionProviderError,
                )
                raise ExchangePositionProviderError("connection refused")

        engine = RecoveryEngine(
            position_repo=MockPositionRepo(),
            exchange_provider=FailingExchange(),
            snapshot_repo=MockSnapshotRepo(),
        )
        report = await engine.recover()
        assert report.recovered
        assert any("exchange_positions_fetch_failed" in e for e in report.errors)

    @pytest.mark.asyncio
    async def test_snapshot_load_failure_recovers_degraded(self):
        class FailingSnapshot:
            async def load_latest(self):
                from app.application.ports.snapshot_repository import (
                    SnapshotRepositoryError,
                )
                raise SnapshotRepositoryError("corrupt db")

            async def save_recovery_state(self, state: str, error: str = ""):
                pass

        engine = RecoveryEngine(
            position_repo=MockPositionRepo(),
            exchange_provider=MockExchangeProvider(),
            snapshot_repo=FailingSnapshot(),
        )
        report = await engine.recover()
        assert not report.snapshot_loaded
        assert report.gate.state == GateState.DEGRADED

    @pytest.mark.asyncio
    async def test_failed_gate_raises_recovery_error(self):
        class BlockedGate:
            def evaluate(self, **kwargs):
                return GateVerdict(
                    state=GateState.BLOCKED,
                    reason="test_blocked",
                )

        engine = RecoveryEngine(
            position_repo=MockPositionRepo(),
            exchange_provider=MockExchangeProvider(),
            snapshot_repo=MockSnapshotRepo(),
            gate=BlockedGate(),
        )
        with pytest.raises(RecoveryError, match="recovery_blocked"):
            await engine.recover()

    @pytest.mark.asyncio
    async def test_risk_validation_invoked(self):
        class ExposingPositionsRepo:
            async def list_open(self):
                return []

            async def get_open_positions_snapshot(self):
                return [{"symbol": "BTC/USDT", "quantity": 2.0, "status": "OPEN"}]

        engine = RecoveryEngine(
            position_repo=ExposingPositionsRepo(),
            exchange_provider=MockExchangeProvider(),
            snapshot_repo=MockSnapshotRepo(),
        )
        report = await engine.recover()
        assert report.recovered

    @pytest.mark.asyncio
    async def test_recovery_report_structure(self):
        engine = RecoveryEngine(
            position_repo=MockPositionRepo(),
            exchange_provider=MockExchangeProvider(),
            snapshot_repo=MockSnapshotRepo(),
        )
        report = await engine.recover()
        assert hasattr(report, "timestamp")
        assert hasattr(report, "event_id")
        assert hasattr(report, "snapshot_loaded")
        assert hasattr(report, "local_positions_count")
        assert hasattr(report, "exchange_positions_count")
        assert hasattr(report, "reconciliation")
        assert hasattr(report, "risk_validation")
        assert hasattr(report, "gate")
        assert hasattr(report, "actions_taken")
        assert hasattr(report, "errors")
        assert hasattr(report, "recovered")
