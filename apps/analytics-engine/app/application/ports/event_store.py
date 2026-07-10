"""EventStore port — deterministic append-only event log.

This is the single writer for all domain events across the system.
It is deliberately DUMB: no business logic, no interpretation,
no branching on event_type. It only guarantees:

1. Single append contract: write(event) → append ONLY if event_id not exists
2. Idempotency across ALL sources: same event_id = 1 write global
3. Deterministic event_id (see DomainEvent.create)
4. No branching logic: EventWriter does not know BUY/SELL, risk rules, simulation

Architecture rule:
  - Port lives in application/ports/ (interface only)
  - Concrete adapter lives in infrastructure/
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Protocol, runtime_checkable

from ...domain.value_objects.domain_event import DomainEvent


class EventStoreError(RuntimeError):
    """Raised when event store I/O fails."""


@runtime_checkable
class EventStore(Protocol):
    """Deterministic append-only event log.

    Key invariant:
      event_id = UNIQUE GLOBAL — no two writes with the same event_id.
    """

    async def write(self, event: DomainEvent) -> bool:
        """Atomically append an event if it does not exist.

        Args:
            event: DomainEvent with deterministic event_id.

        Returns:
            True if the event was appended (first time).
            False if the event_id already exists (duplicate, skipped).

        Raises:
            EventStoreError on I/O failure.
        """
        ...

    async def exists(self, event_id: str) -> bool:
        """Check if an event_id exists without writing.

        Returns:
            True if the event_id has been recorded before.
        """
        ...

    async def read(self, event_id: str) -> DomainEvent | None:
        """Read a single event by event_id.

        Returns:
            DomainEvent if found, None otherwise.
        """
        ...

    async def list_by_type(
        self,
        event_type: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DomainEvent]:
        """List events of a given type, ordered by timestamp ascending."""
        ...

    async def list_since(
        self,
        since_timestamp: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DomainEvent]:
        """List events since a given ISO timestamp, ordered ascending."""
        ...

    async def count(self) -> int:
        """Total number of events in the store."""
        ...

    async def count_by_type(self, event_type: str) -> int:
        """Number of events of a given type."""
        ...

    async def stream_by_index_range(
        self,
        from_index: int,
        to_index: int | None = None,
    ) -> AsyncIterator[DomainEvent]:
        """Stream events by event_index range, ordered ASC.

        Args:
            from_index: Start index (inclusive).
            to_index: End index (inclusive). None = until the end.

        Yields:
            DomainEvent in event_index order.

        Raises:
            EventStoreError on I/O failure.
        """
        ...  # pragma: no cover
        yield  # type: ignore  # make it an async generator

    async def max_event_index(self) -> int:
        """Highest event_index in the store.

        Returns:
            Maximum event_index, or 0 if the store is empty.
        """
        ...

    async def close(self) -> None:
        """Release resources (connection, etc.)."""
        ...

    async def clear(self) -> None:
        """Delete all events (testing / manual reset).

        WARNING: destructive. Only for testing and emergency recovery.
        """
        ...
