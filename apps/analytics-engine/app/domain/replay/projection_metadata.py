"""Projection Fingerprint — two-level execution context of a replay.

EMC v1 §10.3: The fingerprint splits into StructuralFingerprint
(semantics of execution) and DeploymentFingerprint (version metadata).

StructuralFingerprint changes indicate potential semantic drift — the
same event stream may produce different results under different routing
or reducer logic. This can trigger DEGRADED.

DeploymentFingerprint changes are benign version metadata — different
builds of the same logic produce the same structural fingerprint.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StructuralFingerprint:
    """Execution semantics: changes here MAY alter system behavior.

    Computed from actual logic (routing tables, dispatch tables, sort key
    formulas, schema semantics), not from version strings.

    All fields are SHA-256 hex digests (32 chars) of the component logic.
    """

    lane_scheduler_routing_hash: str
    reducer_dispatch_hash: str
    sort_key_spec_hash: str
    schema_semantics_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane_scheduler_routing_hash": self.lane_scheduler_routing_hash,
            "reducer_dispatch_hash": self.reducer_dispatch_hash,
            "sort_key_spec_hash": self.sort_key_spec_hash,
            "schema_semantics_hash": self.schema_semantics_hash,
        }


@dataclass(frozen=True)
class DeploymentFingerprint:
    """Version metadata: changes here do NOT affect execution semantics.

    These are human-readable version strings and build identifiers.
    Two different builds of the same code have identical
    StructuralFingerprint but differ in DeploymentFingerprint.
    """

    lane_scheduler_version: str
    reducer_versions: dict[str, str]
    join_composer_version: str
    build_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane_scheduler_version": self.lane_scheduler_version,
            "reducer_versions": dict(self.reducer_versions),
            "join_composer_version": self.join_composer_version,
            "build_hash": self.build_hash,
        }


# ── Static constants for structural fingerprint ──────────────────
# These are SHA-256 hashes of the actual routing/dispatch/sort/schema
# logic, NOT version strings. They change only when the semantics change.

# Lane scheduler routing table: route(event_type) → lane
# Current mapping:
#   RiskStateChanged → GLOBAL
#   RiskEvaluated → GLOBAL
#   RiskLimitBreached → GLOBAL
#   SignalGenerated → ENTITY
#   SignalRejected → ENTITY
#   PositionOpened → ENTITY
#   PositionUpdated → ENTITY
#   PositionClosed → ENTITY
#   SimulationEvaluated → ENTITY
#   MonteCarloCompleted → GLOBAL
#   ConfigChanged → GLOBAL
_LANE_ROUTING_TABLE = (
    "RiskStateChanged:GLOBAL|RiskEvaluated:GLOBAL|"
    "RiskLimitBreached:GLOBAL|SignalGenerated:ENTITY|"
    "SignalRejected:ENTITY|PositionOpened:ENTITY|"
    "PositionUpdated:ENTITY|PositionClosed:ENTITY|"
    "SimulationEvaluated:ENTITY|MonteCarloCompleted:GLOBAL|"
    "ConfigChanged:GLOBAL|default:GLOBAL"
)
LANE_SCHEDULER_ROUTING_HASH = hashlib.sha256(
    _LANE_ROUTING_TABLE.encode("utf-8"),
).hexdigest()[:32]

# Reducer dispatch table: _REDUCERS mapping
# Current mapping matches reducer_id_for in execution_trace.py
_REDUCER_DISPATCH_TABLE = (
    "PositionOpened:PositionReducer"
    "|PositionUpdated:PositionReducer"
    "|PositionClosed:PositionReducer"
    "|RiskEvaluated:RiskReducer"
    "|RiskLimitBreached:RiskReducer"
    "|RiskStateChanged:RiskReducer"
    "|SignalGenerated:SignalReducer"
    "|SignalRejected:SignalReducer"
    "|SimulationEvaluated:SimulationReducer"
    "|MonteCarloCompleted:SimulationReducer"
    "|ConfigChanged:ConfigReducer"
    "|default:UnknownReducer"
)
REDUCER_DISPATCH_HASH = hashlib.sha256(
    _REDUCER_DISPATCH_TABLE.encode("utf-8"),
).hexdigest()[:32]

# SHA-256 of the sort key specification
# Current spec: (entity_id, stream_priority, timestamp_bucket, timestamp, event_id)
_SORT_KEY_SPEC = (
    "ORDER BY entity_id, stream_priority, timestamp_bucket, timestamp, event_id"
)
SORT_KEY_SPEC_HASH = hashlib.sha256(
    _SORT_KEY_SPEC.encode("utf-8"),
).hexdigest()[:32]

# SHA-256 of the canonical event schemas (from EMC v1 §2)
# This captures the semantic interpretation of each event field.
_EVENT_SCHEMAS_CANONICAL = (
    "PositionOpened:position_id,symbol,side,entry_price,quantity,sl_price?,tp_price?"
    "|PositionUpdated:position_id,sl_price?,tp_price?"
    "|PositionClosed:position_id,close_price,pnl,close_reason,hold_bars"
    "|RiskEvaluated:drawdown_pct,daily_pnl,current_balance,starting_balance"
    "|RiskLimitBreached:limit_type,threshold,current_value"
    "|RiskStateChanged:old_status,new_status,reason"
    "|SignalGenerated:symbol,side,confidence"
    "|SignalRejected:symbol,side,confidence,reason"
    "|SimulationEvaluated:symbol,p_failure,n_failed,n_total"
    "|MonteCarloCompleted:symbol,confidence_interval,sharpe,max_drawdown"
    "|ConfigChanged:key,old_value,new_value"
)
SCHEMA_SEMANTICS_HASH = hashlib.sha256(
    _EVENT_SCHEMAS_CANONICAL.encode("utf-8"),
).hexdigest()[:32]


# ── Static constants for deployment fingerprint ──────────────────
# These are human-readable version strings, not logic hashes.

LANE_SCHEDULER_VERSION = "v1"

REDUCER_VERSIONS: dict[str, str] = {
    "Position": "v1",
    "Risk": "v1",
    "Signal": "v1",
    "Simulation": "v1",
    "Config": "v1",
}

JOIN_COMPOSER_VERSION = "v1"

BUILD_HASH = "dev-local"


# ── Factory ──────────────────────────────────────────────────────


def build_structural_fingerprint() -> StructuralFingerprint:
    """Build current structural fingerprint from static logic hashes.

    Pure function: always returns the same value for the same code version.
    There is exactly one structural fingerprint per execution logic version.
    """
    return StructuralFingerprint(
        lane_scheduler_routing_hash=LANE_SCHEDULER_ROUTING_HASH,
        reducer_dispatch_hash=REDUCER_DISPATCH_HASH,
        sort_key_spec_hash=SORT_KEY_SPEC_HASH,
        schema_semantics_hash=SCHEMA_SEMANTICS_HASH,
    )


def build_deployment_fingerprint() -> DeploymentFingerprint:
    """Build current deployment fingerprint from version constants.

    Pure function: always returns the same value for the same build.
    Two builds of identical code have the same deployment fingerprint
    only if they share the same BUILD_HASH.
    """
    return DeploymentFingerprint(
        lane_scheduler_version=LANE_SCHEDULER_VERSION,
        reducer_versions=dict(REDUCER_VERSIONS),
        join_composer_version=JOIN_COMPOSER_VERSION,
        build_hash=BUILD_HASH,
    )


def build_fingerprints() -> tuple[StructuralFingerprint, DeploymentFingerprint]:
    """Convenience: build both fingerprints in one call."""
    return build_structural_fingerprint(), build_deployment_fingerprint()


__all__ = [
    "StructuralFingerprint",
    "DeploymentFingerprint",
    "build_structural_fingerprint",
    "build_deployment_fingerprint",
    "build_fingerprints",
]
