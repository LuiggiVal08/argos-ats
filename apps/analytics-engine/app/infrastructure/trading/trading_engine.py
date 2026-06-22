from __future__ import annotations

from typing import Callable

import structlog

from ...application.ports.execution_gate import ExecutionGate, GateVerdict
from ...application.ports.trading_engine import TradingEngine
from ...application.use_cases.execute_signal import (
    ExecuteSignalResult,
    ExecuteSignalUseCase,
)
from ...domain.value_objects.execution_report import ExecutionReport
from ...domain.value_objects.execution_signal import ExecutionSignal

log = structlog.get_logger()


class LiveTradingEngine:
    """Single entrypoint for all trading execution.

    Delegates to ExecutionGate for pre-flight checks, then to
    ExecuteSignalUseCase for the actual order placement.
    """

    def __init__(
        self,
        gate: ExecutionGate,
        use_case: ExecuteSignalUseCase,
    ) -> None:
        self._gate = gate
        self._use_case = use_case

    async def execute(self, signal: ExecutionSignal) -> ExecutionReport:
        verdict = await self._gate.evaluate(signal)
        if not verdict.approved:
            log.warning(
                "[trading:engine] gate blocked",
                signal_id=signal.signal_id,
                reason=verdict.reason,
                kill_switch_state=verdict.kill_switch_state,
            )
            return ExecutionReport(
                report_id=f"blocked-{signal.signal_id}",
                signal_id=signal.signal_id,
                symbol=signal.symbol,
                side=signal.side,
                status="REJECTED",
                filled_qty=0,
                errors=[f"gate_blocked: {verdict.reason}"],
            )

        log.info(
            "[trading:engine] gate approved, executing",
            signal_id=signal.signal_id,
        )
        result: ExecuteSignalResult = await self._use_case.execute(signal)
        return result.report
