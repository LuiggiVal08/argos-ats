from __future__ import annotations

from typing import Protocol, runtime_checkable

from ...domain.value_objects.execution_signal import ExecutionSignal
from ...domain.value_objects.execution_report import ExecutionReport


@runtime_checkable
class TradingEngine(Protocol):

    async def execute(self, signal: ExecutionSignal) -> ExecutionReport:
        ...
