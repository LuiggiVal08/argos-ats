"""PostRecoveryDriftValidator — detects state drift after recovery.

After recovery completes, compares expected state (from snapshot + journal)
against actual state (from exchange). Flags any discrepancy.

Checks:
  1. Equity mismatch: expected_equity vs actual_equity (% deviation)
  2. Open positions mismatch: count and detail differences
  3. PnL divergence: realized PnL from journal vs exchange
  4. Missing journal events: gaps in event_id sequence

Drift is expected in small amounts (fees, funding, rounding). The validator
flags only drift ABOVE configurable thresholds.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class DriftValidationResult:
    passed: bool
    equity_mismatch_pct: Decimal = Decimal("0")
    positions_mismatch: int = 0
    pnl_divergence: Decimal = Decimal("0")
    missing_journal_events: int = 0
    details: list[str] = field(default_factory=list)


class PostRecoveryDriftValidator:
    """Validates system state against expected values post-recovery.

    Args:
        equity_threshold_pct: Max allowed equity deviation (default 0.5%).
        max_positions_mismatch: Max allowed position count diff (default 0).
        pnl_threshold: Max allowed PnL divergence in quote currency (default 1.0).
    """

    def __init__(
        self,
        equity_threshold_pct: Decimal = Decimal("0.005"),
        max_positions_mismatch: int = 0,
        pnl_threshold: Decimal = Decimal("1.0"),
    ) -> None:
        self._equity_threshold = equity_threshold_pct
        self._max_positions_mismatch = max_positions_mismatch
        self._pnl_threshold = pnl_threshold

    def validate(
        self,
        expected_equity: Decimal | None = None,
        actual_equity: Decimal | None = None,
        expected_positions: list[dict[str, Any]] | None = None,
        actual_positions: list[dict[str, Any]] | None = None,
        expected_pnl: Decimal | None = None,
        actual_pnl: Decimal | None = None,
        journal_event_count: int | None = None,
        expected_event_count: int | None = None,
    ) -> DriftValidationResult:
        """Run all drift checks.

        Each check is optional — pass None to skip that check.

        Args:
            expected_equity: Equity from snapshot before shutdown.
            actual_equity: Equity from exchange balance.
            expected_positions: Positions from snapshot.
            actual_positions: Positions from exchange fetch.
            expected_pnl: Realized PnL from journal sum.
            actual_pnl: Realized PnL from exchange.
            journal_event_count: Number of events in local journal.
            expected_event_count: Expected number of events from sequence.

        Returns:
            DriftValidationResult with per-check status.
        """
        details: list[str] = []

        # 1. Equity mismatch
        equity_ok = True
        equity_mismatch_pct = Decimal("0")
        if expected_equity is not None and actual_equity is not None and expected_equity > 0:
            diff = abs(actual_equity - expected_equity)
            equity_mismatch_pct = diff / expected_equity
            if equity_mismatch_pct > self._equity_threshold:
                equity_ok = False
                details.append(
                    f"equity_mismatch: expected={expected_equity:.2f}, "
                    f"actual={actual_equity:.2f}, "
                    f"drift={equity_mismatch_pct*100:.2f}%"
                )

        # 2. Open positions mismatch
        positions_ok = True
        positions_mismatch = 0
        if expected_positions is not None and actual_positions is not None:
            exp_count = len(expected_positions)
            act_count = len(actual_positions)
            positions_mismatch = abs(exp_count - act_count)
            if positions_mismatch > self._max_positions_mismatch:
                positions_ok = False
                details.append(
                    f"positions_mismatch: expected={exp_count}, "
                    f"actual={act_count}, diff={positions_mismatch}"
                )

            # Check individual position differences
            exp_symbols = {p.get("symbol", p.get("id", "")) for p in expected_positions}
            act_symbols = {p.get("symbol", p.get("id", "")) for p in actual_positions}
            missing_in_actual = exp_symbols - act_symbols
            extra_in_actual = act_symbols - exp_symbols
            if missing_in_actual:
                details.append(
                    f"positions_missing_in_actual: {', '.join(sorted(missing_in_actual))}"
                )
            if extra_in_actual:
                details.append(
                    f"positions_extra_in_actual: {', '.join(sorted(extra_in_actual))}"
                )

        # 3. PnL divergence
        pnl_ok = True
        pnl_div = Decimal("0")
        if expected_pnl is not None and actual_pnl is not None:
            pnl_div = abs(actual_pnl - expected_pnl)
            if pnl_div > self._pnl_threshold:
                pnl_ok = False
                details.append(
                    f"pnl_divergence: expected={expected_pnl:.2f}, "
                    f"actual={actual_pnl:.2f}, diff={pnl_div:.2f}"
                )

        # 4. Missing journal events
        journal_ok = True
        missing_events = 0
        if journal_event_count is not None and expected_event_count is not None:
            if expected_event_count > 0 and journal_event_count < expected_event_count:
                missing_events = expected_event_count - journal_event_count
                journal_ok = False
                details.append(
                    f"missing_journal_events: have={journal_event_count}, "
                    f"expected={expected_event_count}, missing={missing_events}"
                )

        passed = equity_ok and positions_ok and pnl_ok and journal_ok

        return DriftValidationResult(
            passed=passed,
            equity_mismatch_pct=equity_mismatch_pct,
            positions_mismatch=positions_mismatch,
            pnl_divergence=pnl_div,
            missing_journal_events=missing_events,
            details=details,
        )
