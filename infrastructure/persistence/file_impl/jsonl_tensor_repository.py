from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Generator

from infrastructure.persistence.dto.tensor_dto import EdgeComponentDTO, EdgeTensorDTO


class JsonlTensorRepository:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def store_tensor(self, tensor: EdgeTensorDTO) -> None:
        record = {
            "type": "edge_tensor",
            "experiment_id": tensor.experiment_id,
            "timestamp": tensor.timestamp,
            "symbol": tensor.symbol,
            "n_trades": tensor.n_trades,
            "directional": {
                "mean": tensor.directional.mean,
                "std": tensor.directional.std,
                "ci_lower": tensor.directional.ci_lower,
                "ci_upper": tensor.directional.ci_upper,
                "identifiable": tensor.directional.identifiable,
            },
            "timing": {
                "mean": tensor.timing.mean,
                "std": tensor.timing.std,
                "ci_lower": tensor.timing.ci_lower,
                "ci_upper": tensor.timing.ci_upper,
                "identifiable": tensor.timing.identifiable,
            },
            "execution": {
                "mean": tensor.execution.mean,
                "std": tensor.execution.std,
                "ci_lower": tensor.execution.ci_lower,
                "ci_upper": tensor.execution.ci_upper,
                "identifiable": tensor.execution.identifiable,
            },
            "structural": {
                "mean": tensor.structural.mean,
                "std": tensor.structural.std,
                "ci_lower": tensor.structural.ci_lower,
                "ci_upper": tensor.structural.ci_upper,
                "identifiable": tensor.structural.identifiable,
            },
        }
        self._atomic_append(record)

    def get_tensor_series(self, experiment_id: str) -> list[EdgeTensorDTO]:
        return [
            self._record_to_dto(r)
            for r in self._iter_records()
            if r.get("experiment_id") == experiment_id
        ]

    def replay(self) -> list[EdgeTensorDTO]:
        return [self._record_to_dto(r) for r in self._iter_records()]

    def _iter_records(self) -> Generator[dict, None, None]:
        if not self._path.exists():
            return
        with open(self._path) as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def _render_component(self, data: dict) -> EdgeComponentDTO:
        return EdgeComponentDTO(
            mean=data.get("mean", 0.0),
            std=data.get("std", 0.0),
            ci_lower=data.get("ci_lower", 0.0),
            ci_upper=data.get("ci_upper", 0.0),
            identifiable=data.get("identifiable", False),
        )

    def _record_to_dto(self, record: dict) -> EdgeTensorDTO:
        return EdgeTensorDTO(
            experiment_id=record.get("experiment_id", ""),
            timestamp=record.get("timestamp", ""),
            symbol=record.get("symbol", ""),
            n_trades=record.get("n_trades", 0),
            directional=self._render_component(record.get("directional", {})),
            timing=self._render_component(record.get("timing", {})),
            execution=self._render_component(record.get("execution", {})),
            structural=self._render_component(record.get("structural", {})),
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
