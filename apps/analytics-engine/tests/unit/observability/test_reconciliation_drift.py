"""Tests for reconciliation_drift.py — drift event tracking."""
import pytest
from app.observability.reconciliation_drift import ReconciliationDriftTracker, DriftEvent


class TestReconciliationDriftTracker:
    def test_initial_state(self):
        t = ReconciliationDriftTracker()
        snap = t.snapshot()
        assert snap["total_drift"] == 0
        assert snap["drift_count_last_1h"] == 0
        assert snap["drift_count_last_24h"] == 0
        assert snap["max_drift_pct"] == 0.0

    def test_record_drift_duplicate_symbol(self):
        t = ReconciliationDriftTracker()
        t.record_drift("duplicate_symbol", "BTC/USDT", "multiple positions")
        assert t.total_drift == 1
        assert len(t._events) == 1
        assert t._events[0].event_type == "duplicate_symbol"
        assert t._events[0].symbol == "BTC/USDT"

    def test_record_drift_missing_on_exchange(self):
        t = ReconciliationDriftTracker()
        t.record_drift("missing_on_exchange", "ETH/USDT", local_units="0.1")
        assert t.total_drift == 1

    def test_record_drift_missing_local(self):
        t = ReconciliationDriftTracker()
        t.record_drift("missing_local", "SOL/USDT", exchange_units="0.5")
        assert t.total_drift == 1

    def test_record_drift_partial_mismatch(self):
        t = ReconciliationDriftTracker()
        t.record_drift(
            "partial_mismatch", "BTC/USDT",
            local_units="0.1", exchange_units="0.099",
        )
        assert t.total_drift == 1

    def test_max_drift_pct_calculation(self):
        t = ReconciliationDriftTracker()
        t.record_drift(
            "partial_mismatch", "BTC/USDT",
            local_units="0.1", exchange_units="0.099",
        )
        # drift = |0.1 - 0.099| / max(0.1, 0.099) = 0.001 / 0.1 = 0.01
        assert t.max_drift_pct == pytest.approx(0.01, rel=1e-6)

    def test_max_drift_pct_no_units(self):
        t = ReconciliationDriftTracker()
        t.record_drift("missing_on_exchange", "BTC/USDT")
        assert t.max_drift_pct == 0.0

    def test_max_drift_pct_no_events(self):
        t = ReconciliationDriftTracker()
        assert t.max_drift_pct == 0.0

    def test_snapshot(self):
        t = ReconciliationDriftTracker()
        t.record_drift("duplicate_symbol", "BTC/USDT")
        snap = t.snapshot()
        assert snap["total_drift"] == 1
        assert snap["max_drift_pct"] == 0.0

    def test_emit_snapshot_does_not_raise(self):
        t = ReconciliationDriftTracker()
        t.emit_snapshot()

    def test_multiple_events(self):
        t = ReconciliationDriftTracker()
        for i in range(3):
            t.record_drift("duplicate_symbol", f"SYM/{i}")
        assert t.total_drift == 3

    def test_event_has_timestamp(self):
        t = ReconciliationDriftTracker()
        t.record_drift("duplicate_symbol", "BTC/USDT")
        assert t._events[0].timestamp > 0


class TestDriftEvent:
    def test_create_drift_event(self):
        e = DriftEvent(
            timestamp=1000.0,
            event_type="duplicate_symbol",
            symbol="BTC/USDT",
            detail="multiple positions",
            local_units="0.1",
            exchange_units="0.0",
        )
        assert e.event_type == "duplicate_symbol"
        assert e.symbol == "BTC/USDT"
        assert e.detail == "multiple positions"
