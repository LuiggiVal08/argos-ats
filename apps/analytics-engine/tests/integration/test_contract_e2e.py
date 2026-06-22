"""E2E contract validation tests for the analytics-engine side.

E2E-1 (contract flow): validates that candle, signal, and order payloads
    are schema-compliant when round-tripped through Redis.

E2E-2 (execution loop): validates that order and fill payloads
    are schema-compliant.

Requires ARGOS_BROKER_URL env var. Skipped if unset.
"""
import json
import os
import uuid

import pytest

redis = pytest.importorskip("redis.asyncio")
from app.contracts.registry import validate_candle, validate_signal, validate_order, validate_fill


@pytest.mark.asyncio
async def test_e2e1_candle_signal_order_contract_flow():
    url = os.environ.get("ARGOS_BROKER_URL")
    if not url:
        pytest.skip("ARGOS_BROKER_URL not set")

    client = redis.from_url(url)
    ns = uuid.uuid4().hex[:8]
    candle_stream = "market:candles:1h"
    signal_stream = "signals:trading"
    order_stream = "orders:execution"

    try:
        # -- Step 1: DE publishes candle ---------------------------------
        candle = {
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "open": "45000.50",
            "high": "45200.00",
            "low": "44900.00",
            "close": "45100.00",
            "volume": "1234.567",
            "timestamp": 1718000000000,
            "is_complete": True,
            "schema_version": 1,
        }
        errs = validate_candle(candle)
        assert not errs, f"candle contract violated: {errs}"

        await client.xadd(candle_stream, {"p": json.dumps(candle)})
        read = await client.xread({candle_stream: "0"}, block=2000, count=10)
        assert read, "no candle read from stream"

        # -- Step 2: AE publishes signal ---------------------------------
        signal = {
            "signal_id": str(uuid.uuid4()),
            "symbol": "BTC/USDT",
            "action": "BUY",
            "confidence": 0.73,
            "model_version": "novaquant-v1.2.3",
            "regime": "TRENDING",
            "timestamp": 1718000000000,
            "schema_version": 1,
        }
        errs = validate_signal(signal)
        assert not errs, f"signal contract violated: {errs}"

        await client.xadd(signal_stream, {"p": json.dumps(signal)})
        read = await client.xread({signal_stream: "0"}, block=2000, count=10)
        assert read, "no signal read from stream"

        # -- Step 3: AE publishes order ----------------------------------
        order = {
            "order_id": str(uuid.uuid4()),
            "signal_id": signal["signal_id"],
            "symbol": "BTC/USDT",
            "side": "BUY",
            "order_type": "MARKET",
            "amount": "0.01",
            "idempotency_key": f"exec:{signal['signal_id']}",
            "timestamp": 1718000000000,
            "schema_version": 1,
        }
        errs = validate_order(order)
        assert not errs, f"order contract violated: {errs}"

        await client.xadd(order_stream, {"p": json.dumps(order)})
        read = await client.xread({order_stream: "0"}, block=2000, count=10)
        assert read, "no order read from stream"

    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_e2e2_order_fill_execution_loop():
    url = os.environ.get("ARGOS_BROKER_URL")
    if not url:
        pytest.skip("ARGOS_BROKER_URL not set")

    client = redis.from_url(url)
    order_stream = "orders:execution"
    fill_stream = "fills:execution"

    try:
        # -- Step 1: DE receives order -----------------------------------
        order = {
            "order_id": str(uuid.uuid4()),
            "signal_id": str(uuid.uuid4()),
            "symbol": "BTC/USDT",
            "side": "BUY",
            "order_type": "MARKET",
            "amount": "0.01",
            "idempotency_key": f"exec:{uuid.uuid4()}",
            "timestamp": 1718000000000,
            "schema_version": 1,
        }
        errs = validate_order(order)
        assert not errs, f"order contract violated: {errs}"

        await client.xadd(order_stream, {"p": json.dumps(order)})
        read = await client.xread({order_stream: "0"}, block=2000, count=10)
        assert read, "no order read from stream"

        # -- Step 2: DE publishes fill -----------------------------------
        fill = {
            "fill_id": str(uuid.uuid4()),
            "order_id": order["order_id"],
            "symbol": "BTC/USDT",
            "side": "BUY",
            "filled_qty": "0.01",
            "avg_price": "45100.00",
            "status": "FILLED",
            "exchange_order_id": "binance-order-12345",
            "timestamp": 1718000000001,
            "schema_version": 1,
        }
        errs = validate_fill(fill)
        assert not errs, f"fill contract violated: {errs}"

        await client.xadd(fill_stream, {"p": json.dumps(fill)})
        read = await client.xread({fill_stream: "0"}, block=2000, count=10)
        assert read, "no fill read from stream"

    finally:
        await client.aclose()
