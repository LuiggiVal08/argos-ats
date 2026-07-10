"""Tests for sl_integrity.py — SL lifecycle tracking."""
from app.observability.sl_integrity import SlIntegrityTracker


class TestSlIntegrityTracker:
    def test_initial_state(self):
        t = SlIntegrityTracker()
        snap = t.snapshot()
        assert snap["sl_created"] == 0
        assert snap["sl_verified"] == 0
        assert snap["sl_missing"] == 0
        assert snap["sl_cancelled"] == 0
        assert snap["sl_cancel_failed"] == 0
        assert snap["sl_verify_errors"] == 0
        assert snap["sl_success_rate"] == 1.0
        assert snap["total_operations"] == 0

    def test_record_created(self):
        t = SlIntegrityTracker()
        t.record_created("algo-1", "BTC/USDT")
        assert t._sl_created == 1
        assert t.total_operations == 1

    def test_record_verified(self):
        t = SlIntegrityTracker()
        t.record_created("algo-1", "BTC/USDT")
        t.record_verified("algo-1", "BTC/USDT", latency_ms=10.5)
        assert t._sl_verified == 1
        assert t._sl_missing == 0
        assert t.success_rate == 1.0

    def test_record_missing(self):
        t = SlIntegrityTracker()
        t.record_created("algo-1", "BTC/USDT")
        t.record_missing("algo-1", "BTC/USDT", latency_ms=5.0)
        assert t._sl_missing == 1
        # verified=0, created=1 → success_rate = 0/1 = 0.0
        assert t.success_rate == 0.0

    def test_record_cancelled(self):
        t = SlIntegrityTracker()
        t.record_cancelled("algo-1", "BTC/USDT")
        assert t._sl_cancelled == 1
        assert t.total_operations == 1

    def test_record_cancel_failed(self):
        t = SlIntegrityTracker()
        t.record_cancel_failed("algo-1", "BTC/USDT", "timeout")
        assert t._sl_cancel_failed == 1

    def test_record_verify_error(self):
        t = SlIntegrityTracker()
        t.record_verify_error("algo-1", "BTC/USDT", "connection error")
        assert t._sl_verify_errors == 1

    def test_success_rate_no_operations(self):
        t = SlIntegrityTracker()
        assert t.success_rate == 1.0

    def test_success_rate_partial(self):
        t = SlIntegrityTracker()
        t.record_created("a1", "BTC/USDT")
        t.record_verified("a1", "BTC/USDT")
        t.record_created("a2", "ETH/USDT")
        assert t.success_rate == 0.5

    def test_snapshot_returns_dict(self):
        t = SlIntegrityTracker()
        t.record_created("a1", "BTC/USDT")
        t.record_verified("a1", "BTC/USDT")
        t.record_cancelled("a2", "ETH/USDT")
        snap = t.snapshot()
        assert snap["sl_created"] == 1
        assert snap["sl_verified"] == 1
        assert snap["sl_cancelled"] == 1
        assert snap["sl_success_rate"] == 1.0

    def test_emit_snapshot_does_not_raise(self):
        t = SlIntegrityTracker()
        t.record_created("a1", "BTC/USDT")
        t.emit_snapshot()

    def test_multiple_operations_maintain_counts(self):
        t = SlIntegrityTracker()
        for i in range(5):
            t.record_created(f"algo-{i}", "BTC/USDT")
            t.record_verified(f"algo-{i}", "BTC/USDT")
        for i in range(3):
            t.record_cancelled(f"algo-{i}", "BTC/USDT")
        assert t._sl_created == 5
        assert t._sl_verified == 5
        assert t._sl_cancelled == 3
        assert t.success_rate == 1.0
