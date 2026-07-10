"""Tests for Replay Engine F2: state machines, reducers, invariants, replay.

Covers:
  - Position state machine (NONE → OPEN → CLOSED)
  - Risk state machine (ACTIVE → DEGRADED → HALT → RECOVERY_PENDING)
  - Signal last-value tracking
  - Invariants (no_double_close, quantity_non_negative, pnl_derivable, etc.)
  - ReplayEngine STRICT mode (abort on invariant break)
  - ReplayEngine RECOVERY mode (report violations, continue)
  - Deterministic sort key (partition + precedence + bucket + id)
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from app.domain.entities.invariants import (
    InvariantError,
    check_invariant,
    fail_fast_on_invariant_break,
)
from app.domain.entities.reducers import reduce
from app.domain.entities.replay_state import (
    OutputState,
    PositionState,
    RiskState,
    SignalState,
    _entity_id,
    _stream_priority,
)
from app.domain.replay.event_sourced_replay import EventSourcedReplay, ReplayResult
from app.domain.value_objects.domain_event import DomainEvent
from app.infrastructure.repositories.sqlite_event_store import SQLiteEventStore


# ─── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def tmp_db() -> str:
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test_replay.db")
    yield db_path
    try:
        Path(db_path).unlink(missing_ok=True)
    except OSError:
        pass
    try:
        Path(tmpdir).rmdir()
    except OSError:
        pass


@pytest.fixture
def store(tmp_db: str) -> SQLiteEventStore:
    return SQLiteEventStore(db_path=tmp_db)


# ─── Position State Machine Tests ─────────────────────────────────


class TestPositionStateMachine:
    def test_initial_state_is_none(self) -> None:
        s = OutputState.empty()
        assert len(s.positions) == 0

    def test_position_opened(self) -> None:
        ev = DomainEvent.create(
            event_type="PositionOpened",
            source="ExecutionCore",
            data={
                "position_id": "pos-1",
                "symbol": "BTC/USDT",
                "side": "LONG",
                "entry_price": 50000.0,
                "quantity": 0.1,
                "sl_price": 49500.0,
            },
        )
        s = reduce(ev, OutputState.empty())
        pos = s.positions["pos-1"]
        assert pos.status == "OPEN"
        assert pos.symbol == "BTC/USDT"
        assert pos.side == "LONG"
        assert pos.entry_price == 50000.0
        assert pos.quantity == 0.1

    def test_position_opened_then_closed(self) -> None:
        s = OutputState.empty()
        s = reduce(DomainEvent.create(
            event_type="PositionOpened",
            source="ExecutionCore",
            data={
                "position_id": "pos-1",
                "symbol": "BTC/USDT", "side": "LONG",
                "entry_price": 50000.0, "quantity": 0.1,
            },
        ), s)
        s = reduce(DomainEvent.create(
            event_type="PositionClosed",
            source="ExecutionCore",
            data={
                "position_id": "pos-1",
                "close_price": 51000.0,
                "pnl": 100.0,
                "close_reason": "take_profit",
                "hold_bars": 5,
            },
        ), s)

        pos = s.positions["pos-1"]
        assert pos.status == "CLOSED"
        assert pos.pnl == 100.0
        assert pos.close_price == 51000.0

    def test_position_update_sl(self) -> None:
        s = OutputState.empty()
        s = reduce(DomainEvent.create(
            event_type="PositionOpened",
            source="ExecutionCore",
            data={
                "position_id": "pos-1",
                "symbol": "BTC/USDT", "side": "LONG",
                "entry_price": 50000.0, "quantity": 0.1,
                "sl_price": 49500.0,
            },
        ), s)
        s = reduce(DomainEvent.create(
            event_type="PositionUpdated",
            source="ExecutionCore",
            data={
                "position_id": "pos-1",
                "sl_price": 49800.0,
                "reason": "trailing_sl",
            },
        ), s)

        pos = s.positions["pos-1"]
        assert pos.status == "OPEN"
        assert pos.sl_price == 49800.0  # Updated
        assert pos.tp_price is None  # Unchanged

    def test_close_nonexistent_position_is_noop(self) -> None:
        s = OutputState.empty()
        s = reduce(DomainEvent.create(
            event_type="PositionClosed",
            source="ExecutionCore",
            data={"position_id": "ghost", "close_price": 100, "pnl": 0},
        ), s)
        assert len(s.positions) == 0

    def test_update_nonexistent_position_is_noop(self) -> None:
        s = OutputState.empty()
        s = reduce(DomainEvent.create(
            event_type="PositionUpdated",
            source="ExecutionCore",
            data={"position_id": "ghost", "sl_price": 100},
        ), s)
        assert len(s.positions) == 0


# ─── Risk State Machine Tests ─────────────────────────────────────


class TestRiskStateMachine:
    def test_initial_risk_is_active(self) -> None:
        s = OutputState.empty()
        assert s.risk.status == "ACTIVE"
        assert s.risk.circuit_breaker == "NORMAL"

    def test_risk_state_changed_to_degraded(self) -> None:
        s = OutputState.empty()
        s = reduce(DomainEvent.create(
            event_type="RiskStateChanged",
            source="RiskPolicyEngine",
            data={
                "old_status": "ACTIVE",
                "new_status": "DEGRADED",
                "reason": "p_failure_exceeded",
                "trigger": "SimulationEvaluated",
            },
        ), s)
        assert s.risk.status == "DEGRADED"

    def test_risk_state_changed_to_halt(self) -> None:
        s = OutputState.empty()
        s = reduce(DomainEvent.create(
            event_type="RiskStateChanged",
            source="RiskPolicyEngine",
            data={
                "old_status": "ACTIVE",
                "new_status": "HALT",
                "reason": "drawdown_breach",
            },
        ), s)
        assert s.risk.status == "HALT"
        assert s.risk.circuit_breaker == "TRIP"

    def test_risk_evaluated_updates_metrics(self) -> None:
        s = OutputState.empty()
        s = reduce(DomainEvent.create(
            event_type="RiskEvaluated",
            source="RiskPolicyEngine",
            data={
                "drawdown_pct": 0.023,
                "daily_pnl": -124.50,
                "starting_balance": 86578.04,
                "current_balance": 86453.54,
                "circuit_breaker": "WARN",
            },
        ), s)
        assert s.risk.drawdown_pct == 0.023
        assert s.risk.daily_pnl == -124.50
        assert s.risk.circuit_breaker == "WARN"
        assert s.risk.status == "ACTIVE"  # Status unchanged

    def test_risk_state_changed_full_lifecycle(self) -> None:
        s = OutputState.empty()
        # ACTIVE → DEGRADED
        s = reduce(DomainEvent.create(
            event_type="RiskStateChanged", source="RiskPolicyEngine",
            data={"old_status": "ACTIVE", "new_status": "DEGRADED", "reason": "p_failure_high"},
        ), s)
        assert s.risk.status == "DEGRADED"
        # DEGRADED → HALT
        s = reduce(DomainEvent.create(
            event_type="RiskStateChanged", source="RiskPolicyEngine",
            data={"old_status": "DEGRADED", "new_status": "HALT", "reason": "drawdown_breach"},
        ), s)
        assert s.risk.status == "HALT"
        # HALT → RECOVERY_PENDING
        s = reduce(DomainEvent.create(
            event_type="RiskStateChanged", source="RiskPolicyEngine",
            data={"old_status": "HALT", "new_status": "RECOVERY_PENDING", "reason": "recovery_timer"},
        ), s)
        assert s.risk.status == "RECOVERY_PENDING"
        # RECOVERY_PENDING → ACTIVE
        s = reduce(DomainEvent.create(
            event_type="RiskStateChanged", source="RiskPolicyEngine",
            data={"old_status": "RECOVERY_PENDING", "new_status": "ACTIVE", "reason": "operator_resume"},
        ), s)
        assert s.risk.status == "ACTIVE"


# ─── Signal Reducer Tests ─────────────────────────────────────────


class TestSignalReducer:
    def test_signal_generated(self) -> None:
        s = OutputState.empty()
        s = reduce(DomainEvent.create(
            event_type="SignalGenerated",
            source="SignalEngine",
            data={
                "symbol": "BTC/USDT",
                "signal_id": "sig-123",
                "side": "BUY",
                "confidence": 0.9447,
                "model_version": "1.0.0",
                "regime": "TRENDING",
            },
        ), s)
        sig = s.signals["BTC/USDT"]
        assert sig.side == "BUY"
        assert sig.confidence == 0.9447
        assert not sig.rejected

    def test_signal_generated_overwrites_previous(self) -> None:
        s = OutputState.empty()
        s = reduce(DomainEvent.create(
            event_type="SignalGenerated", source="SignalEngine",
            data={"symbol": "BTC/USDT", "side": "BUY", "confidence": 0.8},
        ), s)
        assert s.signals["BTC/USDT"].side == "BUY"

        s = reduce(DomainEvent.create(
            event_type="SignalGenerated", source="SignalEngine",
            data={"symbol": "BTC/USDT", "side": "SELL", "confidence": 0.9},
        ), s)
        assert s.signals["BTC/USDT"].side == "SELL"
        assert s.signals["BTC/USDT"].confidence == 0.9

    def test_signal_rejected(self) -> None:
        s = OutputState.empty()
        s = reduce(DomainEvent.create(
            event_type="SignalRejected",
            source="ExecutionGuard",
            data={
                "signal_id": "sig-456",
                "reason": "low_confidence",
                "confidence": 0.61,
                "threshold": 0.75,
            },
        ), s)
        sig = s.signals["sig-456"]
        assert sig.rejected
        assert sig.reject_reason == "low_confidence"


# ─── Invariant Tests ──────────────────────────────────────────────


class TestInvariants:
    def test_no_violations_on_empty_state(self) -> None:
        vs = check_invariant(OutputState.empty())
        assert vs == []

    def test_no_zero_entry_price(self) -> None:
        s = OutputState.empty()
        s = reduce(DomainEvent.create(
            event_type="PositionOpened", source="ExecutionCore",
            data={"position_id": "p1", "symbol": "BTC", "side": "LONG",
                  "entry_price": 100, "quantity": 1},
        ), s)
        vs = check_invariant(s)
        violations = [v for v in vs if v[0] == "no_zero_entry_price"]
        assert len(violations) == 0

    def test_position_status_consistent(self) -> None:
        s = OutputState.empty()
        s = reduce(DomainEvent.create(
            event_type="PositionOpened", source="ExecutionCore",
            data={"position_id": "p1", "symbol": "BTC", "side": "LONG",
                  "entry_price": 100, "quantity": 1},
        ), s)
        s = reduce(DomainEvent.create(
            event_type="PositionClosed", source="ExecutionCore",
            data={"position_id": "p1", "close_price": 110, "pnl": 10},
        ), s)
        vs = check_invariant(s)
        violations = [v for v in vs if v[0] == "position_status_consistent"]
        assert len(violations) == 0

    def test_quantity_non_negative_passes(self) -> None:
        s = OutputState.empty()
        s = reduce(DomainEvent.create(
            event_type="PositionOpened", source="ExecutionCore",
            data={"position_id": "p1", "symbol": "BTC", "side": "LONG",
                  "entry_price": 100, "quantity": 1},
        ), s)
        vs = check_invariant(s)
        violations = [v for v in vs if v[0] == "quantity_non_negative"]
        assert len(violations) == 0

    def test_pnl_derivable(self) -> None:
        s = OutputState.empty()
        s = reduce(DomainEvent.create(
            event_type="PositionOpened", source="ExecutionCore",
            data={"position_id": "p1", "symbol": "BTC", "side": "LONG",
                  "entry_price": 100, "quantity": 1},
        ), s)
        s = reduce(DomainEvent.create(
            event_type="PositionClosed", source="ExecutionCore",
            data={"position_id": "p1", "close_price": 110, "pnl": 10},
        ), s)
        vs = check_invariant(s)
        violations = [v for v in vs if v[0] == "pnl_derivable"]
        assert len(violations) == 0  # 110-100=10, matches pnl=10

    def test_pnl_drift_detected(self) -> None:
        s = OutputState.empty()
        s = reduce(DomainEvent.create(
            event_type="PositionOpened", source="ExecutionCore",
            data={"position_id": "p1", "symbol": "BTC", "side": "LONG",
                  "entry_price": 100, "quantity": 1},
        ), s)
        s = reduce(DomainEvent.create(
            event_type="PositionClosed", source="ExecutionCore",
            data={"position_id": "p1", "close_price": 110, "pnl": 500},
        ), s)
        vs = check_invariant(s)
        violations = [v for v in vs if v[0] == "pnl_derivable"]
        assert len(violations) == 1  # Expected 10, got 500

    def test_risk_status_valid(self) -> None:
        s = OutputState.empty()
        # ACTIVE is valid
        vs = check_invariant(s)
        assert len(vs) == 0

    def test_signal_confidence_range(self) -> None:
        s = OutputState.empty()
        s = reduce(DomainEvent.create(
            event_type="SignalGenerated", source="SignalEngine",
            data={"symbol": "BTC", "side": "BUY", "confidence": 1.5},
        ), s)
        vs = check_invariant(s)
        violations = [v for v in vs if v[0] == "signal_confidence_in_range"]
        assert len(violations) == 1

    def test_p_failure_rolling_valid(self) -> None:
        s = OutputState.empty()
        # Simulate a SimulationEvaluated event that adds p_failure to rolling
        s = reduce(DomainEvent.create(
            event_type="SimulationEvaluated",
            source="SimulationEngine",
            data={"symbol": "BTC/USDT", "p_failure": 1.0, "bar_idx": 999},
        ), s)
        vs = check_invariant(s)
        violations = [v for v in vs if v[0] == "p_failure_rolling_valid"]
        assert len(violations) == 0

    def test_fail_fast_raises_on_violation(self) -> None:
        s = OutputState.empty()
        s = reduce(DomainEvent.create(
            event_type="PositionOpened", source="ExecutionCore",
            data={"position_id": "p1", "symbol": "BTC", "side": "LONG",
                  "entry_price": 100, "quantity": 1},
        ), s)
        # Force a bad state by closing with mismatched pnl
        s = reduce(DomainEvent.create(
            event_type="PositionClosed", source="ExecutionCore",
            data={"position_id": "p1", "close_price": 110, "pnl": 9999},
        ), s)
        with pytest.raises(InvariantError):
            fail_fast_on_invariant_break(s)


# ─── Entity ID + Sort Key Tests ───────────────────────────────────


class TestEntityId:
    def test_entity_id_position(self) -> None:
        ev = DomainEvent.create(
            event_type="PositionOpened", source="ExecutionCore",
            data={"position_id": "pos-abc", "symbol": "BTC/USDT"},
        )
        assert _entity_id(ev) == "pos-abc"

    def test_entity_id_signal(self) -> None:
        ev = DomainEvent.create(
            event_type="SignalGenerated", source="SignalEngine",
            data={"symbol": "BTC/USDT"},
        )
        assert _entity_id(ev) == "BTC/USDT"

    def test_entity_id_risk(self) -> None:
        ev = DomainEvent.create(
            event_type="RiskStateChanged", source="RiskPolicyEngine",
            data={"new_status": "HALT"},
        )
        assert _entity_id(ev) == "system"


class TestStreamPriority:
    def test_risk_is_zero(self) -> None:
        assert _stream_priority("RiskEvaluated") == 0
        assert _stream_priority("RiskStateChanged") == 0

    def test_signal_is_one(self) -> None:
        assert _stream_priority("SignalGenerated") == 1

    def test_execution_is_two(self) -> None:
        assert _stream_priority("OrderCreated") == 2
        assert _stream_priority("OrderFilled") == 2

    def test_position_is_three(self) -> None:
        assert _stream_priority("PositionOpened") == 3
        assert _stream_priority("PositionClosed") == 3

    def test_config_is_four(self) -> None:
        assert _stream_priority("ConfigChanged") == 4

    def test_unknown_is_99(self) -> None:
        assert _stream_priority("UnknownEvent") == 99


# ─── ReplayEngine Integration Tests ───────────────────────────────


class TestEventSourcedReplay:
    async def test_empty_store(self, store: SQLiteEventStore) -> None:
        engine = EventSourcedReplay()
        result = await engine.replay(store)
        assert result.success
        assert result.total_events == 0
        assert result.state.risk.status == "ACTIVE"

    async def test_single_position_lifecycle(self, store: SQLiteEventStore) -> None:
        open_ev = DomainEvent.create(
            event_type="PositionOpened", source="ExecutionCore",
            data={"position_id": "p1", "symbol": "BTC/USDT", "side": "LONG",
                  "entry_price": 50000, "quantity": 0.1},
        )
        close_ev = DomainEvent.create(
            event_type="PositionClosed", source="ExecutionCore",
            data={"position_id": "p1", "close_price": 51000, "pnl": 100,
                  "close_reason": "tp"},
        )
        await store.write(open_ev)
        await store.write(close_ev)

        engine = EventSourcedReplay()
        result = await engine.replay(store, mode="STRICT")
        assert result.success
        assert result.total_events == 2
        assert result.processed_events == 2
        assert result.state.positions["p1"].status == "CLOSED"

    async def test_full_lifecycle_risk(self, store: SQLiteEventStore) -> None:
        events = [
            DomainEvent.create(
                event_type="RiskStateChanged", source="RiskPolicyEngine",
                data={"old_status": "ACTIVE", "new_status": "DEGRADED",
                      "reason": "p_failure_high"},
            ),
            DomainEvent.create(
                event_type="RiskStateChanged", source="RiskPolicyEngine",
                data={"old_status": "DEGRADED", "new_status": "HALT",
                      "reason": "drawdown_breach"},
            ),
        ]
        for ev in events:
            await store.write(ev)

        engine = EventSourcedReplay()
        result = await engine.replay(store, mode="STRICT")
        assert result.success
        assert result.state.risk.status == "HALT"

    async def test_strinct_mode_aborts_on_invariant_break(
        self, store: SQLiteEventStore,
    ) -> None:
        await store.write(DomainEvent.create(
            event_type="PositionOpened", source="ExecutionCore",
            data={"position_id": "p1", "symbol": "BTC", "side": "LONG",
                  "entry_price": 100, "quantity": 1},
        ))
        await store.write(DomainEvent.create(
            event_type="PositionClosed", source="ExecutionCore",
            data={"position_id": "p1", "close_price": 110, "pnl": 500},
        ))

        engine = EventSourcedReplay()
        with pytest.raises(InvariantError):
            await engine.replay(store, mode="STRICT")

    async def test_recovery_mode_continues_on_invariant_break(
        self, store: SQLiteEventStore,
    ) -> None:
        await store.write(DomainEvent.create(
            event_type="PositionOpened", source="ExecutionCore",
            data={"position_id": "p1", "symbol": "BTC", "side": "LONG",
                  "entry_price": 100, "quantity": 1},
        ))
        await store.write(DomainEvent.create(
            event_type="PositionClosed", source="ExecutionCore",
            data={"position_id": "p1", "close_price": 110, "pnl": 500},
        ))

        engine = EventSourcedReplay()
        result = await engine.replay(store, mode="RECOVERY")
        assert result.success
        assert len(result.violations) > 0
        violation_names = [v[0] for v in result.violations]
        assert "pnl_derivable" in violation_names
        assert result.state.positions["p1"].status == "CLOSED"

    async def test_multiple_partitions(self, store: SQLiteEventStore) -> None:
        await store.write(DomainEvent.create(
            event_type="PositionOpened", source="ExecutionCore",
            data={"position_id": "p1", "symbol": "BTC", "side": "LONG",
                  "entry_price": 100, "quantity": 1},
        ))
        await store.write(DomainEvent.create(
            event_type="PositionOpened", source="ExecutionCore",
            data={"position_id": "p2", "symbol": "ETH", "side": "SHORT",
                  "entry_price": 2000, "quantity": 5},
        ))
        await store.write(DomainEvent.create(
            event_type="PositionClosed", source="ExecutionCore",
            data={"position_id": "p1", "close_price": 110, "pnl": 10},
        ))

        engine = EventSourcedReplay()
        result = await engine.replay(store, mode="STRICT")
        assert result.success
        assert result.n_partitions == 2
        assert result.state.positions["p1"].status == "CLOSED"
        assert result.state.positions["p2"].status == "OPEN"

    async def test_replay_with_signals_and_risk(
        self, store: SQLiteEventStore,
    ) -> None:
        await store.write(DomainEvent.create(
            event_type="RiskEvaluated", source="RiskPolicyEngine",
            data={"drawdown_pct": 0.01, "daily_pnl": -50,
                  "starting_balance": 100000, "current_balance": 99950},
        ))
        await store.write(DomainEvent.create(
            event_type="SignalGenerated", source="SignalEngine",
            data={"symbol": "BTC/USDT", "side": "BUY", "confidence": 0.85,
                  "model_version": "v2", "regime": "TRENDING"},
        ))
        await store.write(DomainEvent.create(
            event_type="PositionOpened", source="ExecutionCore",
            data={"position_id": "p1", "symbol": "BTC/USDT", "side": "LONG",
                  "entry_price": 50000, "quantity": 0.1},
        ))

        engine = EventSourcedReplay()
        result = await engine.replay(store, mode="STRICT")
        assert result.success
        assert result.state.risk.drawdown_pct == 0.01
        assert result.state.risk.status == "ACTIVE"
        assert result.state.signals["BTC/USDT"].side == "BUY"
        assert result.state.signals["BTC/USDT"].confidence == 0.85
        assert result.state.positions["p1"].status == "OPEN"

    async def test_deterministic_replay_idempotent(
        self, store: SQLiteEventStore,
    ) -> None:
        await store.write(DomainEvent.create(
            event_type="PositionOpened", source="ExecutionCore",
            data={"position_id": "p1", "symbol": "BTC", "side": "LONG",
                  "entry_price": 100, "quantity": 1},
        ))
        engine = EventSourcedReplay()
        r1 = await engine.replay(store)
        r2 = await engine.replay(store)
        assert r1.state.positions["p1"].entry_price == r2.state.positions["p1"].entry_price
        assert r1.state.positions["p1"].status == r2.state.positions["p1"].status

    async def test_invalid_mode_raises(self, store: SQLiteEventStore) -> None:
        engine = EventSourcedReplay()
        with pytest.raises(ValueError, match="Unknown replay mode"):
            await engine.replay(store, mode="INVALID")

    async def test_known_event_type_reduced_correctly(
        self, store: SQLiteEventStore,
    ) -> None:
        await store.write(DomainEvent.create(
            event_type="PositionOpened", source="ExecutionCore",
            data={"position_id": "p1", "symbol": "BTC", "side": "LONG",
                  "entry_price": 100, "quantity": 1},
        ))
        engine = EventSourcedReplay()
        result = await engine.replay(store, mode="STRICT")
        assert result.processed_events == 1
        assert result.state.positions["p1"].status == "OPEN"

    async def test_replay_result_to_dict(
        self, store: SQLiteEventStore,
    ) -> None:
        await store.write(DomainEvent.create(
            event_type="PositionOpened", source="ExecutionCore",
            data={"position_id": "p1", "symbol": "BTC", "side": "LONG",
                  "entry_price": 100, "quantity": 1},
        ))
        engine = EventSourcedReplay()
        result = await engine.replay(store, mode="RECOVERY")
        d = result.to_dict()
        assert d["success"] is True
        assert d["total_events"] == 1
        assert d["state"]["n_positions"] == 1
