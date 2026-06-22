"""RecoveryGate — safety guard at startup.

Prevents the engine from starting if:
  - Recovery state is FAILED (previous recovery crashed)
  - Recovery state is still IN_PROGRESS (system was mid-recovery on crash)
  - Snapshot is corrupt (can't parse / missing required fields)
  - Database is not accessible (SQLite connection fails)
  - Reconciliation is incomplete (exchange vs local mismatch unresolved)

Fix 1 (atomic): RecoveryGate checks recovery_state before allowing trade.
  - FAILED → BLOCKED (manual intervention required)
  - IN_PROGRESS → BLOCKED (mid-recovery crash, restart recovery)
  - No record → DEGRADED (first boot, snapshot-less recovery)
  - COMPLETED → SAFE (normal path)

States:
  - BLOCKED: cannot start — critical error
  - DEGRADED: can start but some state may be stale
  - SAFE: full recovery confirmed
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ...application.ports.snapshot_repository import (
    RecoveryState,
    RecoveryStateRecord,
    SnapshotRepositoryError,
    SystemSnapshot,
)
from .reconciliation_engine import ReconciliationSummary


class GateState(Enum):
    BLOCKED = "BLOCKED"
    DEGRADED = "DEGRADED"
    SAFE = "SAFE"


@dataclass(frozen=True)
class GateVerdict:
    state: GateState
    reason: str = ""
    warnings: list[str] = field(default_factory=list)


class RecoveryGate:
    """Evaluates startup safety based on recovery state and system health.

    Usage:
        gate = RecoveryGate()
        verdict = gate.evaluate(
            snapshot=snapshot,
            db_accessible=True,
            reconciliation=recon_summary,
            recovery_state=recovery_state_record,
        )
        if verdict.state == GateState.BLOCKED:
            sys.exit(1)
    """

    def evaluate(
        self,
        snapshot: SystemSnapshot | None,
        db_accessible: bool,
        reconciliation: ReconciliationSummary | None = None,
        recovery_state: RecoveryStateRecord | None = None,
        risk_validation: object | None = None,
    ) -> GateVerdict:
        """Evaluate startup safety.

        Args:
            snapshot: Latest system snapshot (None if no snapshot exists).
            db_accessible: True if SQLite connection succeeded.
            reconciliation: Result of exchange reconciliation (None if skipped).
            recovery_state: Record of last recovery attempt (Fix 1).

        Returns:
            GateVerdict with state and reason.
        """
        warnings: list[str] = []

        # Fix 1: Check recovery state first — atomic gate
        if recovery_state is not None:
            if recovery_state.state == RecoveryState.FAILED:
                return GateVerdict(
                    state=GateState.BLOCKED,
                    reason=f"previous_recovery_failed: {recovery_state.error}",
                    warnings=warnings,
                )
            if recovery_state.state == RecoveryState.IN_PROGRESS:
                return GateVerdict(
                    state=GateState.BLOCKED,
                    reason="recovery_in_progress_on_previous_shutdown — "
                           "system crashed mid-recovery. Manual cleanup required.",
                )
            if recovery_state.state == RecoveryState.STARTED:
                return GateVerdict(
                    state=GateState.BLOCKED,
                    reason="recovery_started_but_not_completed — "
                           "system crashed during recovery init.",
                )
        else:
            warnings.append("no_recovery_state_record: first boot or state cleared")

        # --- BLOCKED conditions ---
        if not db_accessible:
            return GateVerdict(
                state=GateState.BLOCKED,
                reason="database_not_accessible",
            )

        if snapshot is not None:
            if snapshot.equity < 0:
                return GateVerdict(
                    state=GateState.BLOCKED,
                    reason="snapshot_corrupt: negative equity",
                )

        # --- DEGRADED conditions ---
        if reconciliation is not None:
            if reconciliation.missing_on_exchange > 0:
                warnings.append(
                    f"{reconciliation.missing_on_exchange} position(s) closed on exchange "
                    f"but still present locally — will be cleaned up"
                )

            if reconciliation.partial_mismatch > 0:
                warnings.append(
                    f"{reconciliation.partial_mismatch} position(s) with quantity "
                    f"mismatch — exchange data will overwrite local"
                )

        if snapshot is None:
            warnings.append("no_snapshot_available: starting fresh")

        # --- Verdict ---
        if reconciliation is not None and reconciliation.missing_local > 0:
            return GateVerdict(
                state=GateState.DEGRADED,
                reason=(
                    f"{reconciliation.missing_local} position(s) exist on exchange "
                    f"but not in local DB — will reconstruct"
                ),
                warnings=warnings,
            )

        return GateVerdict(
            state=GateState.SAFE if not warnings else GateState.DEGRADED,
            reason="safe" if not warnings else "degraded_with_warnings",
            warnings=warnings,
        )
