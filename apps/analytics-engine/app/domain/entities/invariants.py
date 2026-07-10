from __future__ import annotations

from typing import Any

from .replay_state import OutputState


class InvariantError(RuntimeError):
    """Raised when a state invariant is violated."""


# ── Individual invariant checks ────────────────────────────────────


def no_duplicate_close(state: OutputState) -> list[str]:
    """No position should transition CLOSED→CLOSED (detect double-close).

    This checks the current state only: if a position is CLOSED and has
    a close_price, a subsequent PositionClosed for the same position_id
    would be suspicious. Pure state check (no event history).
    """
    return []  # Detected at reduce time via entity_id partitioning


def quantity_non_negative(state: OutputState) -> list[str]:
    """No position should have negative quantity."""
    violations: list[str] = []
    for pid, pos in state.positions.items():
        if pos.quantity < 0:
            violations.append(
                f"position {pid}: negative quantity={pos.quantity}"
            )
    return violations


def no_orphan_close(state: OutputState) -> list[str]:
    """PositionClosed should reference an existing position."""
    return []  # Detected at reduce time, not in final state


def pnl_derivable(state: OutputState) -> list[str]:
    """Closed positions should have derivable PnL from entry/close."""
    violations: list[str] = []
    for pid, pos in state.positions.items():
        if pos.status == "CLOSED" and pos.close_price and pos.entry_price > 0 and pos.quantity > 0:
            expected = (pos.close_price - pos.entry_price) * pos.quantity
            if pos.side == "SHORT":
                expected = -expected
            diff = abs(expected - pos.pnl)
            if diff > 0.01 * abs(expected) + 0.01:
                violations.append(
                    f"position {pid}: pnl drift {pos.pnl} vs expected {expected:.2f} (diff={diff:.2f})"
                )
    return violations


def risk_state_machine_valid(state: OutputState) -> list[str]:
    """Validate risk state machine transitions."""
    violations: list[str] = []
    r = state.risk
    valid_statuses = {"ACTIVE", "DEGRADED", "HALT", "RECOVERY_PENDING"}
    if r.status not in valid_statuses:
        violations.append(f"risk status {r.status!r} not in {valid_statuses}")
    # Drawdown should be a reasonable percentage
    if r.drawdown_pct < 0 or r.drawdown_pct > 1:
        violations.append(
            f"drawdown_pct={r.drawdown_pct} outside [0, 1]"
        )
    return violations


def signal_confidence_in_range(state: OutputState) -> list[str]:
    """Signal confidence must be in [0, 1]."""
    violations: list[str] = []
    for sym, sig in state.signals.items():
        if not (0 <= sig.confidence <= 1):
            violations.append(
                f"signal {sym}: confidence={sig.confidence} outside [0, 1]"
            )
    return violations


def p_failure_rolling_valid(state: OutputState) -> list[str]:
    """p_failure_rolling values must be in [0, 1]."""
    violations: list[str] = []
    for p in state.risk.p_failure_rolling:
        if not (0 <= p <= 1):
            violations.append(
                f"p_failure={p} outside [0, 1] in rolling window"
            )
    return violations


# ── Check runner ───────────────────────────────────────────────────


def position_status_consistent(state: OutputState) -> list[str]:
    """OPEN positions must have entry_price > 0 and symbol.
       CLOSED positions must have a close_price."""
    violations: list[str] = []
    for pid, pos in state.positions.items():
        if pos.status == "OPEN":
            if pos.entry_price <= 0:
                violations.append(f"position {pid}: OPEN with entry_price={pos.entry_price}")
            if not pos.symbol:
                violations.append(f"position {pid}: OPEN with empty symbol")
        elif pos.status == "CLOSED":
            if pos.close_price is None:
                violations.append(f"position {pid}: CLOSED without close_price")
    return violations


def no_zero_entry_price(state: OutputState) -> list[str]:
    """Any position with status non-NONE must have entry_price > 0."""
    violations: list[str] = []
    for pid, pos in state.positions.items():
        if pos.status != "NONE" and pos.entry_price <= 0:
            violations.append(
                f"position {pid}: {pos.status} with entry_price={pos.entry_price}"
            )
    return violations


INVARIANTS: list[tuple[str, Any]] = [
    ("quantity_non_negative", quantity_non_negative),
    ("pnl_derivable", pnl_derivable),
    ("risk_state_machine_valid", risk_state_machine_valid),
    ("signal_confidence_in_range", signal_confidence_in_range),
    ("p_failure_rolling_valid", p_failure_rolling_valid),
    ("position_status_consistent", position_status_consistent),
    ("no_zero_entry_price", no_zero_entry_price),
]


def check_invariant(state: OutputState) -> list[tuple[str, str]]:
    """Run all invariants and return (rule_name, message) for violations.

    This NEVER raises — it collects and returns violations.
    """
    violations: list[tuple[str, str]] = []
    for name, check in INVARIANTS:
        try:
            msgs = check(state)
            for msg in msgs:
                violations.append((name, msg))
        except Exception as e:
            violations.append((name, f"check raised: {e}"))
    return violations


def fail_fast_on_invariant_break(state: OutputState) -> None:
    """Run invariants and raise InvariantError on first violation.

    Used in STRICT replay mode.
    """
    violations = check_invariant(state)
    if violations:
        name, msg = violations[0]
        raise InvariantError(f"[{name}] {msg}")
