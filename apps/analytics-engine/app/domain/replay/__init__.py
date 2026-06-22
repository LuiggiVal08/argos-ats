"""Replay Engine — spec Sections 15–19.

Deterministic replay of forward test experiments from persisted ledger.

This module is the first step toward:
    "total auditability of the trading system as a scientific experiment"

It is NOT optimization. It is reality verification of the system.

Rules:
    - NO access to live model
    - NO new decisions generated
    - NO stochasticity introduced
    - Only reconstruction or validation of the past
"""

from .event_stream import Event, EventStream, EventType
from .replay_engine import ReplayEngine
from .replay_state import ReplayState
from .reconstruction import (
    ControlRebuilder,
    EpisodeRebuilder,
    TensorRebuilder,
    TradeRebuilder,
)
from .validation import DriftDetector, ReplayConsistencyReport

__version__ = "0.1.0"

__all__ = [
    "ReplayEngine",
    "ReplayState",
    "EventStream",
    "Event",
    "EventType",
    "TradeRebuilder",
    "EpisodeRebuilder",
    "TensorRebuilder",
    "ControlRebuilder",
    "ReplayConsistencyReport",
    "DriftDetector",
]
