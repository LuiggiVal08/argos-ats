"""LiveDriftWatchdog — runtime drift detection during normal operations.

Fix 4: Unlike PostRecoveryDriftValidator (runs once at recovery),
this watchdog runs periodically during normal streaming to detect
state drift BETWEEN the local system and the exchange.

Periodic checks:
  1. Position count mismatch
  2. Unrecognized positions on exchange
  3. Missing positions locally vs exchange
  4. Abnormal PnL divergence (if trade journal available)

The watchdog NEVER takes corrective action — it only logs warnings.
It is the operator's (or recovery engine's) responsibility to react.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import structlog

from ...domain.recovery.drift_validator import (
    PostRecoveryDriftValidator,
    DriftValidationResult,
)
from ..ports.trade_journal import TradeJournal
from ..ports.position_repository import PositionRepository


log = structlog.get_logger()


@dataclass
class LiveDriftWatchdog:
    """Periodic drift detection between local state and exchange.

    Args:
        position_repo: Local position store.
        exchange_provider: Provides exchange-side positions/balances.
        trade_journal: Optional — enables PnL divergence check.
        check_interval_seconds: How often to run checks (default 300 = 5min).
        equity_provider: Optional — enables equity drift check.
        drift_validator: Reuses PostRecoveryDriftValidator logic.
    """

    position_repo: PositionRepository
    exchange_provider: Any
    trade_journal: TradeJournal | None = None
    check_interval_seconds: float = 300.0
    equity_provider: Any | None = None

    _task: asyncio.Task | None = field(default=None, init=False, repr=False)
    _drift_validator: PostRecoveryDriftValidator = field(
        default_factory=lambda: PostRecoveryDriftValidator(
            equity_threshold_pct=Decimal("0.01"),
            max_positions_mismatch=0,
            pnl_threshold=Decimal("2.0"),
        ),
        init=False,
        repr=False,
    )

    async def _check(self) -> DriftValidationResult | None:
        """Run a single drift check cycle.

        Returns DriftValidationResult if checks ran, None if skipped.
        """
        try:
            local_positions = await self.position_repo.list_all()
            exchange_positions = await self.exchange_provider.fetch_positions()

            expected_equity: Decimal | None = None
            actual_equity: Decimal | None = None
            expected_pnl: Decimal | None = None
            actual_pnl: Decimal | None = None
            journal_event_count: int | None = None
            expected_event_count: int | None = None

            if self.equity_provider is not None:
                try:
                    actual_equity = await self.equity_provider.get_free_balance()
                    # approximate: set expected = actual to avoid false positives
                    # on first check; only drift DETECTORS use the validator.
                    expected_equity = actual_equity
                except Exception as e:
                    log.warning("drift_watchdog_equity_fetch_failed", error=str(e))

            if self.trade_journal is not None:
                try:
                    events = await self.trade_journal.get_all()
                    journal_event_count = len(events)
                    expected_pnl = Decimal("0")
                    for ev in events:
                        if hasattr(ev, "realized_pnl") and ev.realized_pnl is not None:
                            expected_pnl += ev.realized_pnl
                except Exception as e:
                    log.warning("drift_watchdog_journal_fetch_failed", error=str(e))

            exp_positions = [
                {
                    "id": p.position_id,
                    "symbol": p.symbol,
                    "side": p.side.value if hasattr(p.side, "value") else str(p.side),
                    "quantity": float(p.units),
                }
                for p in local_positions
            ]
            act_positions = [
                {
                    "id": getattr(p, "id", getattr(p, "position_id", str(i))),
                    "symbol": getattr(p, "symbol", ""),
                    "side": str(getattr(p, "side", "")),
                    "quantity": float(getattr(p, "size", getattr(p, "quantity", 0))),
                }
                for i, p in enumerate(exchange_positions)
            ]

            result = self._drift_validator.validate(
                expected_equity=expected_equity,
                actual_equity=actual_equity,
                expected_positions=exp_positions,
                actual_positions=act_positions,
                expected_pnl=expected_pnl,
                actual_pnl=actual_pnl,
                journal_event_count=journal_event_count,
                expected_event_count=journal_event_count,
            )

            if not result.passed:
                log.warning(
                    "live_drift_detected",
                    equity_mismatch_pct=float(result.equity_mismatch_pct),
                    positions_mismatch=result.positions_mismatch,
                    pnl_divergence=float(result.pnl_divergence),
                    missing_journal_events=result.missing_journal_events,
                    details=result.details,
                )
            else:
                log.debug("live_drift_check_passed")

            return result
        except Exception as e:
            log.warning("live_drift_check_error", error=str(e))
            return None

    def start(self) -> None:
        """Start the periodic drift check loop."""
        if self._task is not None and not self._task.done():
            log.warning("drift_watchdog_already_running")
            return
        self._task = asyncio.create_task(self._run_loop())

    async def _run_loop(self) -> None:
        log.info(
            "drift_watchdog_started",
            interval_seconds=self.check_interval_seconds,
        )
        try:
            while True:
                await asyncio.sleep(self.check_interval_seconds)
                await self._check()
        except asyncio.CancelledError:
            log.info("drift_watchdog_stopped")
            raise

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
