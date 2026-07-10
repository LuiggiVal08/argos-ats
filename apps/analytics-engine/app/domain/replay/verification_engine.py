"""Verification Engine — F3 projection integrity verification.

EMC v1 §10.6:
  - One-shot verification: replay + hash comparison
  - Full equivalence: full replay vs snapshot + incremental replay
  - VerificationReport: state_match, trace_match, structural_match, deployment_match
  - Two-level fingerprint: structural vs deployment (§10.3)

The verification engine does NOT trust snapshots — it re-executes
the replay and compares the projection hash against the expected value.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...application.ports.event_store import EventStore
from ...domain.replay.event_sourced_replay import (
    EventSourcedReplay,
    ReplayResult,
)
from ...domain.replay.execution_trace import TraceEntry
from ...domain.replay.projection_hasher import compute_projection_hash
from ...domain.replay.projection_metadata import (
    DeploymentFingerprint,
    StructuralFingerprint,
)
from ...domain.replay.replay_planner import SnapshotMetadata
from ...domain.entities.replay_state import OutputState


@dataclass(frozen=True)
class VerificationReport:
    """Result of comparing full replay vs snapshot + incremental replay.

    The report exposes three levels of truth (F4 Verification Policy Matrix):

      1. HARD TRUTH — state_match
         State equivalence. The system state is byte-identical.
         Required for ANY operational decision. Non-negotiable.

      2. SEMANTIC TRUTH — structural_match
         Execution logic equivalence (routing, reducer, sort key, schema).
         Same system → same results given same inputs.
         Required for correctness.

      3. DEPLOYMENT DRIFT — deployment_match
         Version metadata equivalence (build hash, version strings).
         Different builds of the same logic produce structural_match=True
         but deployment_match=False. This is EXPECTED in deploys.

    Properties:
      is_correct:            state_match AND structural_match
                             (semantic truth, ignores deployment drift)
      is_equivalent:         state_match AND trace_match
                             AND structural_match AND deployment_match
                             (strongest invariant — identical execution)
      is_operationally_safe: is_correct AND (deployment_match OR
                             allowed_deployment_drift)
                             (policy-level: can the system continue?)
      full_hash_matches:     projection_hash == expected_hash (one-shot)
    """
    full_hash: str
    snap_hash: str
    state_match: bool
    trace_match: bool
    structural_match: bool
    deployment_match: bool

    @property
    def is_correct(self) -> bool:
        """Semantic truth — same state AND same execution logic.

        Deployment drift is irrelevant for correctness. Two different
        builds of the same code produce is_correct=True.
        """
        return self.state_match and self.structural_match

    @property
    def is_equivalent(self) -> bool:
        """Strongest invariant — identical execution in every dimension.

        Includes deployment_match. This will fail across deploys
        (different build_hash) even if the system is semantically correct.
        Use is_correct for semantic truth.
        """
        return (
            self.state_match
            and self.trace_match
            and self.structural_match
            and self.deployment_match
        )

    @property
    def is_operationally_safe(self) -> bool:
        """Policy-level: can the system continue operating?

        Default policy: is_correct AND (deployment_match is NOT fatal).
        Deployment drift alone never triggers operational concern.
        This property is overridable via the VerificationPolicyLayer (F4).
        """
        # Deployment mismatch alone is NEVER a safety concern.
        # Only structural or state mismatches represent semantic drift.
        return self.is_correct

    @property
    def full_hash_matches(self) -> bool:
        """True if the snapshot projection hash matches the full hash."""
        return self.full_hash == self.snap_hash


class VerificationEngine:
    """Projection integrity verification.

    Does NOT trust snapshots — each verification re-executes replay.
    """

    @staticmethod
    async def verify(
        event_store: EventStore,
        expected_hash: str,
        mode: str = "STRICT",
        snapshot: SnapshotMetadata | None = None,
        initial_state: OutputState | None = None,
    ) -> bool:
        """One-shot verification (EMC v1 §10.6).

        Returns True if the projection hash matches the expected value.
        This detects:
          - State divergence (silent corruption)
          - Trace divergence (different execution path)
          - Structural fingerprint mismatch (semantic drift)
          - Deployment fingerprint mismatch (version diff)
        """
        engine = EventSourcedReplay()
        result = await engine.replay(
            event_store,
            mode=mode,
            snapshot=snapshot,
            initial_state=initial_state,
        )
        if not result.success:
            return False
        actual_hash = compute_projection_hash(
            result.state, result.trace,
            result.structural_fingerprint, result.deployment_fingerprint,
        )
        return actual_hash == expected_hash

    @staticmethod
    async def full_equivalence(
        event_store: EventStore,
        mode: str = "STRICT",
        snapshot: SnapshotMetadata | None = None,
        initial_state: OutputState | None = None,
    ) -> VerificationReport:
        """Full equivalence check (EMC v1 §10.6).

        Compares:
          - full replay hash vs snapshot incremental replay hash
          - state, trace, structural fingerprint, and deployment fingerprint individually

        Use for property tests and audit-mode validation.
        """
        engine = EventSourcedReplay()

        # Full replay
        full_result = await engine.replay(
            event_store, mode=mode,
        )
        if not full_result.success:
            return VerificationReport(
                full_hash="ERROR",
                snap_hash="ERROR",
                state_match=False,
                trace_match=False,
                structural_match=False,
                deployment_match=False,
            )

        # Snapshot-based replay (partial)
        snap_result = await engine.replay(
            event_store, mode=mode,
            snapshot=snapshot,
            initial_state=initial_state,
        )

        full_hash = compute_projection_hash(
            full_result.state, full_result.trace,
            full_result.structural_fingerprint, full_result.deployment_fingerprint,
        )
        snap_hash = compute_projection_hash(
            snap_result.state, snap_result.trace,
            snap_result.structural_fingerprint, snap_result.deployment_fingerprint,
        )

        return VerificationReport(
            full_hash=full_hash,
            snap_hash=snap_hash,
            state_match=full_result.state == snap_result.state,
            trace_match=full_result.trace == snap_result.trace,
            structural_match=(
                full_result.structural_fingerprint == snap_result.structural_fingerprint
            ),
            deployment_match=(
                full_result.deployment_fingerprint == snap_result.deployment_fingerprint
            ),
        )


__all__ = [
    "VerificationEngine",
    "VerificationReport",
]
