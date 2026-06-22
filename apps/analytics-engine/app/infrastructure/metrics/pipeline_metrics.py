"""Pipeline latency metrics tracker.

Tracks end-to-end latencies: candle → signal, signal → order,
and per-model inference time.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

import redis.asyncio as aioredis

log = logging.getLogger(__name__)


class PipelineMetricsCollector:
    """Collects and publishes pipeline latency metrics."""

    def __init__(
        self,
        redis_url: str | None = None,
        stream_key: str = "system:latency",
    ) -> None:
        self._redis_url = redis_url or os.environ.get(
            "ARGOS_BROKER_URL", "redis://localhost:6379"
        )
        self._stream = stream_key
        self._client: aioredis.Redis | None = None

        self._candle_to_signal_latencies: list[float] = []
        self._signal_to_order_latencies: list[float] = []
        self._inference_times: list[float] = []
        self._max_samples = 200

    def record_candle_to_signal(self, latency_ms: float) -> None:
        self._candle_to_signal_latencies.append(latency_ms)
        if len(self._candle_to_signal_latencies) > self._max_samples:
            self._candle_to_signal_latencies.pop(0)

    def record_signal_to_order(self, latency_ms: float) -> None:
        self._signal_to_order_latencies.append(latency_ms)
        if len(self._signal_to_order_latencies) > self._max_samples:
            self._signal_to_order_latencies.pop(0)

    def record_inference_time(self, model_version: str, inference_ms: float) -> None:
        self._inference_times.append(inference_ms)
        if len(self._inference_times) > self._max_samples:
            self._inference_times.pop(0)

    def avg_candle_to_signal_ms(self) -> float:
        if not self._candle_to_signal_latencies:
            return 0.0
        return sum(self._candle_to_signal_latencies) / len(self._candle_to_signal_latencies)

    def avg_signal_to_order_ms(self) -> float:
        if not self._signal_to_order_latencies:
            return 0.0
        return sum(self._signal_to_order_latencies) / len(self._signal_to_order_latencies)

    def avg_inference_ms(self) -> float:
        if not self._inference_times:
            return 0.0
        return sum(self._inference_times) / len(self._inference_times)

    def p95_inference_ms(self) -> float:
        if not self._inference_times:
            return 0.0
        sorted_times = sorted(self._inference_times)
        idx = int(len(sorted_times) * 0.95)
        return sorted_times[min(idx, len(sorted_times) - 1)]

    def total_pipeline_lag_ms(self) -> float:
        return self.avg_candle_to_signal_ms() + self.avg_signal_to_order_ms()

    def to_payload(self) -> dict[str, Any]:
        return {
            "metric_id": str(uuid.uuid4()),
            "service": "analytics-engine",
            "metric": "pipeline_latency",
            "avg_candle_to_signal_ms": round(self.avg_candle_to_signal_ms(), 2),
            "avg_signal_to_order_ms": round(self.avg_signal_to_order_ms(), 2),
            "avg_inference_ms": round(self.avg_inference_ms(), 2),
            "p95_inference_ms": round(self.p95_inference_ms(), 2),
            "total_pipeline_lag_ms": round(self.total_pipeline_lag_ms(), 2),
            "timestamp": int(__import__("time").time() * 1000),
        }

    async def publish(self) -> None:
        if self._client is None:
            self._client = aioredis.from_url(self._redis_url)
        payload = self.to_payload()
        try:
            await self._client.xadd(self._stream, {"p": json.dumps(payload)})
            log.info("pipeline_metrics_published", stream=self._stream)
        except Exception as exc:
            log.warning("pipeline_metrics_publish_failed", error=str(exc))

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                log.warning("redis_close_failed", exc_info=True)
