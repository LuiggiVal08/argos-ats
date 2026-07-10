"""Tests for system_health.py — aggregated snapshot."""
from app.observability.system_health import emit_system_health_snapshot
from app.observability.sl_integrity import SlIntegrityTracker
from app.observability.reconciliation_drift import ReconciliationDriftTracker
from app.observability.execution_integrity import ExecutionIntegrityTracker


class TestEmitSystemHealthSnapshot:
    def test_basic_snapshot_no_trackers(self):
        emit_system_health_snapshot(
            open_positions_count=2,
            algo_api_healthy=True,
            exchange_connected=True,
            pipeline_loaded=True,
            recovery_state="COMPLETED",
        )

    def test_snapshot_with_sl_tracker(self):
        t = SlIntegrityTracker()
        t.record_created("algo-1", "BTC/USDT")
        t.record_verified("algo-1", "BTC/USDT")
        emit_system_health_snapshot(sl_tracker=t)

    def test_snapshot_with_drift_tracker(self):
        t = ReconciliationDriftTracker()
        t.record_drift("duplicate_symbol", "BTC/USDT")
        emit_system_health_snapshot(drift_tracker=t)

    def test_snapshot_with_execution_tracker(self):
        t = ExecutionIntegrityTracker()
        t.record_execution("sig-1", 100.0)
        emit_system_health_snapshot(execution_tracker=t)

    def test_snapshot_with_all_trackers(self):
        sl = SlIntegrityTracker()
        sl.record_created("algo-1", "BTC/USDT")
        sl.record_verified("algo-1", "BTC/USDT")

        drift = ReconciliationDriftTracker()

        exec_t = ExecutionIntegrityTracker()
        exec_t.record_execution("sig-1", 100.0)

        emit_system_health_snapshot(
            sl_tracker=sl,
            drift_tracker=drift,
            execution_tracker=exec_t,
            open_positions_count=1,
            algo_api_healthy=True,
            exchange_connected=True,
            pipeline_loaded=True,
            recovery_state="COMPLETED",
        )

    def test_snapshot_critical_when_algo_unhealthy(self):
        emit_system_health_snapshot(
            algo_api_healthy=False,
        )

    def test_snapshot_with_extra_context(self):
        emit_system_health_snapshot(
            open_positions_count=3,
            custom_field="test_value",
            environment_mode="PAPER_TRADING",
        )
