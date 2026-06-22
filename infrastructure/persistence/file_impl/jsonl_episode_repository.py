from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Generator

from infrastructure.persistence.dto.episode_dto import EpisodeDTO


class JsonlEpisodeRepository:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append_episode(self, episode: EpisodeDTO) -> None:
        record = {
            "type": "episode",
            "episode_id": episode.episode_id,
            "t_entry": episode.t_entry,
            "t_exit": episode.t_exit,
            "side": episode.side,
            "pnl": episode.pnl,
            "regime_at_entry": episode.regime_at_entry,
            "model_version": episode.model_version,
            "feature_hash": episode.feature_hash,
            "feature_schema_version": episode.feature_schema_version,
            "experiment_id": episode.experiment_id,
            "_settled": episode.t_exit is not None,
        }
        self._atomic_append(record)

    def settle_episode(self, episode_id: str, pnl: float, t_exit: str) -> None:
        records = list(self._iter_records())
        found = False
        for i, rec in enumerate(records):
            if rec.get("episode_id") == episode_id:
                records[i]["t_exit"] = t_exit
                records[i]["pnl"] = pnl
                records[i]["_settled"] = True
                found = True
                break
        if not found:
            raise ValueError(f"episode {episode_id} not found")
        self._rewrite_all(records)

    def get_open_episodes(self) -> list[EpisodeDTO]:
        return [
            self._record_to_dto(r)
            for r in self._iter_records()
            if not r.get("_settled", False)
        ]

    def get_settled_episodes(self) -> list[EpisodeDTO]:
        return [
            self._record_to_dto(r)
            for r in self._iter_records()
            if r.get("_settled", False)
        ]

    def replay(self) -> list[EpisodeDTO]:
        return [self._record_to_dto(r) for r in self._iter_records()]

    def _iter_records(self) -> Generator[dict, None, None]:
        if not self._path.exists():
            return
        with open(self._path) as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def _record_to_dto(self, record: dict) -> EpisodeDTO:
        return EpisodeDTO(
            episode_id=record.get("episode_id", ""),
            t_entry=record.get("t_entry", ""),
            t_exit=record.get("t_exit"),
            side=record.get("side", ""),
            pnl=record.get("pnl", 0.0),
            regime_at_entry=record.get("regime_at_entry", ""),
            model_version=record.get("model_version", ""),
            feature_hash=record.get("feature_hash", ""),
            feature_schema_version=record.get("feature_schema_version", ""),
            experiment_id=record.get("experiment_id", ""),
        )

    def _atomic_append(self, record: dict) -> None:
        line = json.dumps(record, sort_keys=True) + "\n"
        if self._path.exists():
            with open(self._path, "a") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
        else:
            tmp = self._path.with_suffix(".tmp")
            with open(tmp, "w") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
            tmp.rename(self._path)

    def _rewrite_all(self, records: list[dict]) -> None:
        tmp = self._path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            for rec in records:
                f.write(json.dumps(rec, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        tmp.rename(self._path)
