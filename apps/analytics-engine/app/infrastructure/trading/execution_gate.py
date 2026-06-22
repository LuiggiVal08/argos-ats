"""ExecutionGate — Module 4: source-based execution gate.

Intercepta TODAS las órdenes antes de ExecutionEngine:
  - source != STREAMING → BLOCK
  - source == STREAMING → ALLOW
"""
from __future__ import annotations

from typing import Any, Callable

import structlog

from ...domain.value_objects.execution_signal import ExecutionSignal

log = structlog.get_logger()

_ALLOWED_SOURCES = frozenset({"STREAMING"})


class ExecutionGate:
    """Wraps an execute callable and enforces source-based access control.

    Usage in REST composition::

        use_case = ExecuteSignalUseCase(...)
        gated = ExecutionGate(use_case.execute)
        result = await gated.execute(signal)
        # signal.metadata["source"] must == "STREAMING"

    Usage in streaming loop::

        execute_fn = ExecutionGate(execute_uc.execute, source="STREAMING")
    """

    def __init__(self, execute_fn: Callable[[ExecutionSignal], Any]) -> None:
        self._execute = execute_fn

    async def execute(self, signal: ExecutionSignal) -> Any:
        source = signal.metadata.get("source", "UNKNOWN")
        if source not in _ALLOWED_SOURCES:
            log.warning(
                "[ecl:gate] blocked",
                source=source,
                signal_id=signal.signal_id,
                side=signal.side.value,
            )
            return _GateBlocked(source=source)
        log.info("[ecl:gate] allowed", source=source)
        return await self._execute(signal)


class _GateBlocked:
    """Returned when ExecutionGate blocks a request."""

    def __init__(self, source: str) -> None:
        self.approved = False
        self.reason = f"source={source} is not allowed for Phase B execution"
