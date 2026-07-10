"""Execution Trace — captures the actual path of each event during replay.

EMC v1 §10.2: Each TraceEntry records:
  - event_id: which event
  - lane: ENTITY or GLOBAL
  - reducer_id: which reducer version processed it
  - transition_id: SHA-256(event_type + reducer_id + invariant_pattern)
  - entity_id: symbol or "global"
  - ordering_key: canonical sort key string

Property: deterministic given the same events + same replay config.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from ...domain.entities.replay_state import _entity_id, _stream_priority
from ...domain.replay.lane_scheduler import LaneScheduler
from ...domain.value_objects.domain_event import DomainEvent


@dataclass(frozen=True)
class TraceEntry:
    """A single step in the execution trace.
    One entry per event applied during replay.
    """
    event_id: str
    lane: str
    reducer_id: str
    transition_id: str
    entity_id: str
    ordering_key: str

    def to_dict(self) -> dict[str, str]:
        return {
            "event_id": self.event_id,
            "lane": self.lane,
            "reducer_id": self.reducer_id,
            "transition_id": self.transition_id,
            "entity_id": self.entity_id,
            "ordering_key": self.ordering_key,
        }


# ── Reducer ID mapping ────────────────────────────────────────────

_REDUCER_IDS: dict[str, str] = {
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

_DEFAULT_REDUCER_ID = "UnknownReducer/v1"


def reducer_id_for(event_type: str) -> str:
    """Return the reducer_id for a given event_type."""
    return _REDUCER_IDS.get(event_type, _DEFAULT_REDUCER_ID)


# ── Invariant pattern mapping ─────────────────────────────────────

_INVARIANT_PATTERNS: dict[str, str] = {
    "PositionOpened": "POSITION_STATE_MACHINE_v1",
    "PositionUpdated": "POSITION_STATE_MACHINE_v1",
    "PositionClosed": "POSITION_STATE_MACHINE_v1",
    "RiskEvaluated": "RISK_STATE_MACHINE_v1",
    "RiskLimitBreached": "RISK_STATE_MACHINE_v1",
    "RiskStateChanged": "RISK_STATE_MACHINE_v1",
    "SignalGenerated": "SIGNAL_LAST_VALUE_v1",
    "SignalRejected": "SIGNAL_LAST_VALUE_v1",
    "SimulationEvaluated": "SIMULATION_REDUCER_v1",
    "MonteCarloCompleted": "SIMULATION_REDUCER_v1",
    "ConfigChanged": "CONFIG_REDUCER_v1",
}

_DEFAULT_INVARIANT = "UNKNOWN_INVARIANT_v1"


def invariant_pattern_for(event_type: str) -> str:
    """Return the invariant pattern identifier for an event_type."""
    return _INVARIANT_PATTERNS.get(event_type, _DEFAULT_INVARIANT)


# ── Transition ID computation ─────────────────────────────────────


def compute_transition_id(
    event_type: str,
    reducer_id: str,
    invariant_pattern: str,
) -> str:
    """Deterministic transition ID.

    transition_id = SHA-256(event_type + '|' + reducer_id + '|' + invariant_pattern)

    Changes when any of the three components changes:
      - event_type: different event
      - reducer_id: different reducer version
      - invariant_pattern: different state machine rules
    """
    payload = f"{event_type}|{reducer_id}|{invariant_pattern}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


# ── Sort key serialization ────────────────────────────────────────


def _sort_key_to_str(event: DomainEvent) -> str:
    """Serialize the sort key tuple deterministically (mirrors _sort_key)."""
    eid = _entity_id(event)
    sp = _stream_priority(event.event_type)
    bucket = event.timestamp_bucket or "0"
    key = (eid, sp, bucket, event.timestamp, event.event_id)
    return "|".join(str(k) for k in key)


# ── Trace entry factory ───────────────────────────────────────────


def make_trace_entry(event: DomainEvent) -> TraceEntry:
    """Build a TraceEntry for a single event during replay.

    Pure function: deterministic given the event.
    """
    rid = reducer_id_for(event.event_type)
    pat = invariant_pattern_for(event.event_type)
    tid = compute_transition_id(event.event_type, rid, pat)
    lane = LaneScheduler.route(event)
    eid = _entity_id(event)
    okey = _sort_key_to_str(event)
    return TraceEntry(
        event_id=event.event_id,
        lane=lane.name,
        reducer_id=rid,
        transition_id=tid,
        entity_id=eid,
        ordering_key=okey,
    )


__all__ = [
    "TraceEntry",
    "compute_transition_id",
    "invariant_pattern_for",
    "make_trace_entry",
    "reducer_id_for",
]
