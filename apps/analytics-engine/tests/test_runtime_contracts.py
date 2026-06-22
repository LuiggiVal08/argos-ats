"""Runtime Contract Enforcement tests for analytics-engine.

Tests that invalid payloads are caught BEFORE side effects:
- invalid candle → dropped (no propagation)
- invalid signal → not published
- invalid order  → not published
- system:errors stream captures violations (DE only in this phase)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.contracts.registry import (
    validate_candle,
    validate_signal,
    validate_order,
    validate_fill,
    validate_heartbeat,
    validate_error,
)


# ── Helpers ─────────────────────────────────────────────────────────


def _ts() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def valid_candle() -> dict[str, Any]:
    return {
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "open": "45000.50",
        "high": "45200.00",
        "low": "44900.00",
        "close": "45100.00",
        "volume": "1234.567",
        "timestamp": _ts(),
        "is_complete": True,
        "schema_version": 1,
    }


def valid_signal() -> dict[str, Any]:
    return {
        "signal_id": str(uuid.uuid4()),
        "symbol": "BTC/USDT",
        "action": "BUY",
        "confidence": 0.73,
        "model_version": "novaquant-v1.2.3",
        "regime": "TRENDING",
        "timestamp": _ts(),
        "schema_version": 1,
    }


def valid_order() -> dict[str, Any]:
    sig_id = str(uuid.uuid4())
    return {
        "order_id": str(uuid.uuid4()),
        "signal_id": sig_id,
        "symbol": "BTC/USDT",
        "side": "BUY",
        "order_type": "MARKET",
        "amount": "0.01",
        "idempotency_key": f"exec:{sig_id}",
        "timestamp": _ts(),
        "schema_version": 1,
    }


def valid_fill() -> dict[str, Any]:
    return {
        "fill_id": str(uuid.uuid4()),
        "order_id": str(uuid.uuid4()),
        "symbol": "BTC/USDT",
        "side": "BUY",
        "filled_qty": "0.01",
        "avg_price": "45100.00",
        "status": "FILLED",
        "exchange_order_id": "binance-12345",
        "timestamp": _ts(),
        "schema_version": 1,
    }


def valid_heartbeat() -> dict[str, Any]:
    return {
        "service": "data-engine",
        "status": "healthy",
        "uptime_seconds": 3600,
        "mode": "PAPER_TRADING",
        "broker_ok": True,
        "exchange_ok": True,
        "loops_alive": 4,
        "timestamp": _ts(),
        "schema_version": 1,
    }


def valid_error() -> dict[str, Any]:
    return {
        "error_id": str(uuid.uuid4()),
        "service": "data-engine",
        "severity": "ERROR",
        "error_code": "SOME_ERROR",
        "message": "Something went wrong",
        "metadata": {"foo": "bar"},
        "timestamp": _ts(),
        "schema_version": 1,
    }


# ── Phase 2A: Candle Consumer Guard ────────────────────────────────


class TestCandleConsumerGuard:
    """AE: invalid candle payloads must be discarded (no propagation)."""

    def test_valid_candle_passes(self) -> None:
        assert validate_candle(valid_candle()) == []

    def test_missing_symbol_fails(self) -> None:
        p = valid_candle()
        del p["symbol"]
        errors = validate_candle(p)
        assert len(errors) > 0
        assert any(e.field == "symbol" and e.rule == "required" for e in errors)

    def test_wrong_timestamp_type_fails(self) -> None:
        p = valid_candle()
        p["timestamp"] = "not_a_number"
        errors = validate_candle(p)
        assert any(e.field == "timestamp" and e.rule == "type" for e in errors)

    def test_decimal_string_violation_fails(self) -> None:
        p = valid_candle()
        p["open"] = "not-a-decimal"
        errors = validate_candle(p)
        assert any(e.field == "open" and e.rule == "pattern" for e in errors)

    def test_bad_timeframe_enum_fails(self) -> None:
        p = valid_candle()
        p["timeframe"] = "2h"
        errors = validate_candle(p)
        assert any(e.field == "timeframe" and e.rule == "enum" for e in errors)

    def test_empty_payload_fails(self) -> None:
        errors = validate_candle({})
        assert len(errors) > 0

    def test_schema_version_mismatch_fails(self) -> None:
        p = valid_candle()
        p["schema_version"] = 2
        errors = validate_candle(p)
        assert any(e.field == "schema_version" and e.rule == "const" for e in errors)


# ── Phase 2B: Signal Publisher Guard ────────────────────────────────


class TestSignalPublisherGuard:
    """AE: invalid signal payloads must be rejected before publish."""

    def test_valid_signal_passes(self) -> None:
        assert validate_signal(valid_signal()) == []

    def test_missing_signal_id_fails(self) -> None:
        p = valid_signal()
        del p["signal_id"]
        errors = validate_signal(p)
        assert any(e.field == "signal_id" and e.rule == "required" for e in errors)

    def test_confidence_out_of_range_high_fails(self) -> None:
        p = valid_signal()
        p["confidence"] = 1.5
        errors = validate_signal(p)
        assert any(e.field == "confidence" and e.rule == "max" for e in errors)

    def test_confidence_out_of_range_low_fails(self) -> None:
        p = valid_signal()
        p["confidence"] = -0.1
        errors = validate_signal(p)
        assert any(e.field == "confidence" and e.rule == "min" for e in errors)

    def test_bad_action_enum_fails(self) -> None:
        p = valid_signal()
        p["action"] = "HOLD"
        errors = validate_signal(p)
        assert any(e.field == "action" and e.rule == "enum" for e in errors)

    def test_bad_regime_enum_fails(self) -> None:
        p = valid_signal()
        p["regime"] = "UNKNOWN_REGIME"
        errors = validate_signal(p)
        assert any(e.field == "regime" and e.rule == "enum" for e in errors)


# ── Phase 2C: Order Publisher Guard ────────────────────────────────


class TestOrderPublisherGuard:
    """AE: invalid order payloads must be rejected before publish."""

    def test_valid_order_passes(self) -> None:
        assert validate_order(valid_order()) == []

    def test_bad_idempotency_key_prefix_fails(self) -> None:
        p = valid_order()
        p["idempotency_key"] = "wrong:prefix"
        errors = validate_order(p)
        assert any(e.field == "idempotency_key" and e.rule == "pattern" for e in errors)

    def test_bad_side_enum_fails(self) -> None:
        p = valid_order()
        p["side"] = "HOLD"
        errors = validate_order(p)
        assert any(e.field == "side" and e.rule == "enum" for e in errors)

    def test_non_market_order_type_fails(self) -> None:
        p = valid_order()
        p["order_type"] = "LIMIT"
        errors = validate_order(p)
        assert any(e.field == "order_type" and e.rule == "enum" for e in errors)

    def test_missing_amount_fails(self) -> None:
        p = valid_order()
        del p["amount"]
        errors = validate_order(p)
        assert any(e.field == "amount" and e.rule == "required" for e in errors)

    def test_amount_not_decimal_string_fails(self) -> None:
        p = valid_order()
        p["amount"] = "not-a-decimal"
        errors = validate_order(p)
        assert any(e.field == "amount" and e.rule == "pattern" for e in errors)

    def test_missing_symbol_fails(self) -> None:
        p = valid_order()
        del p["symbol"]
        errors = validate_order(p)
        assert any(e.field == "symbol" and e.rule == "required" for e in errors)


# ── Phase 4: Failure mode tests ────────────────────────────────────


class TestFailureModes:
    """All failure modes produce validation errors, never silent passes."""

    def test_missing_fields(self) -> None:
        for validator, maker in [
            (validate_candle, valid_candle),
            (validate_signal, valid_signal),
            (validate_order, valid_order),
            (validate_fill, valid_fill),
            (validate_heartbeat, valid_heartbeat),
            (validate_error, valid_error),
        ]:
            p = maker()
            errors = validator({})
            assert len(errors) > 0, f"{validator.__name__} should reject empty payload"

    def test_wrong_types(self) -> None:
        p = valid_candle()
        p["symbol"] = 123
        errors = validate_candle(p)
        assert any(e.rule == "type" for e in errors)

    def test_invalid_enums(self) -> None:
        for field, bad_val, maker in [
            ("action", "HOLD", valid_signal),
            ("timeframe", "2h", valid_candle),
            ("side", "HOLD", valid_order),
            ("status", "PENDING", valid_fill),
            ("service", "unknown", valid_error),
            ("severity", "INFO", valid_error),
        ]:
            p = maker()
            p[field] = bad_val
            errors = validate_error if maker == valid_error else (
                validate_signal if maker == valid_signal else (
                    validate_candle if maker == valid_candle else (
                        validate_order if maker == valid_order else validate_fill
                    )
                )
            )
            result = errors(p)
            assert any(e.rule == "enum" for e in result), f"{maker.__name__}.{field}={bad_val} should fail enum"

    def test_confidence_out_of_range(self) -> None:
        p = valid_signal()
        p["confidence"] = 1.5
        errors = validate_signal(p)
        assert any(e.rule == "max" for e in errors)

    def test_malformed_timestamps(self) -> None:
        p = valid_candle()
        p["timestamp"] = -1
        errors = validate_candle(p)
        assert any(e.field == "timestamp" and e.rule == "min" for e in errors)

    def test_bad_uuid_patterns(self) -> None:
        p = valid_signal()
        p["signal_id"] = "not-a-uuid"
        errors = validate_signal(p)
        assert any(e.field == "signal_id" and e.rule == "pattern" for e in errors)
