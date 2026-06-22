"""ShadowProducer: records live decisions to Redis for off-line evaluation.

No execution path dependency — fire-and-forget XADD. Latency impact
is one Redis write per inference (~1ms). If Redis is unreachable the
write is silently dropped (shadow loss is non-critical).
"""
from __future__ import annotations

import hashlib
import json
import time
from decimal import Decimal
from typing import Any

import structlog

log = structlog.get_logger()


def make_decision_id(symbol: str, candle_ts: int, action: str) -> str:
    raw = f"{symbol}:{candle_ts}:{action}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def produce_shadow_decision(
    client: Any,
    symbol: str,
    action: str,
    confidence: float,
    price: Decimal,
    candle_ohlcv: dict[str, float | int],
    candle_ts: int,
    model_version: str,
    regime: str,
) -> None:
    decision_id = make_decision_id(symbol, candle_ts, action)
    payload = {
        "decision_id": decision_id,
        "candle_ts": candle_ts,
        "action": action,
        "confidence": confidence,
        "price_at_decision": str(price),
        "candle_open": candle_ohlcv.get("open", 0),
        "candle_high": candle_ohlcv.get("high", 0),
        "candle_low": candle_ohlcv.get("low", 0),
        "candle_close": candle_ohlcv.get("close", 0),
        "candle_volume": candle_ohlcv.get("volume", 0),
        "model_version": model_version,
        "regime": regime,
        "ts_ms": int(time.time() * 1000),
    }
    stream = f"shadow:decisions:{symbol.replace('/', '').lower()}"
    try:
        await client.xadd(stream, {"p": json.dumps(payload)}, maxlen=5000)
    except Exception:
        log.warning("shadow_producer_xadd_failed", stream=stream, decision_id=decision_id)
