from __future__ import annotations

from types import MappingProxyType
from typing import Any

from ..value_objects.domain_event import DomainEvent

from .replay_state import (
    OutputState,
    PositionState,
    RiskState,
    SignalState,
)


def reduce(event: DomainEvent, state: OutputState) -> OutputState:
    """Dispatch a single event to the correct reducer.

    This is the ONLY entry point for the reduce step. It matches
    event_type to a pure reducer function and returns a new state.

    Raises:
        InvariantError: if the resulting state violates invariants
          (in STRICT mode).
    """
    et = event.event_type
    d = event.data or {}

    reducer = _REDUCERS.get(et)
    if reducer is None:
        return state  # Unknown event type: no-op

    return reducer(event, state)


# ── Position Reducers ──────────────────────────────────────────────


def _reduce_position_opened(event: DomainEvent, state: OutputState) -> OutputState:
    d = event.data
    pos_id = d["position_id"]
    new_pos = PositionState(
        status="OPEN",
        position_id=pos_id,
        symbol=d.get("symbol", ""),
        side=d.get("side", ""),
        entry_price=float(d.get("entry_price", 0)),
        quantity=float(d.get("quantity", 0)),
        sl_price=float(d["sl_price"]) if d.get("sl_price") is not None else None,
        tp_price=float(d["tp_price"]) if d.get("tp_price") is not None else None,
    )
    new_positions = dict(state.positions)
    new_positions[pos_id] = new_pos
    return OutputState(
        positions=MappingProxyType(new_positions),
        signals=state.signals,
        risk=state.risk,
    )


def _reduce_position_updated(event: DomainEvent, state: OutputState) -> OutputState:
    d = event.data
    pos_id = d["position_id"]
    old = state.positions.get(pos_id)
    if old is None:
        return state  # Position not found: no-op for unknown position

    new_pos = PositionState(
        status=old.status,
        position_id=old.position_id,
        symbol=old.symbol,
        side=old.side,
        entry_price=old.entry_price,
        quantity=old.quantity,
        sl_price=float(d["sl_price"]) if d.get("sl_price") is not None else old.sl_price,
        tp_price=float(d["tp_price"]) if d.get("tp_price") is not None else old.tp_price,
        pnl=old.pnl,
        close_price=old.close_price,
        close_reason=old.close_reason,
        hold_bars=old.hold_bars,
    )
    new_positions = dict(state.positions)
    new_positions[pos_id] = new_pos
    return OutputState(
        positions=MappingProxyType(new_positions),
        signals=state.signals,
        risk=state.risk,
    )


def _reduce_position_closed(event: DomainEvent, state: OutputState) -> OutputState:
    d = event.data
    pos_id = d["position_id"]
    old = state.positions.get(pos_id)
    if old is None:
        return state

    new_pos = PositionState(
        status="CLOSED",
        position_id=old.position_id,
        symbol=old.symbol,
        side=old.side,
        entry_price=old.entry_price,
        quantity=old.quantity,
        sl_price=old.sl_price,
        tp_price=old.tp_price,
        pnl=float(d.get("pnl", 0)),
        close_price=float(d["close_price"]) if d.get("close_price") is not None else None,
        close_reason=d.get("close_reason", ""),
        hold_bars=int(d.get("hold_bars", 0)),
    )
    new_positions = dict(state.positions)
    new_positions[pos_id] = new_pos
    return OutputState(
        positions=MappingProxyType(new_positions),
        signals=state.signals,
        risk=state.risk,
    )


# ── Risk Reducers ──────────────────────────────────────────────────


def _reduce_risk_evaluated(event: DomainEvent, state: OutputState) -> OutputState:
    d = event.data
    old_risk = state.risk
    new_risk = RiskState(
        status=old_risk.status,
        drawdown_pct=float(d.get("drawdown_pct", old_risk.drawdown_pct)),
        daily_pnl=float(d.get("daily_pnl", old_risk.daily_pnl)),
        starting_balance=float(d.get("starting_balance", old_risk.starting_balance)),
        current_balance=float(d.get("current_balance", old_risk.current_balance)),
        circuit_breaker=d.get("circuit_breaker", old_risk.circuit_breaker),
        p_failure_rolling=old_risk.p_failure_rolling,
    )
    return OutputState(
        positions=state.positions,
        signals=state.signals,
        risk=new_risk,
    )


def _reduce_risk_limit_breached(event: DomainEvent, state: OutputState) -> OutputState:
    d = event.data
    action = d.get("action_taken", "WARN")
    old_risk = state.risk
    new_risk = RiskState(
        status="DEGRADED" if action == "REDUCE" else old_risk.status,
        drawdown_pct=old_risk.drawdown_pct,
        daily_pnl=old_risk.daily_pnl,
        starting_balance=old_risk.starting_balance,
        current_balance=old_risk.current_balance,
        circuit_breaker=old_risk.circuit_breaker,
        p_failure_rolling=old_risk.p_failure_rolling,
    )
    return OutputState(
        positions=state.positions,
        signals=state.signals,
        risk=new_risk,
    )


def _reduce_risk_state_changed(event: DomainEvent, state: OutputState) -> OutputState:
    d = event.data
    old_risk = state.risk
    new_risk = RiskState(
        status=d.get("new_status", old_risk.status),
        drawdown_pct=old_risk.drawdown_pct,
        daily_pnl=old_risk.daily_pnl,
        starting_balance=old_risk.starting_balance,
        current_balance=old_risk.current_balance,
        circuit_breaker="TRIP" if d.get("new_status") == "HALT" else old_risk.circuit_breaker,
        p_failure_rolling=old_risk.p_failure_rolling,
    )
    return OutputState(
        positions=state.positions,
        signals=state.signals,
        risk=new_risk,
    )


# ── Signal Reducers ────────────────────────────────────────────────


def _reduce_signal_generated(event: DomainEvent, state: OutputState) -> OutputState:
    d = event.data
    symbol = d.get("symbol", "unknown")
    new_signal = SignalState(
        signal_id=d.get("signal_id", ""),
        side=d.get("side", "HOLD"),
        confidence=float(d.get("confidence", 0)),
        model_version=d.get("model_version", ""),
        regime=d.get("regime", ""),
        rejected=False,
        reject_reason="",
    )
    new_signals = dict(state.signals)
    new_signals[symbol] = new_signal
    return OutputState(
        positions=state.positions,
        signals=MappingProxyType(new_signals),
        risk=state.risk,
    )


def _reduce_signal_rejected(event: DomainEvent, state: OutputState) -> OutputState:
    d = event.data
    # SignalRejected may not have symbol — use signal_id for entity_id
    new_signal = SignalState(
        signal_id=d.get("signal_id", ""),
        side="HOLD",
        confidence=float(d.get("confidence", 0)),
        rejected=True,
        reject_reason=d.get("reason", ""),
    )
    entity_id = d.get("signal_id", "unknown")
    new_signals = dict(state.signals)
    new_signals[entity_id] = new_signal
    return OutputState(
        positions=state.positions,
        signals=MappingProxyType(new_signals),
        risk=state.risk,
    )


# ── Simulation Reducers (informational only) ───────────────────────


def _reduce_simulation_evaluated(event: DomainEvent, state: OutputState) -> OutputState:
    d = event.data
    old_risk = state.risk
    rolling = list(old_risk.p_failure_rolling)
    p_failure = float(d.get("p_failure", 0))
    rolling.append(p_failure)
    # Keep rolling window of last 3
    if len(rolling) > 3:
        rolling = rolling[-3:]

    new_risk = RiskState(
        status=old_risk.status,
        drawdown_pct=old_risk.drawdown_pct,
        daily_pnl=old_risk.daily_pnl,
        starting_balance=old_risk.starting_balance,
        current_balance=old_risk.current_balance,
        circuit_breaker="WARN" if p_failure > 0.5 else old_risk.circuit_breaker,
        p_failure_rolling=tuple(rolling),
    )
    return OutputState(
        positions=state.positions,
        signals=state.signals,
        risk=new_risk,
    )


def _reduce_monte_carlo_completed(event: DomainEvent, state: OutputState) -> OutputState:
    d = event.data
    old_risk = state.risk
    p_failure = float(d.get("p_failure", 0))
    rolling = list(old_risk.p_failure_rolling)
    rolling.append(p_failure)
    if len(rolling) > 3:
        rolling = rolling[-3:]

    new_risk = RiskState(
        status=old_risk.status,
        drawdown_pct=old_risk.drawdown_pct,
        daily_pnl=old_risk.daily_pnl,
        starting_balance=old_risk.starting_balance,
        current_balance=old_risk.current_balance,
        circuit_breaker=old_risk.circuit_breaker,
        p_failure_rolling=tuple(rolling),
    )
    return OutputState(
        positions=state.positions,
        signals=state.signals,
        risk=new_risk,
    )


# ── Config Reducer ─────────────────────────────────────────────────


def _reduce_config_changed(event: DomainEvent, state: OutputState) -> OutputState:
    return state  # Config changes don't affect replay state directly


# ── Reducer Registry ───────────────────────────────────────────────

_REDUCERS: dict[str, Any] = {
    "PositionOpened": _reduce_position_opened,
    "PositionUpdated": _reduce_position_updated,
    "PositionClosed": _reduce_position_closed,
    "RiskEvaluated": _reduce_risk_evaluated,
    "RiskLimitBreached": _reduce_risk_limit_breached,
    "RiskStateChanged": _reduce_risk_state_changed,
    "SignalGenerated": _reduce_signal_generated,
    "SignalRejected": _reduce_signal_rejected,
    "SimulationEvaluated": _reduce_simulation_evaluated,
    "MonteCarloCompleted": _reduce_monte_carlo_completed,
    "ConfigChanged": _reduce_config_changed,
}

__all__ = ["reduce", "_REDUCERS"]
