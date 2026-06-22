from __future__ import annotations

from typing import Any, Callable

import structlog

from ...application.ports.execution_gate import ExecutionGate, GateVerdict
from ...domain.recovery.reconciliation_engine import ReconciliationSummary
from ...domain.value_objects.execution_signal import ExecutionSignal

log = structlog.get_logger()

_ALLOWED_SOURCES = frozenset({"STREAMING"})


class SourceExecutionGate:
    """ExecutionGate that blocks signals from non-streaming sources
    and optionally checks reconciliation status.

    Additional checks (idempotency, kill-switch) are added in
    subsequent phases.
    """

    def __init__(
        self,
        execute_fn: Callable[[ExecutionSignal], Any] | None = None,
    ) -> None:
        self._execute = execute_fn

    async def evaluate(
        self,
        signal: ExecutionSignal,
        reconciliation: ReconciliationSummary | None = None,
    ) -> GateVerdict:
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

        # Reconciliation check (I11, I19)
        if reconciliation is not None and not reconciliation.is_consistent:
            log.warning(
                "[ecl:gate] reconciliation_mismatch",
                signal_id=signal.signal_id,
                symbol=signal.symbol,
                matched=reconciliation.matched,
                missing_exchange=reconciliation.missing_on_exchange,
                missing_local=reconciliation.missing_local,
                mismatches=reconciliation.partial_mismatch,
            )
            return GateVerdict(
                approved=False,
                reason=(
                    f"reconciliation inconsistent: "
                    f"matched={reconciliation.matched}, "
                    f"missing_on_exchange={reconciliation.missing_on_exchange}, "
                    f"missing_local={reconciliation.missing_local}, "
                    f"partial_mismatch={reconciliation.partial_mismatch}"
                ),
                kill_switch_state="SOFT_HALT",
            )

        log.info("[ecl:gate] allowed", source=source)
        return GateVerdict(approved=True)
