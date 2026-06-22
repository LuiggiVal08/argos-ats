"""Candle stream consumer with schema validation guard.

Consumes contract-compliant candle payloads from Redis streams
(market:candles:*), validates against the candle schema, and
forwards valid candles to the caller's handler. Invalid candles
are silently dropped and logged locally (no Redis publish).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

import redis.asyncio as aioredis

from ...contracts.registry import validate_candle

log = logging.getLogger(__name__)

CandleHandler = Callable[[dict[str, Any]], Awaitable[None]]


class CandleStreamConsumer:
    """Consumes candle payloads from a Redis stream with validation.

    Args:
        redis_url: Redis connection URL.
        stream_key: Stream to consume from (default ``market:candles:1h``).
        handler: Async callback for validated candle payloads.
    """

    def __init__(
        self,
        redis_url: str,
        stream_key: str = "market:candles:1h",
        handler: CandleHandler | None = None,
    ) -> None:
        self._url = redis_url
        self._stream = stream_key
        self._handler = handler
        self._client: aioredis.Redis | None = None
        self._stopped = False

    async def start(self, handler: CandleHandler | None = None) -> None:
        """Start consuming candles in a pull loop.

        If *handler* is provided here, it overrides the constructor handler.
        """
        if handler is not None:
            self._handler = handler
        if self._handler is None:
            raise RuntimeError("CandleStreamConsumer: no handler provided")

        self._client = aioredis.from_url(self._url)
        last_id = "$"
        log.info("candle_stream_consumer_started", stream=self._stream)

        while not self._stopped:
            try:
                result = await self._client.xread(
                    {self._stream: last_id}, block=1000, count=10
                )
            except Exception as exc:
                log.warning("candle_stream_xread_error", error=str(exc))
                continue

            if not result:
                continue

            for stream_name, entries in result:
                for entry_id, fields in entries:
                    last_id = (
                        entry_id.decode() if isinstance(entry_id, bytes) else entry_id
                    )
                    raw = fields.get("p") if isinstance(fields, dict) else None
                    if raw is None and isinstance(fields, (list, tuple)):
                        for i, v in enumerate(fields):
                            if v == "p" and i + 1 < len(fields):
                                raw = fields[i + 1]
                                break
                    if raw is None:
                        continue
                    if isinstance(raw, bytes):
                        raw = raw.decode()

                    try:
                        payload: dict[str, Any] = json.loads(raw)
                    except json.JSONDecodeError:
                        log.warning("candle_stream_parse_error", stream=self._stream)
                        continue

                    errors = validate_candle(payload)
                    if errors:
                        log.warning(
                            "candle_validation_failed",
                            errors=[str(e) for e in errors],
                            stream=self._stream,
                        )
                        continue

                    try:
                        await self._handler(payload)
                    except Exception as exc:
                        log.error("candle_handler_error", error=str(exc))

        log.info("candle_stream_consumer_stopped")

    async def stop(self) -> None:
        self._stopped = True
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                log.warning("redis_close_failed", exc_info=True)
