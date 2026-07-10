"""System Health Snapshot — aggregated view of system state.

Collects data from all PO-Layer trackers and emits a single consolidated
snapshot on demand (not in a loop — caller decides frequency).

Source data is collected from tracker objects passed at call time,
not from internal state — this module is stateless.
"""
from __future__ import annotations

from typing import Any

from .schema import emit_metric


def emit_system_health_snapshot(
    sl_tracker: Any | None = None,
    drift_tracker: Any | None = None,
    execution_tracker: Any | None = None,
    open_positions_count: int = 0,
    algo_api_healthy: bool = True,
    exchange_connected: bool = True,
    pipeline_loaded: bool = True,
    recovery_state: str = "OK",
    **extra: Any,
) -> None:
    """Emit a single consolidated system health snapshot.
    
    Args:
        sl_tracker: SlIntegrityTracker instance (or None).
        drift_tracker: ReconciliationDriftTracker instance (or None).
        execution_tracker: ExecutionIntegrityTracker instance (or None).
        open_positions_count: Number of currently open positions.
        algo_api_healthy: Whether Algo API is reachable.
        exchange_connected: Whether exchange WS/REST is connected.
        pipeline_loaded: Whether the streaming pipeline is loaded.
        recovery_state: Current recovery state string.
        **extra: Additional context key-value pairs.
    """
    snapshot: dict[str, Any] = {
        "open_positions_count": open_positions_count,
        "algo_api_healthy": algo_api_healthy,
        "exchange_connected": exchange_connected,
        "pipeline_loaded": pipeline_loaded,
        "recovery_state": recovery_state,
    }

    if sl_tracker is not None:
        sl_snap = sl_tracker.snapshot()
        snapshot["sl_success_rate"] = sl_snap["sl_success_rate"]
        snapshot["sl_total_operations"] = sl_snap["total_operations"]

    if drift_tracker is not None:
        drift_snap = drift_tracker.snapshot()
        snapshot["drift_count_last_1h"] = drift_snap["drift_count_last_1h"]
        snapshot["drift_count_last_24h"] = drift_snap["drift_count_last_24h"]
        snapshot["drift_total"] = drift_snap["total_drift"]

    if execution_tracker is not None:
        exec_snap = execution_tracker.snapshot()
        snapshot["execution_count"] = exec_snap["total_executions"]
        snapshot["execution_latency_p99_ms"] = exec_snap["execution_latency_p99"]

    # Determine overall level
    has_drift = snapshot.get("drift_total", 0) > 0
    low_sl = snapshot.get("sl_success_rate", 1.0) < 0.97
    high_latency = snapshot.get("execution_latency_p99_ms", 0) > 500

    if not algo_api_healthy or low_sl:
        level = "CRITICAL"
    elif has_drift or high_latency or not exchange_connected:
        level = "WARNING"
    else:
        level = "INFO"

    emit_metric(
        "system.health.snapshot",
        value=snapshot,
        threshold="all subsystems healthy",
        level=level,
        **{**snapshot, **extra},
    )
