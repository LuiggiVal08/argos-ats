"""Redis notifier: publishes notification events to a Redis stream."""
from __future__ import annotations

import json
import os

import structlog

from ...domain.value_objects.notification import NotificationEvent
from ...application.ports.notifier import Notifier

log = structlog.get_logger()

_DEFAULT_STREAM = "notifications:events"


class RedisNotifier(Notifier):
    def __init__(self, redis_url: str | None = None, stream: str = _DEFAULT_STREAM) -> None:
        self._url = redis_url or os.environ.get("ARGOS_BROKER_URL", "redis://localhost:6379")
        self._stream = stream
        self._client: Any = None

    async def _ensure_client(self) -> Any:
        if self._client is None:
            import redis.asyncio as aioredis
            self._client = aioredis.from_url(self._url)
        return self._client

    async def send(self, event: NotificationEvent) -> None:
        try:
            client = await self._ensure_client()
            payload = json.dumps({
                "event_type": event.event_type.value,
                "severity": event.severity.value,
                "title": event.title,
                "message": event.message,
                "symbol": event.symbol,
                "metadata": event.metadata,
                "timestamp": event.timestamp.isoformat(),
            })
            await client.xadd(self._stream, {"p": payload})
            log.info("notification_published", stream=self._stream, event_type=event.event_type.value)
        except Exception as e:
            log.warning("redis_notifier_failed", error=str(e), stream=self._stream)

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                log.warning("redis_close_failed", exc_info=True)


from typing import Any
