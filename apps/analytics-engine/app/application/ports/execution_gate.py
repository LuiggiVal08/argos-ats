from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ...domain.value_objects.execution_signal import ExecutionSignal


@dataclass(frozen=True)
class GateVerdict:
    approved: bool
    reason: str = ""
    kill_switch_state: str = "NORMAL"


@runtime_checkable
class ExecutionGate(Protocol):

    async def evaluate(self, signal: ExecutionSignal) -> GateVerdict:
        ...
