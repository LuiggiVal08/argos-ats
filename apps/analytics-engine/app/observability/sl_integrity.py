"""SL Integrity Report — tracks SL lifecycle metrics.

Measures:
- SL created count
- SL verified (is_order_active == True)
- SL missing (is_order_active == False)
- SL cancellation success / failure
- sl_success_rate = verified / created over window
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .schema import emit_metric


@dataclass
class SlIntegrityTracker:
    """Tracks SL operations and emits structured metrics.
    
    Pure instrumentation — no side effects on execution logic.
    """
    _sl_created: int = 0
    _sl_verified: int = 0
    _sl_missing: int = 0
    _sl_cancelled: int = 0
    _sl_cancel_failed: int = 0
    _sl_verify_errors: int = 0
    _window_start: float = 0.0

    def __post_init__(self) -> None:
        if self._window_start == 0.0:
            object.__setattr__(self, "_window_start", datetime.now(timezone.utc).timestamp())

    def record_created(self, algo_id: str, symbol: str, **extra: str) -> None:
        self._sl_created += 1
        emit_metric(
            "sl.created",
            value=algo_id,
            threshold="n/a",
            level="INFO",
            symbol=symbol,
            algo_id=algo_id,
            **extra,
        )

    def record_verified(self, algo_id: str, symbol: str, latency_ms: float = 0.0) -> None:
        self._sl_verified += 1
        emit_metric(
            "sl.verify.success",
            value=algo_id,
            threshold="algo API returns active",
            level="INFO",
            symbol=symbol,
            algo_id=algo_id,
            latency_ms=round(latency_ms, 1),
        )

    def record_missing(self, algo_id: str, symbol: str, latency_ms: float = 0.0) -> None:
        self._sl_missing += 1
        rate = self.success_rate
        emit_metric(
            "sl.verify.missing",
            value=algo_id,
            threshold="expected active in Algo API",
            level="WARNING" if rate > 0.97 else "CRITICAL",
            symbol=symbol,
            algo_id=algo_id,
            latency_ms=round(latency_ms, 1),
            success_rate=round(rate, 4),
        )

    def record_verify_error(self, algo_id: str, symbol: str, error: str) -> None:
        self._sl_verify_errors += 1
        emit_metric(
            "sl.verify.error",
            value=error,
            threshold="0 errors",
            level="WARNING",
            symbol=symbol,
            algo_id=algo_id,
            error=error,
        )

    def record_cancelled(self, algo_id: str, symbol: str) -> None:
        self._sl_cancelled += 1
        emit_metric(
            "sl.cancel.success",
            value=algo_id,
            threshold="n/a",
            level="INFO",
            symbol=symbol,
            algo_id=algo_id,
        )

    def record_cancel_failed(self, algo_id: str, symbol: str, error: str) -> None:
        self._sl_cancel_failed += 1
        emit_metric(
            "sl.cancel.failed",
            value=error,
            threshold="0 failures",
            level="WARNING",
            symbol=symbol,
            algo_id=algo_id,
            error=error,
        )

    @property
    def success_rate(self) -> float:
        total_attempts = self._sl_created
        if total_attempts == 0:
            return 1.0
        return self._sl_verified / total_attempts

    @property
    def total_operations(self) -> int:
        return self._sl_created + self._sl_cancelled

    def snapshot(self) -> dict:
        total = self._sl_created + self._sl_missing + self._sl_verify_errors
        return {
            "sl_created": self._sl_created,
            "sl_verified": self._sl_verified,
            "sl_missing": self._sl_missing,
            "sl_cancelled": self._sl_cancelled,
            "sl_cancel_failed": self._sl_cancel_failed,
            "sl_verify_errors": self._sl_verify_errors,
            "sl_success_rate": round(self.success_rate, 4),
            "total_operations": self.total_operations,
        }

    def emit_snapshot(self) -> None:
        data = self.snapshot()
        emit_metric(
            "sl.integrity.snapshot",
            value=data,
            threshold="success_rate > 0.99",
            level="CRITICAL" if data["sl_success_rate"] < 0.97 else (
                "WARNING" if data["sl_success_rate"] < 0.99 else "INFO"
            ),
            **{k: v for k, v in data.items() if k != "sl_success_rate"},
        )


# Default singleton — import directly in use cases for zero-constructor-change instrumentation
default_sl_tracker = SlIntegrityTracker()
