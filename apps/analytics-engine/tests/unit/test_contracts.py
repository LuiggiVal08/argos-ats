"""Unit tests for contract validators.

These test the validation rules WITHOUT Redis — pure Python logic.
"""
from __future__ import annotations

import uuid

from app.contracts.registry import (
    validate_candle,
    validate_signal,
    validate_order,
    validate_fill,
    validate_heartbeat,
    validate_error,
)


def _valid_candle() -> dict:
    return {
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


def _valid_signal() -> dict:
    return {
        "signal_id": str(uuid.uuid4()),
        "symbol": "BTC/USDT",
        "action": "BUY",
        "confidence": 0.73,
        "model_version": "novaquant-v1.2.3",
        "regime": "TRENDING",
        "timestamp": 1718000000000,
        "schema_version": 1,
    }


def _valid_order() -> dict:
    sig_id = str(uuid.uuid4())
    return {
        "order_id": str(uuid.uuid4()),
        "signal_id": sig_id,
        "symbol": "BTC/USDT",
        "side": "BUY",
        "order_type": "MARKET",
        "amount": "0.01",
        "idempotency_key": f"exec:{sig_id}",
        "timestamp": 1718000000000,
        "schema_version": 1,
    }


def _valid_fill() -> dict:
    return {
        "fill_id": str(uuid.uuid4()),
        "order_id": str(uuid.uuid4()),
        "symbol": "BTC/USDT",
        "side": "BUY",
        "filled_qty": "0.01",
        "avg_price": "45100.00",
        "status": "FILLED",
        "exchange_order_id": "binance-order-12345",
        "timestamp": 1718000000001,
        "schema_version": 1,
    }


# ── Candle tests ───────────────────────────────────────────────────


class TestCandleValidation:
    def test_valid_candle_passes(self):
        assert validate_candle(_valid_candle()) == []

    def test_missing_required_fields_fails(self):
        errs = validate_candle({})
        fields = {e.field for e in errs}
        assert "symbol" in fields
        assert "open" in fields
        assert "timestamp" in fields

    def test_wrong_type_fails(self):
        p = _valid_candle()
        p["timestamp"] = "not_an_int"
        errs = validate_candle(p)
        assert any(e.field == "timestamp" for e in errs)

    def test_bad_symbol_pattern_fails(self):
        p = _valid_candle()
        p["symbol"] = "btc/usdt"
        errs = validate_candle(p)
        assert any(e.field == "symbol" for e in errs)

    def test_bad_timeframe_enum_fails(self):
        p = _valid_candle()
        p["timeframe"] = "2h"
        errs = validate_candle(p)
        assert any(e.field == "timeframe" for e in errs)


# ── Signal tests ───────────────────────────────────────────────────


class TestSignalValidation:
    def test_valid_signal_passes(self):
        assert validate_signal(_valid_signal()) == []

    def test_missing_required_fails(self):
        errs = validate_signal({})
        assert any(e.field == "signal_id" for e in errs)
        assert any(e.field == "action" for e in errs)

    def test_confidence_range_fails(self):
        p = _valid_signal()
        p["confidence"] = 1.5
        errs = validate_signal(p)
        assert any(e.field == "confidence" for e in errs)

        p["confidence"] = -0.1
        errs = validate_signal(p)
        assert any(e.field == "confidence" for e in errs)

    def test_bad_action_enum_fails(self):
        p = _valid_signal()
        p["action"] = "HOLD"
        errs = validate_signal(p)
        assert any(e.field == "action" for e in errs)

    def test_bad_regime_enum_fails(self):
        p = _valid_signal()
        p["regime"] = "UNKNOWN_REGIME"
        errs = validate_signal(p)
        assert any(e.field == "regime" for e in errs)


# ── Order tests ────────────────────────────────────────────────────


class TestOrderValidation:
    def test_valid_order_passes(self):
        assert validate_order(_valid_order()) == []

    def test_idempotency_key_prefix_fails(self):
        p = _valid_order()
        p["idempotency_key"] = "wrong:prefix"
        errs = validate_order(p)
        assert any(e.field == "idempotency_key" for e in errs)

    def test_bad_side_enum_fails(self):
        p = _valid_order()
        p["side"] = "HOLD"
        errs = validate_order(p)
        assert any(e.field == "side" for e in errs)

    def test_bad_order_type_fails(self):
        p = _valid_order()
        p["order_type"] = "LIMIT"
        errs = validate_order(p)
        assert any(e.field == "order_type" for e in errs)


# ── Fill tests ─────────────────────────────────────────────────────


class TestFillValidation:
    def test_valid_fill_passes(self):
        assert validate_fill(_valid_fill()) == []

    def test_missing_required_fails(self):
        errs = validate_fill({})
        assert any(e.field == "fill_id" for e in errs)
        assert any(e.field == "filled_qty" for e in errs)
        assert any(e.field == "avg_price" for e in errs)

    def test_bad_status_enum_fails(self):
        p = _valid_fill()
        p["status"] = "PENDING"
        errs = validate_fill(p)
        assert any(e.field == "status" for e in errs)


# ── Edge cases ─────────────────────────────────────────────────────


class TestEdgeCases:
    def test_schema_version_const_enforced(self):
        for validate_fn, make_valid in [
            (validate_candle, _valid_candle),
            (validate_signal, _valid_signal),
            (validate_order, _valid_order),
            (validate_fill, _valid_fill),
        ]:
            p = make_valid()
            p["schema_version"] = 2
            errs = validate_fn(p)
            assert any(e.field == "schema_version" for e in errs), f"{validate_fn.__name__} did not reject wrong version"

    def test_extra_fields_ignored(self):
        p = _valid_candle()
        p["extra_field"] = "ignored"
        assert validate_candle(p) == []

    def test_all_validators_reject_empty(self):
        for validate_fn in [validate_candle, validate_signal, validate_order, validate_fill, validate_heartbeat, validate_error]:
            errs = validate_fn({})
            assert len(errs) > 0, f"{validate_fn.__name__} did not reject empty payload"
