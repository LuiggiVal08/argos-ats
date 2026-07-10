"""Tests for execution_integrity.py — pipeline timing."""
from app.observability.execution_integrity import ExecutionIntegrityTracker


class TestExecutionIntegrityTracker:
    def test_initial_state(self):
        t = ExecutionIntegrityTracker()
        snap = t.snapshot()
        assert snap["total_executions"] == 0
        assert snap["execution_latency_p50"] == 0.0
        assert snap["execution_latency_p99"] == 0.0

    def test_record_execution(self):
        t = ExecutionIntegrityTracker()
        t.record_execution("sig-1", 150.0)
        snap = t.snapshot()
        assert snap["total_executions"] == 1
        assert snap["execution_latency_p50"] == 150.0
        assert snap["execution_latency_p99"] == 150.0

    def test_record_sl_placement(self):
        t = ExecutionIntegrityTracker()
        t.record_execution("sig-1", 100.0)
        t.record_sl_placement("sig-1", 50.0)
        snap = t.snapshot()
        assert snap["total_executions"] == 1
        assert snap["total_latency_p50"] == 150.0

    def test_record_sl_verify(self):
        t = ExecutionIntegrityTracker()
        t.record_execution("sig-1", 100.0)
        t.record_sl_placement("sig-1", 50.0)
        t.record_sl_verify("sig-1", 20.0, sl_found=True)
        snap = t.snapshot()
        assert snap["total_executions"] == 1
        assert snap["total_latency_p50"] == 170.0

    def test_multiple_executions_p50_p99(self):
        t = ExecutionIntegrityTracker()
        for i in range(1, 101):
            t.record_execution(f"sig-{i}", float(i))
        snap = t.snapshot()
        # p50 ~ 50.5, p99 ~ 99
        assert 45 <= snap["execution_latency_p50"] <= 55
        assert 95 <= snap["execution_latency_p99"] <= 105

    def test_record_execution_with_divergence(self):
        t = ExecutionIntegrityTracker()
        t.record_execution("sig-1", 100.0)
        assert t._timings[0].divergence_pct == 0.0

    def test_sl_verify_updates_existing_timing(self):
        t = ExecutionIntegrityTracker()
        t.record_execution("sig-1", 100.0)
        t.record_execution("sig-2", 200.0)
        t.record_sl_verify("sig-1", 15.0, sl_found=True)
        # sig-1 should have verify latency, sig-2 should not
        sig1 = [x for x in t._timings if x.signal_id == "sig-1"][0]
        sig2 = [x for x in t._timings if x.signal_id == "sig-2"][0]
        assert sig1.sl_verify_latency_ms == 15.0
        assert sig2.sl_verify_latency_ms == 0.0

    def test_p_percentile_empty(self):
        t = ExecutionIntegrityTracker()
        assert t._p_percentile([], 50) == 0.0
        assert t._p_percentile([], 99) == 0.0

    def test_emit_snapshot_does_not_raise(self):
        t = ExecutionIntegrityTracker()
        t.record_execution("sig-1", 100.0)
        t.emit_snapshot()
