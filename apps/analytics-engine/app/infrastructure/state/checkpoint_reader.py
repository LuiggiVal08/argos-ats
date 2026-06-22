"""Checkpoint reader — loads and validates a checkpoint from disk."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, TypeVar

log = logging.getLogger(__name__)


class CheckpointReadError(RuntimeError):
    pass


T = TypeVar("T")


class CheckpointReader:
    """Reads a checkpoint file with schema validation and fallback."""

    def __init__(self, file_path: str) -> None:
        self._path = Path(file_path)

    def exists(self) -> bool:
        return self._path.exists()

    def read(self) -> dict[str, Any]:
        if not self.exists():
            raise CheckpointReadError(f"checkpoint not found: {self._path}")
        try:
            raw = self._path.read_text(encoding="utf-8")
            data: dict[str, Any] = json.loads(raw)
            return data
        except (json.JSONDecodeError, OSError) as exc:
            raise CheckpointReadError(
                f"failed to read checkpoint: {exc}"
            ) from exc

    def read_with_fallback(self, fallback: dict[str, Any]) -> dict[str, Any]:
        if not self.exists():
            return fallback
        try:
            return self.read()
        except CheckpointReadError:
            return fallback
