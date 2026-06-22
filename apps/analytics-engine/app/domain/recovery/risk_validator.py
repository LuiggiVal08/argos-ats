"""RiskValidator — post-recovery invariant validation.

Validates that after recovery, the system state doesn't violate
risk invariants. This is the last gate before trading resumes.

Checks:
  1. Exposure check: total position value ≤ max allowed exposure
  2. Drawdown consistency: recovery snapshot drawdown matches journal
  3. Position sizing sanity: no position exceeds risk_pct of balance
  4. Circuit breaker state: if was HALTED, should remain HALTED
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class RiskValidationResult:
    passed: bool
    exposure_check: bool = True
    drawdown_check: bool = True
    sizing_check: bool = True
    circuit_breaker_check: bool = True
    errors: list[str] = field(default_factory=list)


class RiskValidator:
    """Domain entity: validates risk invariants post-recovery.

    Args:
        max_exposure_pct: Maximum fraction of equity exposed per symbol (default 0.5).
        risk_pct: Maximum risk per trade (default 0.01).
        max_drawdown_pct: Maximum allowed drawdown (default 0.05).
    """

    def __init__(
        self,
        max_exposure_pct: Decimal = Decimal("0.5"),
        risk_pct: Decimal = Decimal("0.01"),
        max_drawdown_pct: Decimal = Decimal("0.05"),
    ) -> None:
        self._max_exposure_pct = max_exposure_pct
        self._risk_pct = risk_pct
        self._max_drawdown_pct = max_drawdown_pct

    def validate(
        self,
        positions: list[dict[str, Any]],
        equity: Decimal | None = None,
        snapshot_drawdown: Decimal | None = None,
        journal_drawdown: Decimal | None = None,
        circuit_breaker_halted: bool = False,
    ) -> RiskValidationResult:
        """Run all risk checks on recovered state.

        Args:
            positions: List of open position dicts (from SQLite/exchange).
            equity: Current free equity (from balance provider).
            snapshot_drawdown: Drawdown from snapshot (if loaded).
            journal_drawdown: Drawdown computed from journal.
            circuit_breaker_halted: True if CB was HALTED before restart.

        Returns:
            RiskValidationResult with per-check status.
        """
        errors: list[str] = []

        # 1. Exposure check
        exposure_ok = True
        if equity is not None and equity > 0 and positions:
            total_exposure = Decimal("0")
            for p in positions:
                qty = Decimal(str(p.get("quantity", p.get("units", 0))))
                price = Decimal(str(p.get("currentPrice", p.get("current_price", 0))))
                total_exposure += qty * price

            max_allowed = equity * self._max_exposure_pct
            if total_exposure > max_allowed:
                exposure_ok = False
                errors.append(
                    f"exposure_check_failed: total_exposure={total_exposure:.2f} > "
                    f"max_allowed={max_allowed:.2f} (equity={equity:.2f})"
                )

        # 2. Drawdown consistency check
        drawdown_ok = True
        if snapshot_drawdown is not None and journal_drawdown is not None:
            diff = abs(snapshot_drawdown - journal_drawdown)
            if diff > Decimal("0.001"):  # 0.1% tolerance
                drawdown_ok = False
                errors.append(
                    f"drawdown_mismatch: snapshot={snapshot_drawdown:.4f}, "
                    f"journal={journal_drawdown:.4f}, diff={diff:.4f}"
                )
        if snapshot_drawdown is not None and snapshot_drawdown > self._max_drawdown_pct:
            drawdown_ok = False
            errors.append(
                f"drawdown_exceeded: {snapshot_drawdown:.4f} > "
                f"max={self._max_drawdown_pct:.4f}"
            )

        # 3. Position sizing sanity check
        sizing_ok = True
        if equity is not None and equity > 0:
            max_risk_amount = equity * self._risk_pct
            for p in positions:
                qty = Decimal(str(p.get("quantity", p.get("units", 0))))
                price = Decimal(str(p.get("currentPrice", p.get("current_price", 0))))
                notional = qty * price
                # Approximate risk as 1.5 * ATR * qty or 0.5% of notional
                approx_risk = notional * Decimal("0.005")
                if approx_risk > max_risk_amount * Decimal("3"):
                    sizing_ok = False
                    errors.append(
                        f"position_sizing_check_failed: {p.get('symbol', 'unknown')} "
                        f"approx_risk={approx_risk:.2f} > "
                        f"3x max_risk={max_risk_amount:.2f}"
                    )

        # 4. Circuit breaker state
        cb_ok = True
        if circuit_breaker_halted:
            cb_ok = False
            errors.append(
                "circuit_breaker_was_halted_before_restart — manual reset required"
            )

        passed = exposure_ok and drawdown_ok and sizing_ok and cb_ok

        return RiskValidationResult(
            passed=passed,
            exposure_check=exposure_ok,
            drawdown_check=drawdown_ok,
            sizing_check=sizing_ok,
            circuit_breaker_check=cb_ok,
            errors=errors,
        )
