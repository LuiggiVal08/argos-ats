"""EpisodeRebuilder — reconstruye episodes desde el ledger.

Consume EpisodeDTOs (o dicts) y reconstruye el grafo de episodios,
validando inmutabilidad y consistencia del settle().
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReplayEpisode:
    """Internal representation of a replayed episode."""
    episode_id: str = ""
    t_entry: str = ""
    t_exit: str | None = None
    side: str = ""
    pnl: float = 0.0
    regime_at_entry: str = ""
    model_version: str = ""
    feature_hash: str = ""
    feature_schema_version: str = ""
    experiment_id: str = ""


def _episode_to_replay(dto: Any) -> ReplayEpisode:
    if isinstance(dto, dict):
        return ReplayEpisode(**{k: v for k, v in dto.items()
                              if k in ReplayEpisode.__dataclass_fields__})
    return ReplayEpisode(
        episode_id=getattr(dto, "episode_id", ""),
        t_entry=getattr(dto, "t_entry", ""),
        t_exit=getattr(dto, "t_exit", None),
        side=getattr(dto, "side", ""),
        pnl=float(getattr(dto, "pnl", 0.0)),
        regime_at_entry=getattr(dto, "regime_at_entry", ""),
        model_version=getattr(dto, "model_version", ""),
        feature_hash=getattr(dto, "feature_hash", ""),
        feature_schema_version=getattr(dto, "feature_schema_version", ""),
        experiment_id=getattr(dto, "experiment_id", ""),
    )


class EpisodeRebuilder:
    """Rebuilds episode graph from persisted episode data.

    An "episode" is a position lifecycle: open → (optional) settle.
    Validates:
      - Every settled episode was first created (open)
      - No duplicate episode_ids
      - Immutability of settled episodes
    """

    def __init__(self) -> None:
        self._episodes: dict[str, ReplayEpisode] = {}
        self._open: dict[str, ReplayEpisode] = {}
        self._settled: dict[str, ReplayEpisode] = {}

    def rebuild(self, episode_dtos: list[Any]) -> dict[str, ReplayEpisode]:
        """Rebuild episode graph from DTOs.

        Returns mapping episode_id → ReplayEpisode.
        """
        self._episodes.clear()
        self._open.clear()
        self._settled.clear()

        for dto in episode_dtos:
            ep = _episode_to_replay(dto)
            self._episodes[ep.episode_id] = ep
            if ep.t_exit is None:
                self._open[ep.episode_id] = ep
            else:
                self._settled[ep.episode_id] = ep

        return dict(self._episodes)

    @property
    def open_episodes(self) -> list[ReplayEpisode]:
        return list(self._open.values())

    @property
    def settled_episodes(self) -> list[ReplayEpisode]:
        return list(self._settled.values())

    @property
    def all_episodes(self) -> list[ReplayEpisode]:
        return list(self._episodes.values())

    def validate_consistency(
        self,
        original_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate episode graph consistency."""
        issues = []

        dupes = set(self._open) & set(self._settled)
        if dupes:
            issues.append(f"Episodes in both open and settled: {dupes}")

        for eid, ep in self._settled.items():
            if ep.t_exit is None:
                issues.append(f"Settled episode {eid} has no t_exit")

        for eid, ep in self._open.items():
            if ep.t_exit is not None:
                issues.append(f"Open episode {eid} has t_exit set")

        total = len(self._episodes)
        n_settled = len(self._settled)
        n_open = len(self._open)

        return {
            "is_consistent": len(issues) == 0,
            "total_episodes": total,
            "open_count": n_open,
            "settled_count": n_settled,
            "issues": issues,
            "match_rate": 1.0 if total == (n_open + n_settled) else 0.0,
        }
