"""Standardized metric schema for the PO-Layer.

Every metric event follows this schema and is emitted via structlog.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import structlog

METRICS_LOG = structlog.get_logger("po_layer")

METRIC_LEVELS = ("INFO", "WARNING", "CRITICAL", "FREEZE")


@dataclass(frozen=True)
class MetricEvent:
    event: str = "po_layer_metric"
    metric: str = ""
    value: Any = None
    threshold: str = ""
    level: str = "INFO"
    timestamp: str = ""

    def to_log(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "metric": self.metric,
            "value": self.value,
            "threshold": self.threshold,
            "level": self.level,
            "timestamp": self.timestamp or datetime.now(timezone.utc).isoformat(),
        }


def emit_metric(
    metric: str,
    value: Any,
    threshold: str = "",
    level: str = "INFO",
    **extra: Any,
) -> None:
    """Emit a standardized PO-Layer metric event.

    Args:
        metric: Metric name (dot-separated, e.g. 'sl.verify.success').
        value: Metric value (int, float, str, bool, or JSON-serializable).
        threshold: Expected threshold description (e.g. '>99%').
        level: INFO | WARNING | CRITICAL | FREEZE.
        **extra: Additional context key-value pairs.
    """
    if level not in METRIC_LEVELS:
        level = "INFO"

    payload = {
        "event": "po_layer_metric",
        "metric": metric,
        "value": value,
        "threshold": threshold,
        "level": level,
        **extra,
    }

    if level == "CRITICAL":
        METRICS_LOG.critical(**payload)
    elif level == "WARNING":
        METRICS_LOG.warning(**payload)
    elif level == "FREEZE":
        METRICS_LOG.critical(**{**payload, "_freeze_signal": True})
    else:
        METRICS_LOG.info(**payload)
