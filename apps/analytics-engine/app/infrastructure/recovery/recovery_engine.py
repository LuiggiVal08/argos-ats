"""Recovery Engine — boot-time state reconstruction.

Loads checkpoint + SQLite truth store replay, reconciles both,
and produces a consistent initial state for the analytics-engine.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..state.checkpoint_reader import CheckpointReader

log = logging.getLogger(__name__)


@dataclass
class RecoveryState:
    last_processed_timestamp: int = 0
    last_candle_timestamp: int = 0
    last_signal_id: str | None = None
    last_order_id: str | None = None
    last_fill_id: str | None = None
    open_positions: list[dict[str, Any]] = field(default_factory=list)
    equity: float = 5000.0
    mode: str = "BACKTESTING"
    is_resume: bool = False


@dataclass
class TruthStoreReplay:
    last_signal_id: str | None = None
    last_order_id: str | None = None
    last_fill_id: str | None = None
    signal_count: int = 0
    order_count: int = 0
    fill_count: int = 0


def _fresh_start(mode: str) -> RecoveryState:
    return RecoveryState(mode=mode, is_resume=False)


class RecoveryEngine:
    """Boot recovery: checkpoint → truth store → reconciled state."""

    def __init__(self, checkpoint_path: str, mode: str) -> None:
        self._reader = CheckpointReader(checkpoint_path)
        self._mode = mode

    def restore(
        self,
        resume_mode: bool,
        replay: TruthStoreReplay | None = None,
    ) -> RecoveryState:
        if not resume_mode:
            return _fresh_start(self._mode)

        cp = self._reader.read_with_fallback({})

        if not cp or cp.get("schema_version") != 1:
            return _fresh_start(self._mode)

        engine = cp.get("engine", "")
        if engine != "analytics-engine":
            return _fresh_start(self._mode)

        last_signal_id = cp.get("last_signal_id")
        last_order_id = cp.get("last_order_id")
        last_fill_id = cp.get("last_fill_id")

        # Truth store reconciliation: replay wins over checkpoint
        if replay is not None:
            if replay.last_signal_id is not None:
                last_signal_id = replay.last_signal_id
            if replay.last_order_id is not None:
                last_order_id = replay.last_order_id
            if replay.last_fill_id is not None:
                last_fill_id = replay.last_fill_id

        positions_raw = cp.get("open_positions", "[]")
        if isinstance(positions_raw, str):
            try:
                positions = json.loads(positions_raw)
            except (json.JSONDecodeError, TypeError):
                positions = []
        else:
            positions = positions_raw if isinstance(positions_raw, list) else []

        return RecoveryState(
            last_processed_timestamp=cp.get("last_processed_timestamp", 0),
            last_candle_timestamp=cp.get("last_candle_timestamp", 0),
            last_signal_id=last_signal_id,
            last_order_id=last_order_id,
            last_fill_id=last_fill_id,
            open_positions=positions,
            equity=cp.get("equity", 5000.0),
            mode=cp.get("mode", self._mode),
            is_resume=True,
        )

    def should_process_signal(self, signal_id: str, state: RecoveryState) -> bool:
        if not state.is_resume:
            return True
        if state.last_signal_id is None:
            return True
        return signal_id > state.last_signal_id

    def should_process_order(self, order_id: str, state: RecoveryState) -> bool:
        if not state.is_resume:
            return True
        if state.last_order_id is None:
            return True
        return order_id > state.last_order_id

    def should_process_fill(self, fill_id: str, state: RecoveryState) -> bool:
        if not state.is_resume:
            return True
        if state.last_fill_id is None:
            return True
        return fill_id > state.last_fill_id

    def is_candle_before_checkpoint(self, candle_ts: int, state: RecoveryState) -> bool:
        if not state.is_resume:
            return False
        if state.last_candle_timestamp == 0:
            return False
        return candle_ts <= state.last_candle_timestamp
