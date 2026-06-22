"""AE system health aggregator.

Publishes a system:metrics snapshot every 30s with signal quality,
pipeline latency, and contract violation data.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, Callable

import redis.asyncio as aioredis

from .signal_metrics import SignalMetricsCollector
from .pipeline_metrics import PipelineMetricsCollector

log = logging.getLogger(__name__)


class AeSystemHealthService:
    """Periodic system health publisher for analytics-engine."""

    def __init__(
        self,
        redis_url: str,
        signal_metrics: SignalMetricsCollector,
        pipeline_metrics: PipelineMetricsCollector,
        stream_key: str = "system:metrics",
        interval_seconds: int = 30,
    ) -> None:
        self._url = redis_url
        self._signal = signal_metrics
        self._pipeline = pipeline_metrics
        self._stream = stream_key
        self._interval = interval_seconds
        self._client: aioredis.Redis | None = None
        self._start_time = time.time()
        self._task: asyncio.Task | None = None

    async def _ensure_client(self) -> aioredis.Redis:
        if self._client is None:
            self._client = aioredis.from_url(self._url)
        return self._client

    def snapshot(self) -> dict[str, Any]:
        return {
            "metric_id": str(uuid.uuid4()),
            "service": "analytics-engine",
            "status": "healthy",
            "signal_rate": self._signal.signal_rate(),
            "avg_confidence": self._signal.avg_confidence(),
            "avg_inference_ms": round(self._pipeline.avg_inference_ms(), 2),
            "pipeline_lag_ms": round(self._pipeline.total_pipeline_lag_ms(), 2),
            "contract_violation_rate": round(
                self._signal.snapshot().contract_violations
                / max(1, self._signal.snapshot().total_signals),
                4,
            ),
            "uptime_seconds": int(time.time() - self._start_time),
            "timestamp": int(time.time() * 1000),
        }

    async def publish(self) -> None:
        client = await self._ensure_client()
        payload = self.snapshot()
        try:
            await client.xadd(self._stream, {"p": json.dumps(payload)})
        except Exception as exc:
            log.warning("ae_health_publish_failed", error=str(exc))

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self.publish()
            except Exception:
                log.warning("ae_health_loop_error", exc_info=True)

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop())
        log.info("ae_system_health_started", interval_seconds=self._interval)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                log.warning("redis_close_failed", exc_info=True)
