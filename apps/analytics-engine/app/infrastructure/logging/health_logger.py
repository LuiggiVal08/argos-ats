"""Health monitoring logger — periodic system resource metrics.

Logs every minute::

    {
        "event": "health_snapshot",
        "cpu_percent": 42.5,
        "memory_percent": 63.1,
        "disk_percent": 55.0,
        "redis_latency_ms": 1.2,
        "websocket_latency_ms": 3.4,
        "last_candle_age_seconds": 0.5,
        "inference_latency_ms": 4.2,
        "feature_latency_ms": 2.1,
        "heartbeat_status": "OK",
        "uptime_hours": 127.3
    }
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

from .logging_config import get_health_logger

HEALTH_LOG = get_health_logger()
_PROC_AVAILABLE = False

try:
    import psutil
    _PROC_AVAILABLE = True
except ImportError:
    _PROC_AVAILABLE = False

_START_TIME: float = time.time()


def _read_proc_stat() -> dict[str, float]:
    """Read CPU/memory/disk stats if psutil is available."""
    result: dict[str, float] = {
        "cpu_percent": 0.0,
        "memory_percent": 0.0,
        "disk_percent": 0.0,
    }
    if not _PROC_AVAILABLE:
        return result
    try:
        result["cpu_percent"] = round(psutil.cpu_percent(interval=0.1), 1)
        result["memory_percent"] = round(psutil.virtual_memory().percent, 1)
        disk = psutil.disk_usage(os.sep)
        result["disk_percent"] = round(disk.used / disk.total * 100, 1)
    except Exception:
        pass
    return result


def log_health_snapshot(
    *,
    redis_latency_ms: float = -1.0,
    websocket_latency_ms: float = -1.0,
    last_candle_age_seconds: float = -1.0,
    inference_latency_ms: float = -1.0,
    feature_latency_ms: float = -1.0,
    heartbeat_status: str = "UNKNOWN",
    **extra: Any,
) -> dict[str, Any]:
    """Log a periodic health snapshot.

    Returns the logged payload for testing/verification.
    """
    proc = _read_proc_stat()
    uptime_s = time.time() - _START_TIME

    payload: dict[str, Any] = {
        "event": "health_snapshot",
        "cpu_percent": proc["cpu_percent"],
        "memory_percent": proc["memory_percent"],
        "disk_percent": proc["disk_percent"],
        "redis_latency_ms": round(redis_latency_ms, 2),
        "websocket_latency_ms": round(websocket_latency_ms, 2),
        "last_candle_age_seconds": round(last_candle_age_seconds, 3),
        "inference_latency_ms": round(inference_latency_ms, 3),
        "feature_latency_ms": round(feature_latency_ms, 3),
        "heartbeat_status": heartbeat_status,
        "uptime_hours": round(uptime_s / 3600, 2),
    }
    payload.update(extra)
    HEALTH_LOG.info("health_snapshot", **payload)
    return payload


def log_system_startup(startup_payload: dict[str, Any] | None = None) -> None:
    """Log a SYSTEM_START event."""
    payload: dict[str, Any] = {
        "uptime_hours": 0.0,
        "pid": os.getpid(),
    }
    if startup_payload:
        payload.update(startup_payload)
    HEALTH_LOG.info("SYSTEM_START", **payload)


def log_system_shutdown(reason: str = "graceful", uptime_seconds: float | None = None) -> None:
    """Log a SYSTEM_STOP event."""
    uptime_s = uptime_seconds if uptime_seconds is not None else (time.time() - _START_TIME)
    payload: dict[str, Any] = {
        "reason": reason,
        "uptime_hours": round(uptime_s / 3600, 2),
    }
    HEALTH_LOG.info("SYSTEM_STOP", **payload)
