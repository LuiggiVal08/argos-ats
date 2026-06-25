from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
import structlog

from ..application.use_cases.collect_telemetry import (
    CollectTelemetryUseCase,
    RecordTelemetryUseCase,
)
from ..application.use_cases.report_incident_extended import (
    GetDisasterStatusUseCase,
    RecoverFromIncidentUseCase,
    ReportIncidentExtendedUseCase,
)
from ..application.use_cases.update_dashboard import (
    GetDashboardUseCase,
    GetDashboardHistoryUseCase,
    UpdateDashboardUseCase,
)
from ..composition import (
    get_collect_telemetry_usecase,
    get_record_telemetry_usecase,
    get_disaster_status_usecase,
    get_report_incident_extended_usecase,
    get_recover_from_incident_usecase,
    get_dashboard_usecase,
    get_dashboard_history_usecase,
    get_update_dashboard_usecase,
)

log = structlog.get_logger()
_error_counters: dict[str, int] = {}

router = APIRouter(prefix="/observability", tags=["observability"])


@router.get("/telemetry", summary="Get current telemetry snapshot")
async def get_telemetry(request: Request) -> dict:
    uc: CollectTelemetryUseCase = get_collect_telemetry_usecase(request)
    result = await uc.execute()
    return {
        "snapshot": {
            "data_engine": result.snapshot.data_engine,
            "analytics_engine": result.snapshot.analytics_engine,
            "execution_engine": result.snapshot.execution_engine,
            "training_engine": result.snapshot.training_engine,
            "timestamp": result.snapshot.timestamp,
        },
        "recent_metrics": result.recent_metrics,
    }


@router.post("/telemetry/record", summary="Record telemetry metrics")
async def record_telemetry(
    request: Request,
    data_engine: dict[str, float] | None = None,
    analytics_engine: dict[str, float] | None = None,
    execution_engine: dict[str, float] | None = None,
    training_engine: dict[str, float] | None = None,
) -> dict:
    uc: RecordTelemetryUseCase = get_record_telemetry_usecase(request)
    await uc.record_all(
        data_engine=data_engine,
        analytics_engine=analytics_engine,
        execution_engine=execution_engine,
        training_engine=training_engine,
    )
    return {"status": "ok"}


@router.get("/dashboard", summary="Get current dashboard state")
async def get_dashboard(request: Request) -> dict:
    uc: GetDashboardUseCase = get_dashboard_usecase(request)
    panel = await uc.execute()
    return {
        "market": panel.market,
        "ai": panel.ai,
        "risk": panel.risk,
        "training": panel.training,
        "updated_at": panel.updated_at,
    }


@router.get("/dashboard/history", summary="Get dashboard history")
async def get_dashboard_history(request: Request, limit: int = 100) -> list[dict]:
    uc: GetDashboardHistoryUseCase = get_dashboard_history_usecase(request)
    return await uc.execute(limit=limit)


@router.post("/dashboard/update", summary="Update dashboard panels")
async def update_dashboard(
    request: Request,
    market: dict[str, Any] | None = None,
    ai: dict[str, Any] | None = None,
    risk: dict[str, Any] | None = None,
    training: dict[str, Any] | None = None,
) -> dict:
    uc: UpdateDashboardUseCase = get_update_dashboard_usecase(request)
    await uc.execute(market=market, ai=ai, risk=risk, training=training)
    return {"status": "ok"}


@router.get("/disaster/status", summary="Get disaster recovery status")
async def disaster_status(request: Request) -> dict:
    uc: GetDisasterStatusUseCase = get_disaster_status_usecase(request)
    status = await uc.execute()
    return {
        "mode": status.mode,
        "total_incidents": status.total_incidents,
        "consecutive_failures": status.consecutive_failures,
        "unrecovered": status.unrecovered,
    }


@router.post("/disaster/report", summary="Report an incident to disaster recovery")
async def report_incident(
    request: Request,
    event_type: str,
    severity: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict:
    uc: ReportIncidentExtendedUseCase = get_report_incident_extended_usecase(request)
    event = await uc.execute(event_type, severity, message, details)
    return {
        "event_type": event.event_type,
        "severity": event.severity.value,
        "message": event.message,
        "timestamp": event.timestamp,
    }


@router.post("/disaster/recover", summary="Recover from an incident")
async def recover_from_incident(request: Request, event_type: str) -> dict:
    uc: RecoverFromIncidentUseCase = get_recover_from_incident_usecase(request)
    result = await uc.execute(event_type)
    return result


@router.get("/trading", summary="Get current trading status")
async def trading_status(request: Request) -> dict:
    comp = request.app.state.composition

    positions = []
    if comp.position_repo is not None:
        try:
            positions = await comp.position_repo.list_all()
        except Exception:
            _error_counters["list_positions"] = _error_counters.get("list_positions", 0) + 1
            if _error_counters["list_positions"] % 50 == 0:
                log.warning("observability_fallback", component="position_repo", count=_error_counters["list_positions"])
            positions = []

    drawdown_status: dict = {"equity": None, "drawdown_pct": None, "halted": None}
    if comp.check_drawdown is not None:
        try:
            snap = await comp.check_drawdown.load_current_snapshot()
            halted = await comp.check_drawdown.is_halted()
            if snap is not None:
                drawdown_status = {
                    "equity": float(snap.current_balance),
                    "drawdown_pct": float(snap.drawdown_pct),
                    "halted": halted,
                }
            else:
                drawdown_status = {"equity": None, "drawdown_pct": None, "halted": halted}
        except Exception:
            _error_counters["drawdown"] = _error_counters.get("drawdown", 0) + 1
            if _error_counters["drawdown"] % 50 == 0:
                log.warning("observability_fallback", component="drawdown", count=_error_counters["drawdown"])
            drawdown_status = {"equity": None, "drawdown_pct": None, "halted": None}

    candles = []
    if comp.streaming is not None and comp.streaming.candle_buffer is not None:
        try:
            candles = comp.streaming.candle_buffer.to_ohlcv_dicts()
        except Exception:
            _error_counters["candles"] = _error_counters.get("candles", 0) + 1
            if _error_counters["candles"] % 50 == 0:
                log.warning("observability_fallback", component="candles", count=_error_counters["candles"])
            candles = []

    pipeline_info: dict = {"is_loaded": False}
    if comp.streaming is not None and comp.streaming.inference_pipeline is not None:
        try:
            pipeline_info = {
                "is_loaded": comp.streaming.inference_pipeline.is_loaded,
            }
        except Exception:
            _error_counters["pipeline"] = _error_counters.get("pipeline", 0) + 1
            if _error_counters["pipeline"] % 50 == 0:
                log.warning("observability_fallback", component="pipeline", count=_error_counters["pipeline"])
            pipeline_info = {"is_loaded": False}

    return {
        "mode": comp.mode,
        "drawdown": drawdown_status,
        "positions": {
            "count": len(positions),
            "open": positions[:5],
        },
        "pipeline": pipeline_info,
        "candles": len(candles),
        "recovery": {
            "blocked": getattr(comp, "recovery_blocked", False),
            "gate_state": comp.recovery_report.gate.state.value if comp.recovery_report and hasattr(comp.recovery_report, "gate") else None,
            "runtime_mode": comp.disaster_recovery.mode.value if hasattr(comp, "disaster_recovery") and comp.disaster_recovery else None,
        },
        "market_data_gap": getattr(comp.market_continuity_guard, "market_data_gap", False) if hasattr(comp, "market_continuity_guard") and comp.market_continuity_guard else None,
        "exchange": comp.exchange is not None,
    }
