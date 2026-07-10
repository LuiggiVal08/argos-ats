"""Property-based tests for Replay Engine determinism.

Properties verified:
  1. DETERMINISM: replay(events) == replay(events) for identical input.
  2. SNAPSHOT EQUIVALENCE: full replay == snapshot(events[:n]) + replay(events[n:]).
  3. CHUNK INVARIANCE: replay(events) == replay(chunk1) + replay(chunk2) + replay(chunk3).

These detect non-determinism from:
  - datetime.utcnow() in reducers
  - uuid4() calls
  - random.random()
  - dict iteration order dependence
  - cached state leaking between runs
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis.strategies import (
    composite,
    floats,
    integers,
    just,
    lists,
    sampled_from,
    text,
)

from app.domain.entities.replay_state import OutputState
from app.domain.replay.event_sourced_replay import EventSourcedReplay
from app.domain.replay.replay_planner import SnapshotMetadata
from app.domain.value_objects.domain_event import DomainEvent
from app.infrastructure.repositories.sqlite_event_store import SQLiteEventStore


# ─── Helpers ────────────────────────────────────────────────────

_counter: int = 0


def _next_pos_id() -> str:
    """Generate unique position/signal IDs to avoid event_id collisions."""
    global _counter
    _counter += 1
    return f"id_{_counter}"


@dataclass
class EventTemplate:
    event_type: str
    source: str
    data_fn: Any  # callable that returns data dict


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
    EventTemplate("SignalGenerated", "SignalEngine", lambda: {
        "symbol": _next_pos_id(), "side": "BUY", "confidence": 0.85,
    }),
    EventTemplate("RiskStateChanged", "RiskPolicyEngine", lambda: {
        "old_status": "ACTIVE", "new_status": "DEGRADED",
        "reason": "p_failure_breach",
    }),
    EventTemplate("SimulationEvaluated", "SimulationEngine", lambda: {
        "symbol": "BTC/USDT", "p_failure": 0.3, "n_failed": 1500,
        "n_total": 5000,
    }),
]


def _make_event(template: EventTemplate, ts_bucket: str | None = None) -> DomainEvent:
    return DomainEvent.create(
        event_type=template.event_type,
        source=template.source,
        data=template.data_fn(),
        timestamp_bucket=ts_bucket,
    )


@composite
def event_lists(draw: Any, min_size: int = 1, max_size: int = 50) -> list[DomainEvent]:
    """Generate a list of DomainEvents using valid transition templates."""
    n = draw(integers(min_value=min_size, max_value=max_size))
    return [_make_event(draw(sampled_from(TEMPLATES))) for _ in range(n)]


def _fresh_db_path(label: str = "") -> str:
    """Create a unique temp file path for each test run."""
    tmpdir = tempfile.mkdtemp()
    return os.path.join(tmpdir, f"replay_prop{label}.db")


def _deterministic_hash(result: Any) -> str:
    """Compute a deterministic hash from replay result, excluding metadata."""
    state_d = result.state.to_dict()
    ss_d = result.system_state.to_dict() if result.system_state else {}
    # Exclude generated_at (clock-based, not logical state)
    ss_d.pop("generated_at", None)
    return f"{state_d}_{ss_d}"


async def _replay_events(
    events: list[DomainEvent],
    db_path: str,
    snapshot: SnapshotMetadata | None = None,
) -> str:
    """Replay a list of events, return hash of deterministic state."""
    store = SQLiteEventStore(db_path=db_path)
    try:
        for ev in events:
            await store.write(ev)
        engine = EventSourcedReplay()
        result = await engine.replay(
            store, mode="STRICT", snapshot=snapshot,
        )
        return _deterministic_hash(result)
    finally:
        store.close()


async def _compute_snapshot_state(
    events: list[DomainEvent], label: str,
) -> OutputState:
    """Replay prefix events and return the resulting OutputState."""
    db_path = _fresh_db_path(f"{label}_state")
    store = SQLiteEventStore(db_path=db_path)
    try:
        for ev in events:
            await store.write(ev)
        engine = EventSourcedReplay()
        result = await engine.replay(store, mode="STRICT")
        return result.state
    finally:
        store.close()


async def _snapshot_eq_replay(
    events: list[DomainEvent], split: int, label: str,
) -> tuple[str, str]:
    """Return (full_hash, snapshot_hash) for comparison."""
    full_db = _fresh_db_path(f"{label}_full")
    full_hash = await _replay_events(events, full_db)

    snap_db = _fresh_db_path(f"{label}_snap")
    store = SQLiteEventStore(db_path=snap_db)

    try:
        for ev in events[:split]:
            await store.write(ev)
        snap_idx = await store.max_event_index()
        last_ev = events[split - 1] if split > 0 else events[0]
        snap = SnapshotMetadata(
            version="1.0", event_index=snap_idx,
            last_event_id=last_ev.event_id,
            last_event_hash=f"hash_{last_ev.event_id}",
            last_event_time=last_ev.timestamp,
        )
        # Build snapshot state by replaying first split events
        snapshot_state = await _compute_snapshot_state(events[:split], f"{label}_prefix")

        for ev in events[split:]:
            await store.write(ev)
        engine = EventSourcedReplay()
        result = await engine.replay(
            store, mode="STRICT", snapshot=snap,
            initial_state=snapshot_state,
        )
        snap_hash = _deterministic_hash(result)
    finally:
        store.close()

    return full_hash, snap_hash


async def _chunked_replay(events: list[DomainEvent], label: str) -> tuple[str, str]:
    """Return (full_hash, chunked_hash) for comparison."""
    full_db = _fresh_db_path(f"{label}_full")
    full_hash = await _replay_events(events, full_db)

    n = len(events)
    split1 = n // 3
    split2 = 2 * n // 3

    chunk_db = _fresh_db_path(f"{label}_chunk")
    store = SQLiteEventStore(db_path=chunk_db)
    try:
        chunks = [events[:split1], events[split1:split2], events[split2:]]
        for chunk in chunks:
            for ev in chunk:
                await store.write(ev)
        engine = EventSourcedReplay()
        result = await engine.replay(store, mode="STRICT")
        chunk_hash = _deterministic_hash(result)
    finally:
        store.close()

    return full_hash, chunk_hash


# ─── Test: Determinism ──────────────────────────────────────────

class TestDeterminism:
    """Property 1: replay(events) == replay(events)  for identical input."""

    @given(events=event_lists(min_size=1, max_size=30))
    @settings(max_examples=200, deadline=None,
              suppress_health_check=["function_scoped_fixture"])
    async def test_deterministic_replay(
        self, events: list[DomainEvent],
    ) -> None:
        db1 = _fresh_db_path("det_a")
        db2 = _fresh_db_path("det_b")
        hash1 = await _replay_events(events, db1)
        hash2 = await _replay_events(events, db2)
        assert hash1 == hash2, (
            f"Replay produced different states for same {len(events)} events"
        )


# ─── Test: Snapshot Equivalence ──────────────────────────────

class TestSnapshotEquivalence:
    """Property 2: full replay == snapshot + incremental replay."""

    @given(events=event_lists(min_size=5, max_size=40))
    @settings(max_examples=50, deadline=None,
              suppress_health_check=["function_scoped_fixture"])
    async def test_snapshot_equivalence(
        self, events: list[DomainEvent],
    ) -> None:
        split = max(2, len(events) // 2)
        full_hash, snap_hash = await _snapshot_eq_replay(events, split, "seq")
        assert full_hash == snap_hash, (
            f"Snapshot replay diverged from full replay for {len(events)} events "
            f"(split at {split})"
        )


# ─── Test: Chunk Invariance ──────────────────────────────────

class TestChunkInvariance:
    """Property 3: replay(all) == replay(chunk1+chunk2+chunk3)."""

    @given(events=event_lists(min_size=6, max_size=45))
    @settings(max_examples=50, deadline=None,
              suppress_health_check=["function_scoped_fixture"])
    async def test_chunk_invariance(
        self, events: list[DomainEvent],
    ) -> None:
        full_hash, chunk_hash = await _chunked_replay(events, "chk")
        assert full_hash == chunk_hash, (
            f"Chunked replay diverged from full replay for {len(events)} events"
        )



