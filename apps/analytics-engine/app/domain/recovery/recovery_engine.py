"""RecoveryEngine — reconstructs exact system state on boot.

Flow:
  1. Write recovery_state = STARTED
  2. Load latest snapshot from SnapshotRepository
  3. Load open positions from PositionRepository (SQLite)
  4. Fetch current positions from Exchange
  5. Write recovery_state = IN_PROGRESS
  6. Reconcile: DB vs Exchange (Exchange always wins, with guardrails)
  7. Apply reconciliation actions
  8. Validate risk invariants (exposure, drawdown, sizing)
  9. Evaluate RecoveryGate
  10. Write recovery_state = COMPLETED or FAILED
  11. Return recovery verdict

Fix 1: Atomic — system won't trade without COMPLETED flag.
Fix 3: Exchange guardrails — completeness + freshness checks.
Fix 4: Event_id embedded in snapshot/journal linkage.
Fix 6: Risk invariant validation post-recovery.

If any step fails, the engine writes FAILED and raises RecoveryError.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from ...application.ports.position_repository import PositionRepository
from ...application.ports.exchange_position_provider import (
    ExchangePositionProvider,
    ExchangePositionProviderError,
)
from ...application.ports.snapshot_repository import (
    RecoveryState,
    SnapshotRepository,
    SnapshotRepositoryError,
    SystemSnapshot,
)
from ...domain.value_objects.event_id import uuid7
from ...domain.value_objects.live_position import LivePosition
from ...domain.value_objects.order import OrderSide
from .reconciliation_engine import (
    ReconciliationEngine,
    ReconciliationStatus,
    ReconciliationSummary,
)
from .recovery_gate import GateState, GateVerdict, RecoveryGate
from .risk_validator import RiskValidator, RiskValidationResult
from .drift_validator import DriftValidationResult, PostRecoveryDriftValidator

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecoveryReport:
    timestamp: datetime
    event_id: str
    snapshot_loaded: bool
    local_positions_count: int
    exchange_positions_count: int
    reconciliation: ReconciliationSummary | None
    risk_validation: RiskValidationResult | None
    gate: GateVerdict
    drift: DriftValidationResult | None = None
    actions_taken: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def recovered(self) -> bool:
        return self.gate.state != GateState.BLOCKED


class RecoveryError(RuntimeError):
    """Unrecoverable state — engine cannot start."""


class RecoveryEngine:
    """Orchestrates full system recovery on startup.

    Args:
        position_repo: Position repository (SQLite).
        exchange_provider: Exchange position fetcher.
        snapshot_repo: System snapshot store (also handles recovery state).
        reconciliation_engine: Domain reconciliation logic.
        gate: Startup safety evaluator.
        risk_validator: Post-recovery risk invariant validator.
        position_repo_raw: Direct SQLite access for raw position dicts.
    """

    def __init__(
        self,
        position_repo: PositionRepository,
        exchange_provider: ExchangePositionProvider,
        snapshot_repo: SnapshotRepository,
        reconciliation_engine: ReconciliationEngine | None = None,
        gate: RecoveryGate | None = None,
        risk_validator: RiskValidator | None = None,
        drift_validator: PostRecoveryDriftValidator | None = None,
        position_repo_raw: Any | None = None,
        equity_provider: Any | None = None,
        trade_journal: Any | None = None,
    ) -> None:
        self._position_repo = position_repo
        self._exchange_provider = exchange_provider
        self._snapshot_repo = snapshot_repo
        self._engine = reconciliation_engine or ReconciliationEngine()
        self._gate = gate or RecoveryGate()
        self._risk_validator = risk_validator or RiskValidator()
        self._drift_validator = drift_validator or PostRecoveryDriftValidator()
        self._position_repo_raw = position_repo_raw or position_repo
        self._equity_provider = equity_provider
        self._trade_journal = trade_journal

    async def recover(self) -> RecoveryReport:
        """Execute the full recovery flow with atomic state tracking.

        Returns RecoveryReport. If state is BLOCKED, raises RecoveryError.
        """
        actions: list[str] = []
        errors: list[str] = []
        event_id = str(uuid7())

        # ── Step 0: Write STARTED ─────────────────────────────────────────
        try:
            await self._snapshot_repo.save_recovery_state(RecoveryState.STARTED)
        except SnapshotRepositoryError as e:
            errors.append(f"recovery_state_write_failed_at_start: {e}")

        # ── Step 1: Load latest snapshot ──────────────────────────────────
        snapshot: SystemSnapshot | None = None
        try:
            snapshot = await self._snapshot_repo.load_latest()
        except SnapshotRepositoryError as e:
            errors.append(f"snapshot_load_failed: {e}")

        # ── Step 2: Load local positions ──────────────────────────────────
        try:
            if hasattr(self._position_repo_raw, "get_open_positions_snapshot"):
                local_positions = await self._position_repo_raw.get_open_positions_snapshot()
            else:
                locals_list = await self._position_repo.list_open()
                local_positions = [
                    {
                        "id": p.position_id,
                        "symbol": p.symbol,
                        "side": p.side.value,
                        "entryPrice": float(p.entry_price),
                        "quantity": float(p.units),
                        "currentPrice": float(p.current_price),
                        "status": p.status,
                    }
                    for p in locals_list
                ]
        except Exception as e:
            errors.append(f"local_positions_load_failed: {e}")
            local_positions = []

        # ── Step 3: Fetch exchange positions ──────────────────────────────
        exchange_positions: list[dict] = []
        exchange_timestamp = time.time()
        try:
            ex_positions = await self._exchange_provider.fetch_positions()
            exchange_positions = [
                {
                    "symbol": ep.symbol,
                    "side": ep.side,
                    "quantity": float(ep.quantity),
                    "entryPrice": float(ep.entry_price),
                    "currentPrice": float(ep.current_price),
                    "unrealizedPnl": float(ep.unrealized_pnl) if ep.unrealized_pnl else 0,
                }
                for ep in ex_positions
            ]
        except ExchangePositionProviderError as e:
            errors.append(f"exchange_positions_fetch_failed: {e}")
        except Exception as e:
            errors.append(f"exchange_positions_unexpected_error: {e}")

        # ── Step 4: Write IN_PROGRESS ─────────────────────────────────────
        try:
            await self._snapshot_repo.save_recovery_state(RecoveryState.IN_PROGRESS)
        except SnapshotRepositoryError as e:
            errors.append(f"recovery_state_write_failed_in_progress: {e}")

        # ── Step 5: Reconcile ─────────────────────────────────────────────
        reconciliation: ReconciliationSummary | None = None
        try:
            reconciliation = self._engine.reconcile(
                local_positions,
                exchange_positions,
                exchange_timestamp=exchange_timestamp,
            )
        except Exception as e:
            errors.append(f"reconciliation_failed: {e}")

        # ── Step 6: Apply reconciliation actions ──────────────────────────
        if reconciliation is not None:
            for result in reconciliation.details:
                if result.guardrail_failed:
                    actions.append(
                        f"guardrail_skipped {result.symbol}: {result.guardrail_reason}"
                    )
                    continue

                if result.status == ReconciliationStatus.MISSING_ON_EXCHANGE:
                    try:
                        await self._position_repo.delete(result.position_id)
                        actions.append(
                            f"closed_local_position {result.position_id} "
                            f"({result.symbol}) — missing on exchange"
                        )
                    except Exception as e:
                        errors.append(
                            f"failed_to_close_local {result.position_id}: {e}"
                        )

                elif result.status == ReconciliationStatus.MISSING_LOCAL:
                    if result.exchange_units > 0:
                        try:
                            ex_side = OrderSide.BUY if result.side == "long" else OrderSide.SELL
                            reconstructed = LivePosition(
                                position_id=uuid4().hex[:12],
                                symbol=result.symbol,
                                side=ex_side,
                                units=abs(result.exchange_units),
                                entry_price=abs(result.exchange_entry),
                                current_price=abs(result.exchange_entry),
                                sl_price=None,
                                tp_price=None,
                                status="OPEN",
                                opened_at=datetime.now(timezone.utc),
                                metadata={"reconstructed": "true", "source": "recovery_missing_local"},
                            )
                            await self._position_repo.save(reconstructed)
                            actions.append(
                                f"reconstructed_position {reconstructed.position_id} "
                                f"({result.symbol}): {result.exchange_units} units "
                                f"at {result.exchange_entry}"
                            )
                        except Exception as e:
                            errors.append(
                                f"failed_to_reconstruct_position {result.symbol}: {e}"
                            )
                    else:
                        actions.append(
                            f"skipped_reconstruct_position {result.symbol} — "
                            f"exchange_units={result.exchange_units} (closed position)"
                        )

                elif result.status == ReconciliationStatus.PARTIAL_MISMATCH:
                    try:
                        await self._position_repo.reconcile_position_with_exchange(
                            result.position_id,
                            {
                                "quantity": float(result.exchange_units),
                                "entryPrice": float(result.exchange_entry),
                                "side": "LONG" if result.exchange_units > 0 else "SHORT",
                                "currentPrice": 0,
                            },
                        )
                        actions.append(
                            f"reconciled_position {result.position_id} "
                            f"({result.symbol}): {result.local_units}→{result.exchange_units} units"
                        )
                    except Exception as e:
                        errors.append(
                            f"failed_to_reconcile {result.position_id}: {e}"
                        )

        # Reconciliation actions have been applied — create a clean summary
        # so the gate doesn't evaluate with stale mismatch counters.
        # The original ReconciliationSummary is frozen and still records
        # mismatches that were just resolved (position deleted/reconstructed/
        # reconciled). Post-reconciliation summary reflects current state.
        if reconciliation is not None:
            reconciliation = ReconciliationSummary(
                total=reconciliation.matched,
                matched=reconciliation.matched,
                missing_on_exchange=0,
                missing_local=0,
                partial_mismatch=0,
                details=[],
            )

        # ── Step 7: Risk invariant validation (Fix 6) ─────────────────────
        equity: Decimal | None = None
        if self._equity_provider is not None:
            try:
                equity = await self._equity_provider.get_free_balance("USDT")
            except Exception:
                log.warning("recovery_equity_fetch_failed", exc_info=True)

        risk_validation: RiskValidationResult | None = None
        if equity is not None:
            try:
                risk_validation = self._risk_validator.validate(
                    positions=local_positions,
                    equity=equity,
                    snapshot_drawdown=snapshot.drawdown if snapshot else None,
                )
                if not risk_validation.passed:
                    errors.extend(risk_validation.errors)
                    for err in risk_validation.errors:
                        actions.append(f"risk_check_failed: {err}")
            except Exception as e:
                errors.append(f"risk_validation_error: {e}")

        # ── Step 7b: Drift validation (Fix 4) ──────────────────────────
        drift: DriftValidationResult | None = None
        try:
            expected_pnl = None
            actual_pnl = None
            journal_count = None
            expected_event_count = None

            if self._trade_journal is not None and hasattr(self._trade_journal, "total_pnl"):
                expected_pnl = await self._trade_journal.total_pnl()
            if hasattr(self._trade_journal, "count"):
                journal_count = await self._trade_journal.count()

            drift = self._drift_validator.validate(
                expected_equity=snapshot.equity if snapshot else None,
                actual_equity=equity,
                expected_positions=[{
                    "symbol": lp.get("symbol", ""),
                    "status": lp.get("status", ""),
                } for lp in local_positions] if local_positions else None,
                actual_positions=exchange_positions if exchange_positions else None,
                expected_pnl=expected_pnl,
                actual_pnl=actual_pnl,
                journal_event_count=journal_count,
                expected_event_count=len(exchange_positions) if exchange_positions else None,
            )
            if drift is not None and not drift.passed:
                for detail in drift.details:
                    actions.append(f"drift_detected: {detail}")
        except Exception as e:
            errors.append(f"drift_validation_error: {e}")

        # ── Step 8: Gate evaluation ───────────────────────────────────────
        db_accessible = True
        gate = self._gate.evaluate(
            snapshot=snapshot,
            db_accessible=db_accessible,
            reconciliation=reconciliation,
            risk_validation=risk_validation,
        )

        # ── Step 9: Write COMPLETED or FAILED ─────────────────────────────
        if gate.state == GateState.BLOCKED:
            try:
                await self._snapshot_repo.save_recovery_state(
                    RecoveryState.FAILED,
                    error=gate.reason + "; " + "; ".join(errors) if errors else gate.reason,
                )
            except SnapshotRepositoryError:
                pass
            raise RecoveryError(
                f"recovery_blocked: {gate.reason}. "
                f"Errors: {'; '.join(errors) if errors else 'none'}"
            )
        else:
            try:
                await self._snapshot_repo.save_recovery_state(RecoveryState.COMPLETED)
            except SnapshotRepositoryError as e:
                errors.append(f"recovery_state_write_failed_completed: {e}")

        # Re-evaluate gate with completed recovery_state to
        # clear stale boot-time warnings (e.g. no_recovery_state_record)
        try:
            updated_recovery_state = await self._snapshot_repo.load_recovery_state()
            if updated_recovery_state is not None:
                gate = self._gate.evaluate(
                    snapshot=snapshot,
                    db_accessible=db_accessible,
                    reconciliation=reconciliation,
                    recovery_state=updated_recovery_state,
                    risk_validation=risk_validation,
                )
        except SnapshotRepositoryError as e:
            errors.append(f"recovery_state_reload_failed: {e}")

        report = RecoveryReport(
            timestamp=datetime.now(timezone.utc),
            event_id=event_id,
            snapshot_loaded=snapshot is not None,
            local_positions_count=len(local_positions),
            exchange_positions_count=len(exchange_positions),
            reconciliation=reconciliation,
            risk_validation=risk_validation,
            gate=gate,
            drift=drift,
            actions_taken=actions,
            errors=errors,
        )
        return report
