"""Position lifecycle logger — structured entry-to-exit tracking.

Every position emits::

    position_opened
    position_closed

with full entry/exit context, PnL decomposition, and R-multiple.
"""
from __future__ import annotations

from typing import Any

from .logging_config import get_trades_logger
from .correlation import bind_correlation_context

TRADES_LOG = get_trades_logger()


def log_position_opened(
    *,
    position_id: str,
    trade_id: str,
    symbol: str,
    side: str,
    entry_timestamp: str,
    entry_price: float,
    position_size: float,
    stop_loss: float,
    take_profit: float,
    entry_reason: str,
    entry_prob_sell: float = 0.0,
    entry_prob_hold: float = 0.0,
    entry_prob_buy: float = 0.0,
    model_version: str = "",
    capital_used: float = 0.0,
    **extra: Any,
) -> None:
    """Log a position open event."""
    payload = {
        "event": "position_opened",
        "position_id": position_id,
        "trade_id": trade_id,
        "symbol": symbol,
        "side": side,
        "entry_timestamp": entry_timestamp,
        "entry_price": round(entry_price, 2),
        "position_size": round(position_size, 8),
        "stop_loss": round(stop_loss, 2) if stop_loss else 0.0,
        "take_profit": round(take_profit, 2) if take_profit else 0.0,
        "entry_reason": entry_reason,
        "entry_prob_sell": round(entry_prob_sell, 6),
        "entry_prob_hold": round(entry_prob_hold, 6),
        "entry_prob_buy": round(entry_prob_buy, 6),
        "model_version": model_version,
        "capital_used": round(capital_used, 2),
    }
    payload.update(extra)
    TRADES_LOG.info("position_opened", **payload)


def log_position_closed(
    *,
    position_id: str,
    trade_id: str,
    symbol: str,
    side: str,
    entry_timestamp: str,
    exit_timestamp: str,
    entry_price: float,
    exit_price: float,
    position_size: float,
    exit_reason: str,
    gross_pnl: float = 0.0,
    fees: float = 0.0,
    funding: float = 0.0,
    slippage: float = 0.0,
    net_pnl: float = 0.0,
    holding_time_hours: float = 0.0,
    risk_multiple: float = 0.0,
    entry_prob_sell: float = 0.0,
    entry_prob_hold: float = 0.0,
    entry_prob_buy: float = 0.0,
    exit_prob_sell: float = 0.0,
    exit_prob_hold: float = 0.0,
    exit_prob_buy: float = 0.0,
    entry_reason: str = "",
    exit_signal: str = "",
    **extra: Any,
) -> None:
    """Log a position close event with full PnL decomposition."""
    payload = {
        "event": "position_closed",
        "position_id": position_id,
        "trade_id": trade_id,
        "symbol": symbol,
        "side": side,
        "entry_timestamp": entry_timestamp,
        "exit_timestamp": exit_timestamp,
        "entry_price": round(entry_price, 2),
        "exit_price": round(exit_price, 2),
        "position_size": round(position_size, 8),
        "exit_reason": exit_reason,
        "entry_reason": entry_reason,
        "exit_signal": exit_signal,
        "gross_pnl": round(gross_pnl, 2),
        "fees": round(fees, 2),
        "funding": round(funding, 2),
        "slippage": round(slippage, 2),
        "net_pnl": round(net_pnl, 2),
        "holding_time_hours": round(holding_time_hours, 2),
        "risk_multiple": round(risk_multiple, 4),
        "entry_prob_sell": round(entry_prob_sell, 6),
        "entry_prob_hold": round(entry_prob_hold, 6),
        "entry_prob_buy": round(entry_prob_buy, 6),
        "exit_prob_sell": round(exit_prob_sell, 6),
        "exit_prob_hold": round(exit_prob_hold, 6),
        "exit_prob_buy": round(exit_prob_buy, 6),
    }
    payload.update(extra)
    TRADES_LOG.info("position_closed", **payload)


def log_position_updated(
    *,
    position_id: str,
    trade_id: str,
    symbol: str,
    current_pnl: float = 0.0,
    current_price: float = 0.0,
    stop_loss: float = 0.0,
    update_reason: str = "",
    **extra: Any,
) -> None:
    """Log a position update (SL adjustment, trailing, etc.)."""
    payload = {
        "event": "position_updated",
        "position_id": position_id,
        "trade_id": trade_id,
        "symbol": symbol,
        "current_pnl": round(current_pnl, 2),
        "current_price": round(current_price, 2),
        "stop_loss": round(stop_loss, 2),
        "update_reason": update_reason,
    }
    payload.update(extra)
    TRADES_LOG.info("position_updated", **payload)
