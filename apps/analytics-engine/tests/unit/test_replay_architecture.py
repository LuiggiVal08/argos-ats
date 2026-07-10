"""Tests for Dual-Lane Replay Architecture components (EMC v1 §7).

Covers:
  - LaneScheduler: event routing (Entity vs Global)
  - ReplayPlanner: snapshot validation, contamination, from_index
  - JoinComposer: pure composition, determinism, no side effects
  - EventSourcedReplay: snapshot-based replay, environment_mode, full pipeline
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from app.domain.replay.event_sourced_replay import EventSourcedReplay
from app.domain.replay.join_composer import SystemState, compose
from app.domain.replay.lane_scheduler import Lane, LaneScheduler
from app.domain.replay.replay_planner import (
    PlannerDecision,
    ReplayPlanner,
    SnapshotMetadata,
)
from app.domain.entities.replay_state import OutputState, RiskState, PositionState
from app.domain.value_objects.domain_event import DomainEvent
from app.infrastructure.repositories.sqlite_event_store import SQLiteEventStore


# ─── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def tmp_db() -> str:
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test_replay_arch.db")
    yield db_path
    try:
        Path(db_path).unlink(missing_ok=True)
    except OSError:
        pass


@pytest.fixture
async def store(tmp_db: str) -> SQLiteEventStore:
    s = SQLiteEventStore(db_path=tmp_db)
    yield s
    s.close()


# ─── LaneScheduler ───────────────────────────────────────────────

class TestLaneScheduler:
    def test_entity_lane_events(self) -> None:
        entity_types = [
            "PositionOpened", "PositionUpdated", "PositionClosed",
            "OrderCreated", "OrderFilled", "OrderCancelled",
            "SignalGenerated", "SignalRejected",
        ]
        for et in entity_types:
            ev = DomainEvent.create(event_type=et, source="Test")
            assert LaneScheduler.route(ev) == Lane.ENTITY, f"{et} should be ENTITY"

    def test_global_lane_events(self) -> None:
        global_types = [
            "RiskEvaluated", "RiskLimitBreached", "RiskStateChanged",
            "SimulationEvaluated", "MonteCarloCompleted",
            "ConfigChanged", "ProjectionCorruptionDetected",
        ]
        for et in global_types:
            ev = DomainEvent.create(event_type=et, source="Test")
            assert LaneScheduler.route(ev) == Lane.GLOBAL, f"{et} should be GLOBAL"

    def test_unknown_event_defaults_to_global(self) -> None:
        ev = DomainEvent.create(event_type="SomeFutureEvent", source="Test")
        assert LaneScheduler.route(ev) == Lane.GLOBAL

    def test_is_entity_event(self) -> None:
        assert LaneScheduler.is_entity_event("PositionOpened") is True
        assert LaneScheduler.is_entity_event("RiskStateChanged") is False

    def test_is_global_event(self) -> None:
        assert LaneScheduler.is_global_event("RiskStateChanged") is True
        assert LaneScheduler.is_global_event("PositionOpened") is False

    def test_no_side_effects(self) -> None:
        ev = DomainEvent.create(event_type="PositionOpened", source="Test",
                                data={"position_id": "p1"})
        result = LaneScheduler.route(ev)
        assert result == Lane.ENTITY
        # Confirm no mutation of event
        assert ev.event_type == "PositionOpened"


# ─── ReplayPlanner ───────────────────────────────────────────────

class TestReplayPlanner:
    def test_no_snapshot_full_replay(self) -> None:
        decision = ReplayPlanner.plan(
            current_version="1.0", max_store_index=50000,
        )
        assert decision.is_full_replay
        assert decision.from_index == 0
        assert "no snapshot available" in decision.reason

    def test_valid_snapshot_from_partial(self) -> None:
        snap = SnapshotMetadata(
            version="1.0", event_index=1000,
            last_event_id="evt_abc", last_event_hash="hash_abc",
            last_event_time="2026-06-18T12:00:00",
        )
        decision = ReplayPlanner.plan(
            current_version="1.0", max_store_index=50000,
            snapshot=snap,
        )
        assert not decision.is_full_replay
        assert decision.from_index == 1001
        assert decision.snapshot == snap
        assert decision.snapshots_invalidated == 0

    def test_snapshot_missing_metadata(self) -> None:
        snap = SnapshotMetadata(
            version="", event_index=1000,
            last_event_id="", last_event_hash="",
            last_event_time="",
        )
        decision = ReplayPlanner.plan(
            current_version="1.0", max_store_index=50000,
            snapshot=snap,
        )
        assert decision.is_full_replay
        assert "missing metadata" in decision.reason

    def test_snapshot_major_version_mismatch(self) -> None:
        snap = SnapshotMetadata(
            version="2.0", event_index=1000,
            last_event_id="evt_abc", last_event_hash="hash_abc",
            last_event_time="2026-06-18T12:00:00",
        )
        decision = ReplayPlanner.plan(
            current_version="1.0", max_store_index=50000,
            snapshot=snap,
        )
        assert decision.is_full_replay
        assert "incompatible" in decision.reason

    def test_snapshot_event_index_exceeds_store(self) -> None:
        snap = SnapshotMetadata(
            version="1.0", event_index=99999,
            last_event_id="evt_abc", last_event_hash="hash_abc",
            last_event_time="2026-06-18T12:00:00",
        )
        decision = ReplayPlanner.plan(
            current_version="1.0", max_store_index=50000,
            snapshot=snap,
        )
        assert decision.is_full_replay
        assert "corruption" in decision.reason

    def test_snapshot_contaminated_by_late_event(self) -> None:
        snap = SnapshotMetadata(
            version="1.0", event_index=1000,
            last_event_id="evt_abc", last_event_hash="hash_abc",
            last_event_time="2026-06-18T12:00:00",
        )
        decision = ReplayPlanner.plan(
            current_version="1.0", max_store_index=50000,
            snapshot=snap,
            late_event_time="2026-06-18T11:30:00",
        )
        assert not decision.is_full_replay
        assert decision.from_index == 1001
        assert decision.snapshots_invalidated == 1
        assert "contaminated" in decision.reason

    def test_late_event_after_snapshot_no_contamination(self) -> None:
        snap = SnapshotMetadata(
            version="1.0", event_index=1000,
            last_event_id="evt_abc", last_event_hash="hash_abc",
            last_event_time="2026-06-18T12:00:00",
        )
        decision = ReplayPlanner.plan(
            current_version="1.0", max_store_index=50000,
            snapshot=snap,
            late_event_time="2026-06-18T13:00:00",
        )
        assert not decision.is_full_replay
        assert decision.snapshots_invalidated == 0
        assert "contaminated" not in decision.reason

    def test_minor_version_is_compatible(self) -> None:
        snap = SnapshotMetadata(
            version="1.1", event_index=1000,
            last_event_id="evt_abc", last_event_hash="hash_abc",
            last_event_time="2026-06-18T12:00:00",
        )
        decision = ReplayPlanner.plan(
            current_version="1.2", max_store_index=50000,
            snapshot=snap,
        )
        assert not decision.is_full_replay


# ─── JoinComposer ────────────────────────────────────────────────

class TestJoinComposer:
    def test_compose_empty_state(self) -> None:
        state = OutputState.empty()
        sys = compose(state, mode="PAPER_TRADING")
        assert isinstance(sys, SystemState)
        assert sys.version == "1.0"
        assert sys.mode == "PAPER_TRADING"
        assert sys.risk.status == "ACTIVE"
        assert len(sys.positions) == 0
        assert len(sys.signals) == 0

    def test_compose_with_position_and_risk(self) -> None:
        state = OutputState(
            positions={"p1": PositionState(
                status="OPEN", position_id="p1", symbol="BTC/USDT",
                side="LONG", entry_price=60000, quantity=0.5,
            )},
            risk=RiskState(
                status="HALT", drawdown_pct=0.08,
                circuit_breaker="TRIP",
            ),
        )
        sys = compose(state, mode="LIVE_SIMULATION")
        assert sys.risk.status == "HALT"
        assert "p1" in sys.positions
        assert sys.positions["p1"].symbol == "BTC/USDT"
        assert sys.positions["p1"].side == "LONG"

    def test_deterministic(self) -> None:
        state = OutputState.empty()
        ts = "2026-06-24T12:00:00+00:00"
        sys1 = compose(state, mode="PAPER_TRADING", generated_at=ts)
        sys2 = compose(state, mode="PAPER_TRADING", generated_at=ts)
        assert sys1.to_dict() == sys2.to_dict()
        assert sys1.to_dict()["generated_at"] == ts

    def test_to_dict_contains_expected_keys(self) -> None:
        state = OutputState.empty()
        sys = compose(state, mode="TEST")
        d = sys.to_dict()
        assert "version" in d
        assert "risk" in d
        assert "positions" in d
        assert "signals" in d
        assert "n_positions" in d
        assert "n_signals" in d
        assert "mode" in d
        assert d["mode"] == "TEST"

    def test_no_side_effects_on_input(self) -> None:
        state = OutputState.empty()
        original_dict = state.to_dict()
        compose(state, mode="TEST")
        assert state.to_dict() == original_dict


# ─── EventSourcedReplay integration ─────────────────────────────

class TestEventSourcedReplayIntegration:
    async def test_replay_with_environment_mode(
        self, store: SQLiteEventStore,
    ) -> None:
        ev = DomainEvent.create(
            event_type="PositionOpened", source="ExecutionCore",
            data={"position_id": "p1", "symbol": "BTC", "side": "LONG",
                  "entry_price": 60000, "quantity": 0.5},
        )
        await store.write(ev)

        engine = EventSourcedReplay()
        result = await engine.replay(store, mode="STRICT",
                                     environment_mode="PAPER_TRADING")
        assert result.system_state is not None
        assert result.system_state.mode == "PAPER_TRADING"
        assert result.system_state.to_dict()["mode"] == "PAPER_TRADING"

    async def test_replay_with_snapshot_skip(
        self, store: SQLiteEventStore,
    ) -> None:
        # Write 2 events
        ev1 = DomainEvent.create(
            event_type="PositionOpened", source="ExecutionCore",
            data={"position_id": "p1", "symbol": "BTC", "side": "LONG",
                  "entry_price": 60000, "quantity": 0.5},
        )
        ev2 = DomainEvent.create(
            event_type="PositionClosed", source="ExecutionCore",
            data={"position_id": "p1", "close_price": 61000, "pnl": 500,
                  "close_reason": "take_profit", "hold_bars": 5},
        )
        await store.write(ev1)
        idx2 = await store.max_event_index()
        await store.write(ev2)

        # Snapshot at index idx2-1 (only first event)
        snap = SnapshotMetadata(
            version="1.0", event_index=idx2 - 1,
            last_event_id=ev1.event_id,
            last_event_hash=f"hash_{ev1.event_id}",
            last_event_time=ev1.timestamp,
        )

        engine = EventSourcedReplay()
        result = await engine.replay(store, mode="STRICT", snapshot=snap)
        assert result.planner_decision is not None
        assert result.planner_decision.from_index == idx2
        assert not result.planner_decision.is_full_replay

    async def test_replay_full_without_snapshot(
        self, store: SQLiteEventStore,
    ) -> None:
        ev = DomainEvent.create(
            event_type="PositionOpened", source="ExecutionCore",
            data={"position_id": "p1", "symbol": "BTC", "side": "LONG",
                  "entry_price": 60000, "quantity": 0.5},
        )
        assert await store.write(ev) is True

        engine = EventSourcedReplay()
        result = await engine.replay(store, mode="STRICT")
        assert result.planner_decision is not None
        assert result.planner_decision.is_full_replay
        assert result.system_state is not None
        assert "p1" in result.system_state.positions

    async def test_replay_result_to_dict_with_system_state(
        self, store: SQLiteEventStore,
    ) -> None:
        await store.write(DomainEvent.create(
            event_type="PositionOpened", source="ExecutionCore",
            data={"position_id": "p1", "symbol": "BTC", "side": "LONG",
                  "entry_price": 60000, "quantity": 0.5},
        ))
        engine = EventSourcedReplay()
        result = await engine.replay(store, mode="RECOVERY",
                                     environment_mode="BACKTESTING")
        d = result.to_dict()
        assert d["success"] is True
        assert "state" in d
        assert "system_state" in d
        assert d["system_state"]["mode"] == "BACKTESTING"
        assert d["system_state"]["n_positions"] == 1


# ─── Replay with contamination detection ────────────────────────

class TestReplayWithContamination:
    async def test_no_contamination_without_late_event(
        self, store: SQLiteEventStore,
    ) -> None:
        ev = DomainEvent.create(
            event_type="RiskStateChanged", source="RiskPolicyEngine",
            data={"old_status": "ACTIVE", "new_status": "HALT",
                  "reason": "drawdown_breach"},
        )
        await store.write(ev)
        snap = SnapshotMetadata(
            version="1.0", event_index=await store.max_event_index(),
            last_event_id=ev.event_id, last_event_hash=f"hash_{ev.event_id}",
            last_event_time=ev.timestamp,
        )
        engine = EventSourcedReplay()
        result = await engine.replay(store, mode="STRICT", snapshot=snap)
        assert result.planner_decision is not None
        assert result.planner_decision.snapshots_invalidated == 0

    async def test_contamination_with_late_event(
        self, store: SQLiteEventStore,
    ) -> None:
        ev = DomainEvent.create(
            event_type="RiskStateChanged", source="RiskPolicyEngine",
            data={"old_status": "ACTIVE", "new_status": "HALT"},
        )
        await store.write(ev)
        max_idx = await store.max_event_index()
        snap = SnapshotMetadata(
            version="1.0", event_index=max_idx,
            last_event_id=ev.event_id, last_event_hash=f"hash_{ev.event_id}",
            last_event_time=ev.timestamp,
        )
        engine = EventSourcedReplay()
        result = await engine.replay(
            store, mode="STRICT", snapshot=snap,
            late_event_time="2020-01-01T00:00:00",
        )
        assert result.planner_decision is not None
        assert result.planner_decision.snapshots_invalidated == 1
        assert "contaminated" in result.planner_decision.reason
