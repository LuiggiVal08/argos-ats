"""Property-based tests for F3 Execution Trace invariants.

Properties verified (EMC v1 §10):
  1. TRACE DETERMINISM: trace(events) == trace(events) for identical input.
  2. TRACE-ROUTE CONSISTENCY: ∀ i, trace[i].lane == LaneScheduler.route(sorted_events[i]).
  3. TRACE-EVENT CORRESPONDENCE: ∀ i, trace[i].event_id == sorted_events[i].event_id.
  4. TRACE SNAPSHOT EQUIVALENCE: full_replay.trace == snapshot_replay.trace.
  5. TRANSITION ID DETERMINISM: ∀ i, trace[i].transition_id == compute_transition_id(...).
  6. FINGERPRINT DETERMINISM: fingerprint(events) == fingerprint(events).

These detect:
  - LaneScheduler version drift between trace generation and actual routing
  - Sort order inconsistency between trace and replay
  - Non-determinism in trace generation
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import Any

from hypothesis import given, settings
from hypothesis.strategies import (
    composite,
    integers,
    sampled_from,
)

from app.domain.replay.event_sourced_replay import EventSourcedReplay, _sort_key
from app.domain.replay.execution_trace import (
    TraceEntry,
    compute_transition_id,
    invariant_pattern_for,
    reducer_id_for,
)
from app.domain.replay.lane_scheduler import LaneScheduler
from app.domain.replay.projection_hasher import compute_projection_hash
from app.domain.replay.replay_planner import SnapshotMetadata
from app.domain.value_objects.domain_event import DomainEvent
from app.infrastructure.repositories.sqlite_event_store import SQLiteEventStore


# ─── Helpers (from test_replay_properties) ────────────────────────

_counter: int = 0


def _next_pos_id() -> str:
    global _counter
    _counter += 1
    return f"id_{_counter}"


@dataclass
class EventTemplate:
    event_type: str
    source: str
    data_fn: Any


def _make_data(base: dict[str, Any]) -> dict[str, Any]:
    """Add a unique nonce to every event to guarantee distinct event_ids."""
    d = dict(base)
    d["_nonce"] = _next_pos_id()
    return d


TEMPLATES: list[EventTemplate] = [
    EventTemplate("PositionOpened", "ExecutionCore", lambda: {
        "position_id": _next_pos_id(), "symbol": "BTC/USDT", "side": "LONG",
        "entry_price": 60000.0, "quantity": 0.5,
    }),
    EventTemplate("PositionOpened", "ExecutionCore", lambda: {
        "position_id": _next_pos_id(), "symbol": "ETH/USDT", "side": "LONG",
        "entry_price": 3500.0, "quantity": 2.0,
    }),
    EventTemplate("PositionUpdated", "ExecutionCore", lambda: {
        "position_id": _next_pos_id(), "sl_price": 59500.0,
    }),
    EventTemplate("PositionClosed", "ExecutionCore", lambda: {
        "position_id": _next_pos_id(), "close_price": 61000.0, "pnl": 500.0,
        "close_reason": "take_profit", "hold_bars": 5,
    }),
    EventTemplate("SignalGenerated", "SignalEngine", lambda: _make_data({
        "symbol": _next_pos_id(), "side": "BUY", "confidence": 0.85,
    })),
    EventTemplate("RiskStateChanged", "RiskPolicyEngine", lambda: _make_data({
        "old_status": "ACTIVE", "new_status": "DEGRADED",
        "reason": "p_failure_breach",
    })),
    EventTemplate("SimulationEvaluated", "SimulationEngine", lambda: _make_data({
        "symbol": "BTC/USDT", "p_failure": 0.3, "n_failed": 1500,
        "n_total": 5000,
    })),
]


def _make_event(template: EventTemplate) -> DomainEvent:
    return DomainEvent.create(
        event_type=template.event_type,
        source=template.source,
        data=template.data_fn(),
    )


@composite
def event_lists(draw: Any, min_size: int = 1, max_size: int = 30) -> list[DomainEvent]:
    n = draw(integers(min_value=min_size, max_value=max_size))
    return [_make_event(draw(sampled_from(TEMPLATES))) for _ in range(n)]


def _fresh_db_path(label: str = "") -> str:
    tmpdir = tempfile.mkdtemp()
    return os.path.join(tmpdir, f"f3_trace_{label}.db")


async def _replay_get_trace(
    events: list[DomainEvent],
    db_path: str,
    snapshot: SnapshotMetadata | None = None,
    initial_state: Any = None,
) -> tuple[list[DomainEvent], list[TraceEntry], Any, Any, Any]:
    """Store events, replay, return (sorted_events, trace, state, sfp, dfp)."""
    store = SQLiteEventStore(db_path=db_path)
    try:
        for ev in events:
            await store.write(ev)
        engine = EventSourcedReplay()
        result = await engine.replay(
            store, mode="STRICT",
            snapshot=snapshot,
            initial_state=initial_state,
        )
        # Sort events the same way the engine does
        sorted_events = sorted(events, key=_sort_key)
        return (
            sorted_events,
            result.trace,
            result.state,
            result.structural_fingerprint,
            result.deployment_fingerprint,
        )
    finally:
        store.close()


# ═══════════════════════════════════════════════════════════════════
# Property 1: Trace Determinism
# ═══════════════════════════════════════════════════════════════════

class TestTraceDeterminism:
    """Property 1: trace(events) == trace(events) for identical input."""

    @given(events=event_lists(min_size=1, max_size=20))
    @settings(max_examples=100, deadline=None)
    async def test_trace_identical_on_identical_events(
        self, events: list[DomainEvent],
    ) -> None:
        db1 = _fresh_db_path("td_a")
        db2 = _fresh_db_path("td_b")
        _, trace1, _, sfp1, dfp1 = await _replay_get_trace(events, db1)
        _, trace2, _, sfp2, dfp2 = await _replay_get_trace(events, db2)
        assert trace1 == trace2, (
            f"Trace differs for same {len(events)} events"
        )
        # Both fingerprints must be deterministic
        assert sfp1 == sfp2, (
            f"Structural fingerprint differs for same {len(events)} events"
        )
        assert dfp1 == dfp2, (
            f"Deployment fingerprint differs for same {len(events)} events"
        )


# ═══════════════════════════════════════════════════════════════════
# Property 2: Trace-Route Consistency
# ═══════════════════════════════════════════════════════════════════

class TestTraceRouteConsistency:
    """Property 2: ∀ i, trace[i].lane == LaneScheduler.route(sorted_events[i])."""

    @given(events=event_lists(min_size=1, max_size=20))
    @settings(max_examples=100, deadline=None)
    async def test_trace_lane_matches_route(
        self, events: list[DomainEvent],
    ) -> None:
        db = _fresh_db_path("tr")
        sorted_events, trace, _, _, _ = await _replay_get_trace(events, db)
        assert len(trace) == len(sorted_events), (
            f"Trace length {len(trace)} != sorted events {len(sorted_events)}"
        )
        for i, (entry, ev) in enumerate(zip(trace, sorted_events)):
            expected_lane = LaneScheduler.route(ev).name
            assert entry.lane == expected_lane, (
                f"Entry {i}: trace lane {entry.lane} != "
                f"route({ev.event_type}) = {expected_lane}"
            )


# ═══════════════════════════════════════════════════════════════════
# Property 3: Trace-Event Correspondence
# ═══════════════════════════════════════════════════════════════════

class TestTraceEventCorrespondence:
    """Property 3: ∀ i, trace[i].event_id == sorted_events[i].event_id."""

    @given(events=event_lists(min_size=1, max_size=20))
    @settings(max_examples=100, deadline=None)
    async def test_trace_event_ids_match(
        self, events: list[DomainEvent],
    ) -> None:
        db = _fresh_db_path("tec")
        sorted_events, trace, _, _, _ = await _replay_get_trace(events, db)
        assert len(trace) == len(sorted_events)
        for i, (entry, ev) in enumerate(zip(trace, sorted_events)):
            assert entry.event_id == ev.event_id, (
                f"Entry {i}: trace event_id {entry.event_id} != "
                f"sorted event {ev.event_id}"
            )

    @given(events=event_lists(min_size=1, max_size=20))
    @settings(max_examples=100, deadline=None)
    async def test_trace_event_ids_exist_in_input(
        self, events: list[DomainEvent],
    ) -> None:
        db = _fresh_db_path("tei")
        _, trace, _, _, _ = await _replay_get_trace(events, db)
        input_ids = {ev.event_id for ev in events}
        for entry in trace:
            assert entry.event_id in input_ids, (
                f"Trace contains event_id {entry.event_id} not in input"
            )
        assert len(trace) == len(events), (
            f"Trace length {len(trace)} != input {len(events)} — "
            f"events may have been deduplicated"
        )


# ═══════════════════════════════════════════════════════════════════
# Property 4: Trace Snapshot Equivalence
# ═══════════════════════════════════════════════════════════════════

async def _snapshot_trace_eq(
    events: list[DomainEvent], split: int, label: str,
) -> tuple[list[TraceEntry], list[TraceEntry]]:
    """Return (full_trace, snapshot_trace) for comparison."""
    full_db = _fresh_db_path(f"{label}_ft")
    full_store = SQLiteEventStore(db_path=full_db)
    try:
        for ev in events:
            await full_store.write(ev)
        engine = EventSourcedReplay()
        full_result = await engine.replay(full_store, mode="STRICT")
        full_trace = full_result.trace
    finally:
        full_store.close()

    # Snapshot replay
    snap_db = _fresh_db_path(f"{label}_st")
    snap_store = SQLiteEventStore(db_path=snap_db)
    try:
        for ev in events[:split]:
            await snap_store.write(ev)
        snap_idx = await snap_store.max_event_index()
        last_ev = events[split - 1] if split > 0 else events[0]
        snap_meta = SnapshotMetadata(
            version="1.0", event_index=snap_idx,
            last_event_id=last_ev.event_id,
            last_event_hash=f"hash_{last_ev.event_id}",
            last_event_time=last_ev.timestamp,
        )

        prefix_db = _fresh_db_path(f"{label}_pfx")
        prefix_store = SQLiteEventStore(db_path=prefix_db)
        try:
            for ev in events[:split]:
                await prefix_store.write(ev)
            pfx_engine = EventSourcedReplay()
            pfx_result = await pfx_engine.replay(prefix_store, mode="STRICT")
            snapshot_state = pfx_result.state
        finally:
            prefix_store.close()

        for ev in events[split:]:
            await snap_store.write(ev)
        snap_engine = EventSourcedReplay()
        snap_result = await snap_engine.replay(
            snap_store, mode="STRICT",
            snapshot=snap_meta, initial_state=snapshot_state,
        )
        snap_trace = snap_result.trace
    finally:
        snap_store.close()

    return full_trace, snap_trace, events


class TestTraceSnapshotEquivalence:
    """Property 4: snapshot trace events are subset of full trace events."""

    @given(events=event_lists(min_size=5, max_size=30))
    @settings(max_examples=50, deadline=None)
    async def test_snapshot_trace_has_tail_events(
        self, events: list[DomainEvent],
    ) -> None:
        split = max(2, len(events) // 2)
        full_trace, snap_trace, all_events = await _snapshot_trace_eq(
            events, split, "tse",
        )
        # Snapshot trace should only contain events from the tail
        tail_ids = {ev.event_id for ev in events[split:]}
        snap_ids = {entry.event_id for entry in snap_trace}
        for entry in snap_trace:
            assert entry.event_id in tail_ids, (
                f"Snapshot trace contains event {entry.event_id} "
                f"from prefix, not tail"
            )
        # Full trace should contain all event_ids
        all_ids = {ev.event_id for ev in events}
        full_ids = {entry.event_id for entry in full_trace}
        assert full_ids == all_ids, (
            f"Full trace missing events: {all_ids - full_ids}"
        )

    @given(events=event_lists(min_size=5, max_size=30))
    @settings(max_examples=50, deadline=None)
    async def test_snapshot_trace_order_matches_full(
        self, events: list[DomainEvent],
    ) -> None:
        split = max(2, len(events) // 2)
        full_trace, snap_trace, all_events = await _snapshot_trace_eq(
            events, split, "tse",
        )
        # Build set of tail event_ids
        tail_ids = {ev.event_id for ev in events[split:]}
        # Filter full trace to only tail events, preserving order
        full_tail_trace = [e for e in full_trace if e.event_id in tail_ids]
        assert full_tail_trace == snap_trace, (
            f"Snapshot trace order differs from full trace's tail for "
            f"{len(events)} events (split at {split})"
        )


# ═══════════════════════════════════════════════════════════════════
# Property 5: Transition ID Determinism
# ═══════════════════════════════════════════════════════════════════

class TestTransitionIdDeterminism:
    """Property 5: ∀ i, trace[i].transition_id == compute_transition_id(...)."""

    @given(events=event_lists(min_size=1, max_size=20))
    @settings(max_examples=100, deadline=None)
    async def test_transition_id_matches_computation(
        self, events: list[DomainEvent],
    ) -> None:
        db = _fresh_db_path("tid")
        sorted_events, trace, _, _, _ = await _replay_get_trace(events, db)
        for i, (entry, ev) in enumerate(zip(trace, sorted_events)):
            rid = reducer_id_for(ev.event_type)
            pat = invariant_pattern_for(ev.event_type)
            expected_tid = compute_transition_id(ev.event_type, rid, pat)
            assert entry.transition_id == expected_tid, (
                f"Entry {i} ({ev.event_type}): transition_id "
                f"{entry.transition_id} != computed {expected_tid}"
            )


# ═══════════════════════════════════════════════════════════════════
# Property 6: Projection Hash Determinism (redundancy check)
# ═══════════════════════════════════════════════════════════════════

class TestProjectionHashDeterminism:
    """Property 6: projection_hash(events) == projection_hash(events)."""

    @given(events=event_lists(min_size=1, max_size=20))
    @settings(max_examples=50, deadline=None)
    async def test_projection_hash_identical(
        self, events: list[DomainEvent],
    ) -> None:
        db1 = _fresh_db_path("ph1")
        db2 = _fresh_db_path("ph2")
        _, trace1, state1, sfp1, dfp1 = await _replay_get_trace(events, db1)
        _, trace2, state2, sfp2, dfp2 = await _replay_get_trace(events, db2)
        h1 = compute_projection_hash(state1, trace1, sfp1, dfp1)
        h2 = compute_projection_hash(state2, trace2, sfp2, dfp2)
        assert h1 == h2, (
            f"Projection hash differs for same {len(events)} events"
        )
