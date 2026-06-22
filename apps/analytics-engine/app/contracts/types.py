"""Typed dataclasses for all contract payloads.

These are derived from the JSON schemas in contracts/*.json (single source of truth).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class Candle:
    symbol: str
    timeframe: Literal["1m", "5m", "15m", "1h", "4h", "1d"]
    open: str
    high: str
    low: str
    close: str
    volume: str
    timestamp: int  # ms UTC
    is_complete: bool = True
    schema_version: int = 1


@dataclass(frozen=True)
class Signal:
    signal_id: str
    symbol: str
    action: Literal["BUY", "SELL"]
    confidence: float  # 0.0 – 1.0
    model_version: str | None = None
    regime: Literal["TRENDING", "RANGING", "UNKNOWN"] | None = None
    timestamp: int = 0
    schema_version: int = 1


@dataclass(frozen=True)
class Order:
    order_id: str
    signal_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET"] = "MARKET"
    amount: str = "0"
    idempotency_key: str = ""
    timestamp: int = 0
    schema_version: int = 1


@dataclass(frozen=True)
class Fill:
    fill_id: str
    order_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    filled_qty: str
    avg_price: str
    status: Literal["FILLED", "PARTIALLY_FILLED", "REJECTED"]
    exchange_order_id: str | None = None
    error_message: str | None = None
    timestamp: int = 0
    schema_version: int = 1


@dataclass(frozen=True)
class Heartbeat:
    service: Literal["data-engine", "analytics-engine"]
    status: Literal["healthy", "degraded", "halted"]
    uptime_seconds: int
    mode: Literal["BACKTESTING", "PAPER_TRADING", "LIVE", "LIVE_SIMULATION"]
    broker_ok: bool
    exchange_ok: bool
    loops_alive: int | None = None
    timestamp: int = 0
    schema_version: int = 1


@dataclass(frozen=True)
class Error:
    error_id: str
    service: Literal["data-engine", "analytics-engine"]
    severity: Literal["WARN", "ERROR", "CRITICAL"]
    error_code: str
    message: str
    metadata: dict[str, Any] | None = None
    timestamp: int = 0
    schema_version: int = 1
