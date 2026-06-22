"""EventStream — modelo de eventos ordenados para replay determinista.

El EventStream reconstruye una secuencia ordenada temporalmente de
todos los eventos del sistema a partir del ledger persistido.

Reglas:
- Orden estrictamente temporal (timestamp + sequence_id)
- Idempotente
- Sin pérdida de eventos
- Reconstrucción completa desde DTOs
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class EventType(Enum):
    TRADE_OPENED = auto()
    TRADE_CLOSED = auto()
    EPISODE_CREATED = auto()
    EPISODE_SETTLED = auto()
    TENSOR_STORED = auto()
    CONTROL_DECISION = auto()
    SIGNAL_GENERATED = auto()
    EQUITY_CHANGED = auto()
    EXPERIMENT_STARTED = auto()
    EXPERIMENT_ENDED = auto()


@dataclass(frozen=True, order=True)
class Event:
    """Un evento único en el stream del experimento.

    El ordenamiento natural es por (timestamp, sequence_id),
    garantizando replay determinista.
    """
    timestamp: str
    sequence_id: int
    event_type: EventType
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", dict(self.data or {}))


class EventStream:
    """Stream ordenado de eventos del experimento.

    Construcción:
        stream = EventStream()
        stream.add_event(Event(...))
        stream.add_events([Event(...), ...])

    Replay:
        for event in stream.sorted():
            process(event)

    Los eventos se ordenan por (timestamp, sequence_id) y son
    estrictamente deterministas.
    """

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._seq_counter: int = 0

    def add_event(
        self,
        event_type: EventType,
        data: dict[str, Any] | None = None,
        timestamp: str | None = None,
    ) -> Event:
        """Add a single event with auto-incrementing sequence_id."""
        self._seq_counter += 1
        event = Event(
            timestamp=timestamp or "",
            sequence_id=self._seq_counter,
            event_type=event_type,
            data=data or {},
        )
        self._events.append(event)
        return event

    def add_events(
        self,
        events: list[tuple[EventType, dict[str, Any], str | None]],
    ) -> list[Event]:
        """Add multiple events at once."""
        return [self.add_event(et, d, ts) for et, d, ts in events]

    def sorted(self) -> list[Event]:
        """Return events sorted by (timestamp, sequence_id).

        Determinista: mismo input → mismo orden siempre.
        """
        return sorted(self._events)

    def by_type(self, event_type: EventType) -> list[Event]:
        """Filter events by type, preserving order."""
        return [e for e in self._events if e.event_type == event_type]

    def clear(self) -> None:
        self._events.clear()
        self._seq_counter = 0

    @property
    def count(self) -> int:
        return len(self._events)

    @property
    def is_empty(self) -> bool:
        return len(self._events) == 0

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self):
        return iter(self.sorted())
