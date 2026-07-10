"""Order forensics logger — structured order lifecycle tracking.

Every order emits events at each lifecycle stage::

    order_submitted
    order_acknowledged
    order_filled
    order_partially_filled
    order_rejected
    order_cancelled

Each event carries prices, fees, funding, slippage, and latency breakdown.
"""
from __future__ import annotations

from typing import Any

from .logging_config import get_orders_logger
from .correlation import bind_correlation_context

ORDERS_LOG = get_orders_logger()


def log_order_submitted(
    *,
    symbol: str,
    side: str,
    qty_requested: float,
    qty_submitted: float,
    price_expected: float,
    price_submitted: float,
    order_type: str = "MARKET",
    exchange_order_id: str = "",
    submit_latency_ms: float = 0.0,
    **extra: Any,
) -> None:
    """Log an order submission."""
    payload = {
        "event": "order_submitted",
        "symbol": symbol,
        "side": side,
        "order_type": order_type,
        "qty_requested": round(qty_requested, 8),
        "qty_submitted": round(qty_submitted, 8),
        "price_expected": round(price_expected, 2),
        "price_submitted": round(price_submitted, 2),
        "exchange_order_id": exchange_order_id,
        "submit_latency_ms": round(submit_latency_ms, 3),
    }
    payload.update(extra)
    ORDERS_LOG.info("order_submitted", **payload)


def log_order_acknowledged(
    *,
    symbol: str,
    exchange_order_id: str,
    side: str,
    qty_submitted: float,
    ack_latency_ms: float = 0.0,
    exchange_response: str = "",
    **extra: Any,
) -> None:
    """Log an order acknowledgment from the exchange."""
    payload = {
        "event": "order_acknowledged",
        "symbol": symbol,
        "exchange_order_id": exchange_order_id,
        "side": side,
        "qty_submitted": round(qty_submitted, 8),
        "ack_latency_ms": round(ack_latency_ms, 3),
        "exchange_response": exchange_response[:500],
    }
    payload.update(extra)
    ORDERS_LOG.info("order_acknowledged", **payload)


def log_order_filled(
    *,
    symbol: str,
    side: str,
    qty_requested: float,
    qty_filled: float,
    price_expected: float,
    price_filled: float,
    slippage_expected: float = 0.0,
    slippage_real: float = 0.0,
    fee_expected: float = 0.0,
    fee_real: float = 0.0,
    funding_expected: float = 0.0,
    funding_real: float = 0.0,
    exchange_order_id: str = "",
    submit_latency_ms: float = 0.0,
    ack_latency_ms: float = 0.0,
    fill_latency_ms: float = 0.0,
    exchange_response: str = "",
    **extra: Any,
) -> None:
    """Log a filled order."""
    payload = {
        "event": "order_filled",
        "symbol": symbol,
        "side": side,
        "qty_requested": round(qty_requested, 8),
        "qty_filled": round(qty_filled, 8),
        "price_expected": round(price_expected, 2),
        "price_filled": round(price_filled, 2),
        "slippage_expected": round(slippage_expected, 6),
        "slippage_real": round(slippage_real, 6),
        "fee_expected": round(fee_expected, 6),
        "fee_real": round(fee_real, 6),
        "funding_expected": round(funding_expected, 6),
        "funding_real": round(funding_real, 6),
        "exchange_order_id": exchange_order_id,
        "submit_latency_ms": round(submit_latency_ms, 3),
        "ack_latency_ms": round(ack_latency_ms, 3),
        "fill_latency_ms": round(fill_latency_ms, 3),
        "exchange_response": exchange_response[:500],
    }
    payload.update(extra)
    ORDERS_LOG.info("order_filled", **payload)


def log_order_rejected(
    *,
    symbol: str,
    side: str,
    qty_requested: float,
    price_expected: float,
    reason: str,
    exchange_error: str = "",
    exchange_order_id: str = "",
    submit_latency_ms: float = 0.0,
    retry_count: int = 0,
    **extra: Any,
) -> None:
    """Log an order rejection."""
    payload = {
        "event": "order_rejected",
        "symbol": symbol,
        "side": side,
        "qty_requested": round(qty_requested, 8),
        "price_expected": round(price_expected, 2),
        "reason": reason,
        "exchange_error": exchange_error[:500],
        "exchange_order_id": exchange_order_id,
        "submit_latency_ms": round(submit_latency_ms, 3),
        "retry_count": retry_count,
    }
    payload.update(extra)
    ORDERS_LOG.warning("order_rejected", **payload)


def log_order_cancelled(
    *,
    symbol: str,
    exchange_order_id: str,
    side: str,
    qty_remaining: float,
    reason: str = "",
    **extra: Any,
) -> None:
    """Log an order cancellation."""
    payload = {
        "event": "order_cancelled",
        "symbol": symbol,
        "exchange_order_id": exchange_order_id,
        "side": side,
        "qty_remaining": round(qty_remaining, 8),
        "reason": reason,
    }
    payload.update(extra)
    ORDERS_LOG.info("order_cancelled", **payload)


def log_partial_fill(
    *,
    symbol: str,
    side: str,
    qty_filled: float,
    qty_remaining: float,
    price_filled: float,
    exchange_order_id: str = "",
    fill_latency_ms: float = 0.0,
    **extra: Any,
) -> None:
    """Log a partial fill."""
    payload = {
        "event": "order_partially_filled",
        "symbol": symbol,
        "side": side,
        "qty_filled": round(qty_filled, 8),
        "qty_remaining": round(qty_remaining, 8),
        "price_filled": round(price_filled, 2),
        "exchange_order_id": exchange_order_id,
        "fill_latency_ms": round(fill_latency_ms, 3),
    }
    payload.update(extra)
    ORDERS_LOG.warning("order_partially_filled", **payload)
