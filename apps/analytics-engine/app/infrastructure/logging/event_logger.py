"""Structured failure and lifecycle events.

Dedicated log functions for system-critical events that must be
unambiguously identifiable in log analysis::

    WS_CONNECTED
    WS_DISCONNECTED
    WS_RECONNECTED
    REDIS_CONNECTED
    REDIS_DISCONNECTED
    MODEL_LOADED
    MODEL_RELOADED
    CHECKSUM_MISMATCH
    STALE_CANDLE
    CIRCUIT_BREAKER_TRIGGERED
    KILL_SWITCH_TRIGGERED
    ORDER_REJECTED
    PARTIAL_FILL
    RECOVERY_MODE_ENTER
    RECOVERY_MODE_EXIT
"""
from __future__ import annotations

from typing import Any

from .logging_config import get_logger
from .correlation import bind_correlation_context

EVENT_LOG = get_logger("system")


def _emit(event: str, level: str = "info", **kwargs: Any) -> None:
    """Emit a structured event to the system log."""
    payload: dict[str, Any] = {"event": event}
    payload.update(kwargs)
    fn = getattr(EVENT_LOG, level, EVENT_LOG.info)
    fn(**payload)


# ── Connectivity ──

def ws_connected(
    *,
    symbol: str = "",
    endpoint: str = "",
    ping_latency_ms: float = 0.0,
    reconnect_count: int = 0,
    **extra: Any,
) -> None:
    _emit(
        "WS_CONNECTED",
        symbol=symbol,
        endpoint=endpoint,
        ping_latency_ms=round(ping_latency_ms, 2),
        reconnect_count=reconnect_count,
        **extra,
    )


def ws_disconnected(
    *,
    code: int = 0,
    reason: str = "",
    uptime_connection_s: float = 0.0,
    **extra: Any,
) -> None:
    _emit(
        "WS_DISCONNECTED",
        code=code,
        reason=reason,
        uptime_connection_s=round(uptime_connection_s, 1),
        **extra,
    )


def ws_reconnected(
    *,
    attempt: int = 0,
    total_attempts: int = 0,
    backoff_ms: float = 0.0,
    **extra: Any,
) -> None:
    _emit(
        "WS_RECONNECTED",
        attempt=attempt,
        total_attempts=total_attempts,
        backoff_ms=round(backoff_ms, 1),
        **extra,
    )


def redis_connected(
    *,
    host: str = "",
    port: int = 0,
    latency_ms: float = 0.0,
    **extra: Any,
) -> None:
    _emit(
        "REDIS_CONNECTED",
        host=host,
        port=port,
        latency_ms=round(latency_ms, 2),
        **extra,
    )


def redis_disconnected(
    *,
    reason: str = "",
    uptime_s: float = 0.0,
    **extra: Any,
) -> None:
    _emit(
        "REDIS_DISCONNECTED",
        reason=reason,
        uptime_s=round(uptime_s, 1),
        **extra,
    )


# ── Model ──

def model_loaded(
    *,
    symbol: str = "",
    model_version: str = "",
    model_checksum: str = "",
    scaler_checksum: str = "",
    metadata_checksum: str = "",
    feature_count: int = 0,
    **extra: Any,
) -> None:
    _emit(
        "MODEL_LOADED",
        symbol=symbol,
        model_version=model_version,
        model_checksum=model_checksum,
        scaler_checksum=scaler_checksum,
        metadata_checksum=metadata_checksum,
        feature_count=feature_count,
        **extra,
    )


def model_reloaded(
    *,
    symbol: str = "",
    model_version: str = "",
    model_checksum: str = "",
    reason: str = "",
    **extra: Any,
) -> None:
    _emit(
        "MODEL_RELOADED",
        symbol=symbol,
        model_version=model_version,
        model_checksum=model_checksum,
        reason=reason,
        **extra,
    )


def checksum_mismatch(
    *,
    component: str = "",
    expected: str = "",
    actual: str = "",
    path: str = "",
    action: str = "",
    **extra: Any,
) -> None:
    _emit(
        "CHECKSUM_MISMATCH",
        level="error",
        component=component,
        expected=expected,
        actual=actual,
        path=path,
        action=action,
        **extra,
    )


# ── Data Quality ──

def stale_candle(
    *,
    symbol: str = "",
    candle_timestamp: str = "",
    age_seconds: float = 0.0,
    threshold_seconds: float = 0.0,
    **extra: Any,
) -> None:
    _emit(
        "STALE_CANDLE",
        level="warning",
        symbol=symbol,
        candle_timestamp=candle_timestamp,
        age_seconds=round(age_seconds, 1),
        threshold_seconds=threshold_seconds,
        **extra,
    )


# ── Circuit Breaker ──

def circuit_breaker_triggered(
    *,
    reason: str = "",
    drawdown_pct: float = 0.0,
    consecutive_losses: int = 0,
    current_balance: float = 0.0,
    daily_starting_balance: float = 0.0,
    **extra: Any,
) -> None:
    _emit(
        "CIRCUIT_BREAKER_TRIGGERED",
        level="critical",
        reason=reason,
        drawdown_pct=round(drawdown_pct, 4),
        consecutive_losses=consecutive_losses,
        current_balance=round(current_balance, 2),
        daily_starting_balance=round(daily_starting_balance, 2),
        **extra,
    )


def kill_switch_triggered(
    *,
    criteria: str = "",
    details: str = "",
    total_trades: int = 0,
    uptime_hours: float = 0.0,
    **extra: Any,
) -> None:
    _emit(
        "KILL_SWITCH_TRIGGERED",
        level="critical",
        criteria=criteria,
        details=details,
        total_trades=total_trades,
        uptime_hours=round(uptime_hours, 2),
        **extra,
    )


# ── Recovery ──

def recovery_mode_enter(
    *,
    reason: str = "",
    mode: str = "",
    portfolio_state: str = "",
    **extra: Any,
) -> None:
    _emit(
        "RECOVERY_MODE_ENTER",
        level="warning",
        reason=reason,
        mode=mode,
        portfolio_state=portfolio_state,
        **extra,
    )


def recovery_mode_exit(
    *,
    outcome: str = "",
    duration_s: float = 0.0,
    mode: str = "",
    **extra: Any,
) -> None:
    _emit(
        "RECOVERY_MODE_EXIT",
        outcome=outcome,
        duration_s=round(duration_s, 1),
        mode=mode,
        **extra,
    )


# ── General Failure ──

def order_rejected_event(
    *,
    symbol: str = "",
    side: str = "",
    reason: str = "",
    exchange_error: str = "",
    retry_count: int = 0,
    **extra: Any,
) -> None:
    _emit(
        "ORDER_REJECTED",
        level="warning",
        symbol=symbol,
        side=side,
        reason=reason,
        exchange_error=exchange_error[:500],
        retry_count=retry_count,
        **extra,
    )


def partial_fill_event(
    *,
    symbol: str = "",
    side: str = "",
    qty_filled: float = 0.0,
    qty_remaining: float = 0.0,
    price_filled: float = 0.0,
    **extra: Any,
) -> None:
    _emit(
        "PARTIAL_FILL",
        level="warning",
        symbol=symbol,
        side=side,
        qty_filled=round(qty_filled, 8),
        qty_remaining=round(qty_remaining, 8),
        price_filled=round(price_filled, 2),
        **extra,
    )


def feature_engine_ready(
    *,
    n_features: int = 0,
    feature_names: list[str] | None = None,
    **extra: Any,
) -> None:
    _emit(
        "FEATURE_ENGINE_READY",
        n_features=n_features,
        feature_names=feature_names or [],
        **extra,
    )
