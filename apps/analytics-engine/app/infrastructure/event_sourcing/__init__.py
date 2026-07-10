"""EventRecorder — writes DomainEvent to EventStore for every pipeline event.

Every event carries:
  - event_id (deterministic SHA-256 hash)
  - event_type (e.g. "signal.generated", "trade.executed")
  - timestamp (ISO 8601 UTC)
  - source (e.g. "decision_loop", "execution_guard")
  - correlation_id (trace across pipeline stages)
  - payload (event-specific data)

Single append contract: write(event) -> True if first write, False if duplicate.
"""

from __future__ import annotations

from typing import Any

import structlog

from ...domain.value_objects.domain_event import DomainEvent

log = structlog.get_logger()


class EventRecorder:
    """Static facade for writing structured DomainEvent records.

    Usage:
        await EventRecorder.record(event_store, "signal.generated", "decision_loop", {...})
    """

    @staticmethod
    async def record(
        event_store: Any,
        event_type: str,
        source: str,
        data: dict[str, Any] | None = None,
        version: int = 1,
    ) -> DomainEvent | None:
        """Create and write a DomainEvent to the event store.

        Args:
            event_store: EventStore-compatible writer.
            event_type: Dot-notation type (e.g. "signal.generated").
            source: Component name (e.g. "decision_loop", "execution_guard").
            data: Arbitrary JSON-serializable payload.
            version: Schema version for forward compatibility.

        Returns:
            DomainEvent if written successfully, None on failure.
        """
        if event_store is None:
            return None

        try:
            event = DomainEvent.create(
                event_type=event_type,
                source=source,
                version=version,
                data=data or {},
            )

            written = await event_store.write(event)
            if written:
                log.debug(
                    "event_recorded",
                    event_type=event_type,
                    event_id=event.event_id,
                    source=source,
                )
            return event if written else None

        except Exception as exc:
            log.warning(
                "event_recording_failed",
                event_type=event_type,
                source=source,
                error=str(exc),
            )
            return None
