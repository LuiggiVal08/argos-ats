"""Signal quality metrics tracker.

Tracks signal rate (BUY/SELL distribution), confidence distribution,
regime breakdown, and signal entropy for observability.
"""
from __future__ import annotations

import json
import logging
import math
import os
import uuid
from collections import Counter
from dataclasses import dataclass, field as dc_field
from typing import Any

import redis.asyncio as aioredis

log = logging.getLogger(__name__)


@dataclass
class SignalMetricsSnapshot:
    total_signals: int = 0
    buy_count: int = 0
    sell_count: int = 0
    avg_confidence: float = 0.0
    confidence_std: float = 0.0
    regime_breakdown: dict[str, int] = dc_field(default_factory=dict)
    signal_entropy: float = 0.0
    contract_violations: int = 0


class SignalMetricsCollector:
    """Collects and publishes signal quality metrics."""

    def __init__(
        self,
        redis_url: str | None = None,
        stream_key: str = "system:metrics",
    ) -> None:
        self._redis_url = redis_url or os.environ.get(
            "ARGOS_BROKER_URL", "redis://localhost:6379"
        )
        self._stream = stream_key
        self._client: aioredis.Redis | None = None

        self._total_signals = 0
        self._buy_count = 0
        self._sell_count = 0
        self._confidences: list[float] = []
        self._regime_counts: Counter = Counter()
        self._contract_violations = 0
        self._max_confidences = 200

    def record_signal(
        self,
        action: str,
        confidence: float,
        regime: str | None = None,
    ) -> None:
        self._total_signals += 1
        if action == "BUY":
            self._buy_count += 1
        elif action == "SELL":
            self._sell_count += 1

        self._confidences.append(confidence)
        if len(self._confidences) > self._max_confidences:
            self._confidences.pop(0)

        if regime:
            self._regime_counts[regime] += 1

    def record_violation(self) -> None:
        self._contract_violations += 1

    def signal_rate(self) -> float:
        if self._total_signals == 0:
            return 0.0
        return self._buy_count / self._total_signals

    def avg_confidence(self) -> float:
        if not self._confidences:
            return 0.0
        return sum(self._confidences) / len(self._confidences)

    def confidence_std(self) -> float:
        if len(self._confidences) < 2:
            return 0.0
        avg = self.avg_confidence()
        variance = sum((c - avg) ** 2 for c in self._confidences) / len(self._confidences)
        return math.sqrt(variance)

    def signal_entropy(self) -> float:
        if self._total_signals == 0:
            return 0.0
        p_buy = self._buy_count / self._total_signals
        p_sell = self._sell_count / self._total_signals
        entropy = 0.0
        if p_buy > 0:
            entropy -= p_buy * math.log2(p_buy)
        if p_sell > 0:
            entropy -= p_sell * math.log2(p_sell)
        return entropy

    def snapshot(self) -> SignalMetricsSnapshot:
        return SignalMetricsSnapshot(
            total_signals=self._total_signals,
            buy_count=self._buy_count,
            sell_count=self._sell_count,
            avg_confidence=self.avg_confidence(),
            confidence_std=self.confidence_std(),
            regime_breakdown=dict(self._regime_counts),
            signal_entropy=self.signal_entropy(),
            contract_violations=self._contract_violations,
        )

    def to_payload(self) -> dict[str, Any]:
        snap = self.snapshot()
        return {
            "metric_id": str(uuid.uuid4()),
            "service": "analytics-engine",
            "metric": "signal_quality",
            "total_signals": snap.total_signals,
            "buy_count": snap.buy_count,
            "sell_count": snap.sell_count,
            "signal_rate": self.signal_rate(),
            "avg_confidence": snap.avg_confidence,
            "confidence_std": snap.confidence_std,
            "regime_breakdown": snap.regime_breakdown,
            "signal_entropy": snap.signal_entropy,
            "contract_violations": snap.contract_violations,
            "timestamp": int(__import__("time").time() * 1000),
        }

    async def publish(self) -> None:
        if self._client is None:
            self._client = aioredis.from_url(self._redis_url)
        payload = self.to_payload()
        try:
            await self._client.xadd(self._stream, {"p": json.dumps(payload)})
            log.info("signal_metrics_published", stream=self._stream)
        except Exception as exc:
            log.warning("signal_metrics_publish_failed", error=str(exc))

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                log.warning("redis_close_failed", exc_info=True)
