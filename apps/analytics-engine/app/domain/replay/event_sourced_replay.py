"""EventSourcedReplay — Dual-Lane replay engine with snapshot support.

Architecture (EMC v1 §7):
  - Entity Lane: per-symbol state machines (positions, signals, orders).
  - Global Lane: risk/portfolio/system state (no entity grouping).
  - Join Composer: pure composition of both lanes → SystemState.

Uso:
    engine = EventSourcedReplay()
    result = await engine.replay(event_store, mode="STRICT")
    system_state = result.system_state  # SystemState object
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...application.ports.event_store import EventStore
from ...domain.entities.invariants import (
    InvariantError,
    check_invariant,
    fail_fast_on_invariant_break,
)
from ...domain.entities.reducers import reduce
from ...domain.entities.replay_state import (
    OutputState,
    _entity_id,
    _stream_priority,
)
from ...domain.replay.execution_trace import TraceEntry, make_trace_entry
from ...domain.replay.join_composer import SystemState, compose
from ...domain.replay.lane_scheduler import LaneScheduler
from ...domain.replay.projection_metadata import (
    DeploymentFingerprint,
    StructuralFingerprint,
    build_fingerprints,
)
from ...domain.replay.replay_planner import (
    PlannerDecision,
    ReplayPlanner,
    SnapshotMetadata,
)


@dataclass
class ReplayResult:
    """Resultado completo de un replay."""
    success: bool
    mode: str
    total_events: int
    processed_events: int
    skipped_events: int
    n_partitions: int
    state: OutputState
    system_state: SystemState | None = None
    planner_decision: PlannerDecision | None = None
    violations: list[tuple[str, str]] = field(default_factory=list)
    error: str | None = None
    trace: list[TraceEntry] = field(default_factory=list)
    structural_fingerprint: StructuralFingerprint | None = None
    deployment_fingerprint: DeploymentFingerprint | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "success": self.success,
            "mode": self.mode,
            "total_events": self.total_events,
            "processed_events": self.processed_events,
            "skipped_events": self.skipped_events,
            "n_partitions": self.n_partitions,
            "violations": [
                {"rule": r, "message": m} for r, m in self.violations
            ],
            "error": self.error,
            "state": self.state.to_dict(),
        }
        if self.trace:
            d["trace"] = [t.to_dict() for t in self.trace]
        if self.structural_fingerprint:
            d["structural_fingerprint"] = self.structural_fingerprint.to_dict()
        if self.deployment_fingerprint:
            d["deployment_fingerprint"] = self.deployment_fingerprint.to_dict()
        if self.system_state:
            d["system_state"] = self.system_state.to_dict()
        if self.planner_decision:
            d["planner"] = {
                "from_index": self.planner_decision.from_index,
                "is_full_replay": self.planner_decision.is_full_replay,
                "snapshots_invalidated": (
                    self.planner_decision.snapshots_invalidated
                ),
                "reason": self.planner_decision.reason,
            }
        return d


def _sort_key(event: Any) -> tuple:
    """Deterministic sort key for partial-order replay.

    ORDER BY:
      1. entity_id
      2. stream_priority (0=risk, 1=signal, 2=execution, 3=position, ...)
      3. timestamp_bucket (numeric string)
      4. timestamp (event_time)
      5. event_id (tiebreaker, deterministic)
    """
    eid = _entity_id(event)
    sp = _stream_priority(event.event_type)
    bucket = event.timestamp_bucket or "0"
    return (eid, sp, bucket, event.timestamp, event.event_id)


class EventSourcedReplay:
    """Replay engine determinista desde EventStore.

    No hace I/O (excepto lectura inicial del EventStore).
    No accede al modelo en vivo.
    No genera nuevas decisiones.
    Solo reconstruye estado a partir de eventos.
    """

    def __init__(self) -> None:
        self._result: ReplayResult | None = None

    async def replay(
        self,
        event_store: EventStore,
        mode: str = "STRICT",
        event_types: list[str] | None = None,
        snapshot: SnapshotMetadata | None = None,
        initial_state: OutputState | None = None,
        late_event_time: str | None = None,
        environment_mode: str = "UNKNOWN",
    ) -> ReplayResult:
        """Execute deterministic replay.

        Args:
            event_store: Fuente de eventos.
            mode: STRICT → abort on invariant violation.
                  RECOVERY → report violations, continue.
            event_types: Optional filter — only replay these types.
            snapshot: Snapshot metadata for partial replay.
            initial_state: Estado base del snapshot (None = empty).
            late_event_time: Late event time for contamination detection.
            environment_mode: Current mode (LIVE_SIMULATION, etc.).

        Returns:
            ReplayResult with reconstructed state, SystemState, and violations.
        """
        if mode not in ("STRICT", "RECOVERY"):
            raise ValueError(f"Unknown replay mode: {mode}")

        # 1. Plan the replay (snapshot validation + contamination check)
        total = await event_store.count()
        max_idx = await event_store.max_event_index()

        planner = ReplayPlanner()
        decision = planner.plan(
            current_version="1.0",
            max_store_index=max_idx,
            snapshot=snapshot,
            late_event_time=late_event_time,
        )

        # Build fingerprints once per replay
        structural_fp, deployment_fp = build_fingerprints()

        if total == 0:
            return ReplayResult(
                success=True,
                mode=mode,
                total_events=0,
                processed_events=0,
                skipped_events=0,
                n_partitions=0,
                state=OutputState.empty(),
                planner_decision=decision,
                structural_fingerprint=structural_fp,
                deployment_fingerprint=deployment_fp,
            )

        # 2. Load events from decision.from_index
        events = await self._load_all_events(
            event_store, event_types, decision.from_index,
        )

        # 3. Sort ALL events by (entity_id, stream_priority, bucket, timestamp, event_id)
        events.sort(key=_sort_key)

        # 4. Reduce (pure function per event) + build execution trace
        state = initial_state if initial_state is not None else OutputState.empty()
        processed = 0
        skipped = 0
        violations: list[tuple[str, str]] = []
        lane_counts: dict[str, int] = {"entity": 0, "global": 0}
        trace: list[TraceEntry] = []

        for ev in events:
            try:
                new_state = reduce(ev, state)
                state = new_state
                processed += 1

                lane = LaneScheduler.route(ev)
                if lane.name == "ENTITY":
                    lane_counts["entity"] += 1
                else:
                    lane_counts["global"] += 1

                # Build trace entry (pure, deterministic)
                trace_entry = make_trace_entry(ev)
                trace.append(trace_entry)

                if mode == "STRICT":
                    fail_fast_on_invariant_break(state)
                else:
                    vs = check_invariant(state)
                    violations.extend(vs)

            except InvariantError:
                raise
            except Exception as e:
                if mode == "STRICT":
                    raise InvariantError(
                        f"reducer failed for {ev.event_type} "
                        f"({ev.event_id}): {e}"
                    ) from e
                skipped += 1
                violations.append(
                    ("reducer_error", f"{ev.event_type}: {e}")
                )

        # 5. Build SystemState via Join Composer
        system_state = compose(state, mode=environment_mode)

        result = ReplayResult(
            success=len(violations) == 0 or mode == "RECOVERY",
            mode=mode,
            total_events=total,
            processed_events=processed,
            skipped_events=skipped,
            n_partitions=len(set(_entity_id(e) for e in events)),
            state=state,
            system_state=system_state,
            planner_decision=decision,
            violations=violations,
            trace=trace,
            structural_fingerprint=structural_fp,
            deployment_fingerprint=deployment_fp,
        )
        self._result = result
        return result

    async def _load_all_events(
        self,
        event_store: EventStore,
        event_types: list[str] | None = None,
        from_index: int = 0,
    ) -> list[Any]:
        """Load all events from store, optionally filtered by type and index."""
        all_events: list[Any] = []

        if event_types:
            for et in event_types:
                async for ev in event_store.stream_by_index_range(from_index):
                    if ev.event_type == et:
                        all_events.append(ev)
        else:
            async for ev in event_store.stream_by_index_range(from_index):
                all_events.append(ev)

        return all_events

    @property
    def result(self) -> ReplayResult | None:
        return self._result
