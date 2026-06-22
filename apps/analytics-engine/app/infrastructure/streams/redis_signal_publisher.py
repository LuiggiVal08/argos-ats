"""Redis signal publisher with schema validation guard.

Validates trading signals against the signals-trading schema before
publishing to the Redis stream. Invalid signals are rejected and
logged locally (no Redis publish).
"""
from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis

from ...contracts.registry import validate_signal

log = logging.getLogger(__name__)


class RedisSignalPublisher:
    """Publishes validated signal payloads to the signals:trading stream.

    Args:
        redis_url: Redis connection URL.
        stream_key: Stream to publish to (default ``signals:trading``).
    """

    def __init__(
        self,
        redis_url: str,
        stream_key: str = "signals:trading",
    ) -> None:
        self._url = redis_url
        self._stream = stream_key
        self._client: aioredis.Redis | None = None

    async def _ensure_client(self) -> aioredis.Redis:
        if self._client is None:
            self._client = aioredis.from_url(self._url)
        return self._client

    async def publish(self, payload: dict[str, Any]) -> bool:
        """Publish a validated signal payload.

        Returns ``True`` if the payload was published, ``False`` if it
        was rejected by validation.
        """
        errors = validate_signal(payload)
        if errors:
            log.warning(
                "signal_validation_failed",
                errors=[str(e) for e in errors],
                stream=self._stream,
            )
            return False

        client = await self._ensure_client()
        try:
            await client.xadd(self._stream, {"p": json.dumps(payload)})
            log.info("signal_published", stream=self._stream)
            return True
        except Exception as exc:
            log.error("signal_publish_failed", error=str(exc), stream=self._stream)
            return False

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                log.warning("redis_close_failed", exc_info=True)
