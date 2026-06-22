from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EpisodeDTO:
    episode_id: str
    t_entry: str
    t_exit: str | None = None
    side: str = ""
    pnl: float = 0.0
    regime_at_entry: str = ""
    model_version: str = ""
    feature_hash: str = ""
    feature_schema_version: str = ""
    experiment_id: str = ""
