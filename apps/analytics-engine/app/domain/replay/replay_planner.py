"""ReplayPlanner — determina desde dónde empezar el replay.

Responsabilidades:
  1. Validar snapshots (version, hash, event_index, contamination).
  2. Detectar contaminación por late events.
  3. Calcular el punto de inicio del replay.

Policy: Full rewind (EMC v1 §7.7.1).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SnapshotMetadata:
    """Metadata que todo snapshot debe incluir.

    Fields:
      version: Version string (MAJOR.MINOR), e.g. "1.0".
      event_index: Índice del último evento incluido en el snapshot.
      last_event_id: event_id del último evento incluido.
      last_event_hash: SHA-256 del event_id del último evento.
      last_event_time: event_time ISO del último evento incluido.
    """
    version: str
    event_index: int
    last_event_id: str
    last_event_hash: str
    last_event_time: str


@dataclass(frozen=True)
class PlannerDecision:
    """Resultado del ReplayPlanner: desde dónde replay.

    Fields:
      snapshot: El snapshot elegido como base (None = replay completo).
      from_index: event_index desde el cual empezar a replay.
      snapshots_invalidated: Cuántos snapshots se invalidaron.
      reason: Explicación de la decisión.
    """
    snapshot: SnapshotMetadata | None = None
    from_index: int = 0
    snapshots_invalidated: int = 0
    reason: str = ""

    @property
    def is_full_replay(self) -> bool:
        return self.snapshot is None


class ReplayPlanner:
    """Valida snapshots y decide desde dónde replay.

    Uso:
        planner = ReplayPlanner()
        decision = planner.plan(
            current_version="1.0",
            snapshot=snapshot_meta,
            late_event_time="2026-06-18T12:00:00",
            max_store_index=50000,
        )
    """

    @staticmethod
    def plan(
        current_version: str,
        max_store_index: int,
        snapshot: SnapshotMetadata | None = None,
        late_event_time: str | None = None,
    ) -> PlannerDecision:
        """Calcular el punto de inicio del replay.

        Args:
            current_version: Version actual del código de proyección.
            max_store_index: Máximo event_index en la store.
            snapshot: Metadata del snapshot candidato (None = no hay snapshot).
            late_event_time: event_time del late event más antiguo (None = no hay).

        Returns:
            PlannerDecision con from_index y snapshot a usar.
        """
        if snapshot is None:
            return PlannerDecision(
                from_index=0,
                reason="no snapshot available — full replay from 0",
            )

        # 1. Validar que el snapshot tenga metadata completa
        missing = ReplayPlanner._validate_metadata(snapshot)
        if missing:
            return PlannerDecision(
                from_index=0,
                reason=f"snapshot rejected — missing metadata: {missing}",
            )

        # 2. Validar version MAJOR match
        if not ReplayPlanner._version_compatible(current_version, snapshot.version):
            return PlannerDecision(
                from_index=0,
                reason=(
                    f"snapshot version {snapshot.version} incompatible with "
                    f"current {current_version} — full replay"
                ),
            )

        # 3. Validar event_index contra store
        if snapshot.event_index > max_store_index:
            return PlannerDecision(
                from_index=0,
                reason=(
                    f"snapshot event_index {snapshot.event_index} > "
                    f"store max {max_store_index} — corruption, full replay"
                ),
            )

        # 4. Detectar contaminación por late event
        if late_event_time and snapshot.last_event_time > late_event_time:
            return PlannerDecision(
                snapshot=snapshot,
                from_index=snapshot.event_index + 1,
                snapshots_invalidated=1,
                reason=(
                    f"snapshot contaminated (last_event_time="
                    f"{snapshot.last_event_time} > late_event_time="
                    f"{late_event_time}) — replay from index "
                    f"{snapshot.event_index + 1}"
                ),
            )

        # 5. Snapshot válido + sin contaminación
        return PlannerDecision(
            snapshot=snapshot,
            from_index=snapshot.event_index + 1,
            reason=(
                f"valid snapshot at index {snapshot.event_index} — "
                f"replaying from {snapshot.event_index + 1}"
            ),
        )

    @staticmethod
    def _validate_metadata(snapshot: SnapshotMetadata) -> list[str]:
        """Check that snapshot has all required metadata fields."""
        missing: list[str] = []
        if not snapshot.version:
            missing.append("version")
        if not snapshot.last_event_id:
            missing.append("last_event_id")
        if not snapshot.last_event_hash:
            missing.append("last_event_hash")
        if not snapshot.last_event_time:
            missing.append("last_event_time")
        if snapshot.event_index < 0:
            missing.append("event_index (negative)")
        return missing

    @staticmethod
    def _version_compatible(current: str, snapshot: str) -> bool:
        """Check MAJOR version compatibility.

        Returns True if major versions match.
        """
        try:
            current_major = current.split(".")[0]
            snapshot_major = snapshot.split(".")[0]
            return current_major == snapshot_major
        except (IndexError, ValueError):
            return False
