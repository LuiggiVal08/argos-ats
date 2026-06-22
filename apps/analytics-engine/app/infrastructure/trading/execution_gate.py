from __future__ import annotations

from typing import Any, Callable

import structlog

from ...application.ports.execution_gate import ExecutionGate, GateVerdict
from ...domain.value_objects.execution_signal import ExecutionSignal

log = structlog.get_logger()

_ALLOWED_SOURCES = frozenset({"STREAMING"})


class SourceExecutionGate:
    """ExecutionGate that blocks signals from non-streaming sources.

    Used as the initial gate implementation. Additional checks
    (reconciliation, idempotency, kill-switch) are added in
    subsequent phases.
    """

    def __init__(self, execute_fn: Callable[[ExecutionSignal], Any] | None = None) -> None:
        self._execute = execute_fn

    async def evaluate(self, signal: ExecutionSignal) -> GateVerdict:
        source = signal.metadata.get("source", "UNKNOWN")
        if source not in _ALLOWED_SOURCES:
            log.warning(
                "[ecl:gate] blocked",
                source=source,
                signal_id=signal.signal_id,
                side=signal.side.value,
            )
            return GateVerdict(
                approved=False,
                reason=f"source={source} is not allowed for Phase B execution",
            )
        log.info("[ecl:gate] allowed", source=source)
        return GateVerdict(approved=True)
