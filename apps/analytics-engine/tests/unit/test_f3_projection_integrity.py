"""Tests for F3 — Projection Integrity Layer (EMC v1 §10).

Covers:
  - Execution Trace (TraceEntry, reducer_id, transition_id, make_trace_entry)
  - Projection Fingerprint (build, determinism)
  - Canonical JSON (serialization, float handling, field exclusion)
  - Projection Hash (determinism, sensitivity to changes)
  - Verification Engine (one-shot verify, full equivalence)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

import os
import tempfile
from pathlib import Path
from types import MappingProxyType

from app.domain.entities.replay_state import (
    OutputState,
    PositionState,
    RiskState,
)
from app.domain.replay.event_sourced_replay import EventSourcedReplay
from app.domain.replay.execution_trace import (
    TraceEntry,
    compute_transition_id,
    invariant_pattern_for,
    make_trace_entry,
    reducer_id_for,
)
from app.domain.replay.projection_hasher import (
    canonical_json,
    compute_projection_hash,
)
from app.domain.replay.projection_metadata import (
    DeploymentFingerprint,
    StructuralFingerprint,
    build_deployment_fingerprint,
    build_fingerprints,
    build_structural_fingerprint,
)
from app.domain.replay.verification_engine import (
    VerificationEngine,
    VerificationReport,
)
from app.domain.replay.projection_hasher import compute_projection_hash
from app.domain.value_objects.domain_event import DomainEvent
from app.infrastructure.repositories.sqlite_event_store import SQLiteEventStore


# ─── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def tmp_db() -> str:
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test_f3.db")
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


# ═══════════════════════════════════════════════════════════════════
# Execution Trace
# ═══════════════════════════════════════════════════════════════════

class TestReducerId:
    def test_known_event_types(self) -> None:
        cases: dict[str, str] = {
            "PositionOpened": "PositionReducer/v1",
            "PositionUpdated": "PositionReducer/v1",
            "PositionClosed": "PositionReducer/v1",
            "RiskEvaluated": "RiskReducer/v1",
            "RiskLimitBreached": "RiskReducer/v1",
            "RiskStateChanged": "RiskReducer/v1",
            "SignalGenerated": "SignalReducer/v1",
            "SignalRejected": "SignalReducer/v1",
            "SimulationEvaluated": "SimulationReducer/v1",
            "MonteCarloCompleted": "SimulationReducer/v1",
            "ConfigChanged": "ConfigReducer/v1",
        }
        for event_type, expected in cases.items():
            assert reducer_id_for(event_type) == expected

    def test_unknown_event_type(self) -> None:
        assert reducer_id_for("UnknownFutureEvent") == "UnknownReducer/v1"


class TestInvariantPattern:
    def test_known_event_types(self) -> None:
        cases: dict[str, str] = {
            "PositionOpened": "POSITION_STATE_MACHINE_v1",
            "PositionClosed": "POSITION_STATE_MACHINE_v1",
            "RiskStateChanged": "RISK_STATE_MACHINE_v1",
            "SignalGenerated": "SIGNAL_LAST_VALUE_v1",
            "SimulationEvaluated": "SIMULATION_REDUCER_v1",
        }
        for event_type, expected in cases.items():
            assert invariant_pattern_for(event_type) == expected

    def test_unknown_event_type(self) -> None:
        assert invariant_pattern_for("Unknown") == "UNKNOWN_INVARIANT_v1"


class TestTransitionId:
    def test_deterministic(self) -> None:
        tid1 = compute_transition_id("PositionOpened", "PositionReducer/v1", "POSITION_STATE_MACHINE_v1")
        tid2 = compute_transition_id("PositionOpened", "PositionReducer/v1", "POSITION_STATE_MACHINE_v1")
        assert tid1 == tid2

    def test_changes_on_event_type(self) -> None:
        tid1 = compute_transition_id("PositionOpened", "PositionReducer/v1", "POSITION_STATE_MACHINE_v1")
        tid2 = compute_transition_id("PositionClosed", "PositionReducer/v1", "POSITION_STATE_MACHINE_v1")
        assert tid1 != tid2

    def test_changes_on_reducer_id(self) -> None:
        tid1 = compute_transition_id("PositionOpened", "PositionReducer/v1", "POSITION_STATE_MACHINE_v1")
        tid2 = compute_transition_id("PositionOpened", "PositionReducer/v2", "POSITION_STATE_MACHINE_v1")
        assert tid1 != tid2

    def test_changes_on_invariant_pattern(self) -> None:
        tid1 = compute_transition_id("PositionOpened", "PositionReducer/v1", "POSITION_STATE_MACHINE_v1")
        tid2 = compute_transition_id("PositionOpened", "PositionReducer/v1", "POSITION_STATE_MACHINE_v2")
        assert tid1 != tid2

    def test_output_length(self) -> None:
        tid = compute_transition_id("PositionOpened", "PositionReducer/v1", "POSITION_STATE_MACHINE_v1")
        assert len(tid) == 32
        assert all(c in "0123456789abcdef" for c in tid)


class TestMakeTraceEntry:
    def test_position_opened(self) -> None:
        ev = DomainEvent.create(
            event_type="PositionOpened",
            source="ExecutionCore",
            data={"position_id": "p1", "symbol": "BTC/USDT"},
        )
        entry = make_trace_entry(ev)
        assert isinstance(entry, TraceEntry)
        assert entry.event_id == ev.event_id
        assert entry.lane == "ENTITY"
        assert entry.reducer_id == "PositionReducer/v1"
        assert entry.transition_id is not None
        assert entry.entity_id == "p1"
        assert "|" in entry.ordering_key

    def test_risk_state_changed(self) -> None:
        ev = DomainEvent.create(
            event_type="RiskStateChanged",
            source="RiskPolicyEngine",
            data={"old_status": "ACTIVE", "new_status": "HALT"},
        )
        entry = make_trace_entry(ev)
        assert entry.lane == "GLOBAL"
        assert entry.reducer_id == "RiskReducer/v1"
        # entity_id for system/risk events is "system" (from _entity_id)
        assert entry.entity_id == "system"

    def test_unknown_event_type_still_produces_trace(self) -> None:
        ev = DomainEvent.create(
            event_type="SomeFutureEvent",
            source="Test",
            data={"value": 42},
        )
        entry = make_trace_entry(ev)
        assert entry.reducer_id == "UnknownReducer/v1"
        assert entry.transition_id is not None
        # Unknown events default to GLOBAL lane
        assert entry.lane == "GLOBAL"

    def test_deterministic(self) -> None:
        ev = DomainEvent.create(
            event_type="PositionOpened",
            source="ExecutionCore",
            data={"position_id": "p1"},
        )
        entry1 = make_trace_entry(ev)
        entry2 = make_trace_entry(ev)
        assert entry1 == entry2


# ═══════════════════════════════════════════════════════════════════
# Projection Fingerprint (two-level)
# ═══════════════════════════════════════════════════════════════════

class TestStructuralFingerprint:
    def test_build(self) -> None:
        sfp = build_structural_fingerprint()
        assert isinstance(sfp, StructuralFingerprint)
        assert len(sfp.lane_scheduler_routing_hash) == 32
        assert len(sfp.reducer_dispatch_hash) == 32
        assert len(sfp.sort_key_spec_hash) == 32
        assert len(sfp.schema_semantics_hash) == 32

    def test_deterministic(self) -> None:
        sfp1 = build_structural_fingerprint()
        sfp2 = build_structural_fingerprint()
        assert sfp1 == sfp2

    def test_to_dict(self) -> None:
        sfp = build_structural_fingerprint()
        d = sfp.to_dict()
        assert "lane_scheduler_routing_hash" in d
        assert "reducer_dispatch_hash" in d
        assert "sort_key_spec_hash" in d
        assert "schema_semantics_hash" in d

    def test_different_from_structural(self) -> None:
        """Verify structural hash components are distinct from each other."""
        sfp = build_structural_fingerprint()
        hashes = {
            sfp.lane_scheduler_routing_hash,
            sfp.reducer_dispatch_hash,
            sfp.sort_key_spec_hash,
            sfp.schema_semantics_hash,
        }
        assert len(hashes) >= 3, (
            "Structural hashes must be distinct — they hash different logic"
        )


class TestDeploymentFingerprint:
    def test_build(self) -> None:
        dfp = build_deployment_fingerprint()
        assert isinstance(dfp, DeploymentFingerprint)
        assert dfp.lane_scheduler_version == "v1"
        assert "Position" in dfp.reducer_versions
        assert dfp.join_composer_version == "v1"
        assert dfp.build_hash == "dev-local"

    def test_deterministic(self) -> None:
        dfp1 = build_deployment_fingerprint()
        dfp2 = build_deployment_fingerprint()
        assert dfp1 == dfp2

    def test_to_dict(self) -> None:
        dfp = build_deployment_fingerprint()
        d = dfp.to_dict()
        assert "lane_scheduler_version" in d
        assert "reducer_versions" in d
        assert "join_composer_version" in d
        assert "build_hash" in d

    def test_structural_changes_without_deployment_change(self) -> None:
        """Structural and deployment fingerprint are independently mutable."""
        sfp = build_structural_fingerprint()
        dfp = build_deployment_fingerprint()
        # They should be different objects with different shapes
        assert sfp.to_dict() != dfp.to_dict()


class TestBuildFingerprints:
    def test_returns_both(self) -> None:
        sfp, dfp = build_fingerprints()
        assert isinstance(sfp, StructuralFingerprint)
        assert isinstance(dfp, DeploymentFingerprint)

    def test_deterministic(self) -> None:
        sfp1, dfp1 = build_fingerprints()
        sfp2, dfp2 = build_fingerprints()
        assert sfp1 == sfp2
        assert dfp1 == dfp2


# ═══════════════════════════════════════════════════════════════════
# Canonical JSON
# ═══════════════════════════════════════════════════════════════════

class TestCanonicalJson:
    def test_simple_dict(self) -> None:
        result = canonical_json({"b": 1, "a": 2})
        parsed = json.loads(result)
        assert parsed == {"a": 2, "b": 1}
        # Keys are sorted in the string representation
        assert result.index('"a"') < result.index('"b"')

    def test_excludes_clock_based_fields(self) -> None:
        obj = {"state": "OK", "generated_at": "2026-01-01T00:00:00", "session_id": "sess_1"}
        result = canonical_json(obj)
        parsed = json.loads(result)
        assert "generated_at" not in parsed
        assert "session_id" not in parsed
        assert parsed["state"] == "OK"

    def test_float_determinism(self) -> None:
        result1 = canonical_json({"value": 1.23456789})
        result2 = canonical_json({"value": 1.23456789})
        assert result1 == result2

    def test_list_preserves_order(self) -> None:
        result = canonical_json([3, 1, 2])
        parsed = json.loads(result)
        assert parsed == [3, 1, 2]

    def test_nested_dataclass(self) -> None:
        @dataclass
        class Inner:
            z: int = 3
            a: str = "x"
        obj = {"data": Inner(), "label": "test"}
        result = canonical_json(obj)
        parsed = json.loads(result)
        assert "data" in parsed
        assert parsed["label"] == "test"

    def test_identical_for_equivalent_objects(self) -> None:
        a = {"positions": {"BTC": {"qty": 0.5, "side": "LONG"}}}
        b = {"positions": {"BTC": {"side": "LONG", "qty": 0.5}}}
        assert canonical_json(a) == canonical_json(b)


# ═══════════════════════════════════════════════════════════════════
# Projection Hash
# ═══════════════════════════════════════════════════════════════════

class TestProjectionHash:
    def test_deterministic(self) -> None:
        state = OutputState.empty()
        trace: list[TraceEntry] = []
        sfp, dfp = build_fingerprints()
        h1 = compute_projection_hash(state, trace, sfp, dfp)
        h2 = compute_projection_hash(state, trace, sfp, dfp)
        assert h1 == h2
        assert len(h1) == 64

    def test_changes_on_state_diff(self) -> None:
        trace: list[TraceEntry] = []
        sfp, dfp = build_fingerprints()

        state_a = OutputState.empty()
        state_b = OutputState(
            positions=MappingProxyType({
                "p1": PositionState(
                    position_id="p1", symbol="BTC/USDT",
                    side="LONG", quantity=0.5, entry_price=60000.0,
                ),
            }),
            risk=RiskState(),
        )
        h1 = compute_projection_hash(state_a, trace, sfp, dfp)
        h2 = compute_projection_hash(state_b, trace, sfp, dfp)
        assert h1 != h2, "Different states must produce different hashes"

    def test_changes_on_trace_diff(self) -> None:
        state = OutputState.empty()
        sfp, dfp = build_fingerprints()

        ev = DomainEvent.create(
            event_type="PositionOpened", source="Test",
            data={"position_id": "p1"},
        )
        entry_a = make_trace_entry(ev)

        ev2 = DomainEvent.create(
            event_type="RiskStateChanged", source="Test",
            data={"old_status": "ACTIVE", "new_status": "HALT"},
        )
        entry_b = make_trace_entry(ev2)

        h1 = compute_projection_hash(state, [entry_a], sfp, dfp)
        h2 = compute_projection_hash(state, [entry_b], sfp, dfp)
        assert h1 != h2, "Different traces must produce different hashes"

    def test_changes_on_structural_fingerprint_diff(self) -> None:
        state = OutputState.empty()
        trace: list[TraceEntry] = []
        dfp = build_deployment_fingerprint()

        sfp1 = build_structural_fingerprint()
        sfp2 = StructuralFingerprint(
            lane_scheduler_routing_hash="aaaa",
            reducer_dispatch_hash="bbbb",
            sort_key_spec_hash="cccc",
            schema_semantics_hash="dddd",
        )
        h1 = compute_projection_hash(state, trace, sfp1, dfp)
        h2 = compute_projection_hash(state, trace, sfp2, dfp)
        assert h1 != h2, (
            "Different structural fingerprints must produce different hashes"
        )

    def test_changes_on_deployment_fingerprint_diff(self) -> None:
        state = OutputState.empty()
        trace: list[TraceEntry] = []
        sfp = build_structural_fingerprint()

        dfp1 = build_deployment_fingerprint()
        dfp2 = DeploymentFingerprint(
            lane_scheduler_version="v2",
            reducer_versions={"Position": "v1"},
            join_composer_version="v1",
            build_hash="abc123",
        )
        h1 = compute_projection_hash(state, trace, sfp, dfp1)
        h2 = compute_projection_hash(state, trace, sfp, dfp2)
        assert h1 != h2, (
            "Different deployment fingerprints must produce different hashes"
        )

    def test_structural_diff_same_deployment(self) -> None:
        """Structural change produces different hash even with same deployment."""
        state = OutputState.empty()
        trace: list[TraceEntry] = []
        dfp = build_deployment_fingerprint()

        sfp_a = StructuralFingerprint(
            lane_scheduler_routing_hash="x" * 32,
            reducer_dispatch_hash="y" * 32,
            sort_key_spec_hash="z" * 32,
            schema_semantics_hash="w" * 32,
        )
        sfp_b = StructuralFingerprint(
            lane_scheduler_routing_hash="a" * 32,
            reducer_dispatch_hash="y" * 32,
            sort_key_spec_hash="z" * 32,
            schema_semantics_hash="w" * 32,
        )
        h1 = compute_projection_hash(state, trace, sfp_a, dfp)
        h2 = compute_projection_hash(state, trace, sfp_b, dfp)
        assert h1 != h2, (
            "Structural routing change must produce different hash"
        )

    def test_deployment_only_diff_same_structural(self) -> None:
        """Deployment-only change produces different hash (correct), but
        the policy engine treats it as benign (§11.4 Rule D)."""
        state = OutputState.empty()
        trace: list[TraceEntry] = []
        sfp = build_structural_fingerprint()

        dfp_a = DeploymentFingerprint(
            lane_scheduler_version="v1",
            reducer_versions={"Position": "v1"},
            join_composer_version="v1",
            build_hash="build-001",
        )
        dfp_b = DeploymentFingerprint(
            lane_scheduler_version="v1",
            reducer_versions={"Position": "v1"},
            join_composer_version="v1",
            build_hash="build-002",
        )
        h1 = compute_projection_hash(state, trace, sfp, dfp_a)
        h2 = compute_projection_hash(state, trace, sfp, dfp_b)
        assert h1 != h2, (
            "Different builds must produce different hashes"
        )


# ═══════════════════════════════════════════════════════════════════
# Verification Engine
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestVerificationEngine:
    async def test_verify_with_empty_store_and_wrong_hash(self, store: Any) -> None:
        result = await VerificationEngine.verify(store, "000000")
        assert result is False  # Wrong expected hash → verification fails

    async def test_verify_computes_hash_from_replay(self, store: Any) -> None:
        ev = DomainEvent.create(
            event_type="PositionOpened", source="Test",
            data={
                "position_id": "p1", "symbol": "BTC/USDT",
                "side": "LONG", "entry_price": 60000.0, "quantity": 0.5,
            },
        )
        await store.write(ev)
        engine = EventSourcedReplay()
        replay_result = await engine.replay(store, mode="STRICT")
        expected = compute_projection_hash(
            replay_result.state, replay_result.trace,
            replay_result.structural_fingerprint,
            replay_result.deployment_fingerprint,
        )
        result = await VerificationEngine.verify(store, expected)
        assert result is True

    async def test_full_equivalence_with_empty_store(self, store: Any) -> None:
        report = await VerificationEngine.full_equivalence(store)
        assert isinstance(report, VerificationReport)
        assert report.is_equivalent
        assert report.full_hash_matches

    async def test_full_equivalence_with_events(self, store: Any) -> None:
        ev = DomainEvent.create(
            event_type="PositionOpened", source="Test",
            data={
                "position_id": "p1", "symbol": "BTC/USDT",
                "side": "LONG", "entry_price": 60000.0, "quantity": 0.5,
            },
        )
        await store.write(ev)

        report = await VerificationEngine.full_equivalence(store)
        assert isinstance(report, VerificationReport)
        assert report.is_equivalent
        assert report.full_hash_matches

    async def test_full_equivalence_reports_detailed_matches(self, store: Any) -> None:
        """VerificationReport exposes state, trace, structural, and deployment
        match flags independently for policy decisions."""
        ev = DomainEvent.create(
            event_type="RiskStateChanged", source="RiskPolicyEngine",
            data={"old_status": "ACTIVE", "new_status": "DEGRADED"},
        )
        await store.write(ev)
        report = await VerificationEngine.full_equivalence(store)
        assert isinstance(report, VerificationReport)
        assert report.state_match
        assert report.trace_match
        assert report.structural_match
        assert report.deployment_match
        assert report.is_correct
        assert report.is_equivalent
        assert report.is_operationally_safe
        assert report.full_hash_matches

    async def test_three_level_truth_model(self, store: Any) -> None:
        """is_correct, is_equivalent, and is_operationally_safe have
        different sensitivities to deployment drift."""
        ev = DomainEvent.create(
            event_type="PositionOpened", source="Test",
            data={
                "position_id": "p1", "symbol": "BTC/USDT",
                "side": "LONG", "entry_price": 60000.0, "quantity": 0.5,
            },
        )
        await store.write(ev)

        report = await VerificationEngine.full_equivalence(store)
        assert isinstance(report, VerificationReport)

        # All should pass in a clean run
        assert report.is_correct
        assert report.is_equivalent
        assert report.is_operationally_safe

        # Simulate: construct a report with only deployment mismatch
        deploy_only_report = VerificationReport(
            full_hash=report.full_hash,
            snap_hash=report.snap_hash,
            state_match=True,
            trace_match=True,
            structural_match=True,
            deployment_match=False,  # only deployment differs
        )
        assert deploy_only_report.is_correct, (
            "Deployment-only mismatch must NOT break is_correct"
        )
        assert deploy_only_report.is_operationally_safe, (
            "Deployment-only mismatch must NOT break is_operationally_safe"
        )
        assert not deploy_only_report.is_equivalent, (
            "is_equivalent requires ALL four matches (including deployment)"
        )

        # Simulate: structural mismatch
        struct_mismatch_report = VerificationReport(
            full_hash=report.full_hash,
            snap_hash=report.snap_hash,
            state_match=True,
            trace_match=True,
            structural_match=False,  # structural differs
            deployment_match=True,
        )
        assert not struct_mismatch_report.is_correct, (
            "Structural mismatch must break is_correct"
        )
        assert not struct_mismatch_report.is_operationally_safe, (
            "Structural mismatch must break is_operationally_safe"
        )
        assert not struct_mismatch_report.is_equivalent

        # Simulate: state mismatch (hard truth failure)
        state_mismatch_report = VerificationReport(
            full_hash=report.full_hash,
            snap_hash=report.snap_hash,
            state_match=False,  # state differs
            trace_match=True,
            structural_match=True,
            deployment_match=True,
        )
        assert not state_mismatch_report.is_correct, (
            "State mismatch must break is_correct"
        )
        assert not state_mismatch_report.is_operationally_safe
