"""LaneScheduler — enruta eventos a su lane correspondiente.

Entity Lane:  eventos que afectan a una sola entidad (símbolo/posición/orden).
Global Lane:  eventos que afectan al estado global del sistema (riesgo/config/simulación).

EMC v1 §7.1 — Dual-Lane Replay Architecture.
"""
from __future__ import annotations

from enum import Enum, auto

from ...domain.value_objects.domain_event import DomainEvent


class Lane(Enum):
    ENTITY = auto()
    GLOBAL = auto()


# Event types that belong to the Entity Lane (per-symbol state machines)
ENTITY_LANE_TYPES: frozenset[str] = frozenset({
    "SignalGenerated",
    "SignalRejected",
    "OrderCreated",
    "OrderFilled",
    "OrderCancelled",
    "PositionOpened",
    "PositionUpdated",
    "PositionClosed",
})

# Event types that belong to the Global Lane (risk/portfolio/system)
GLOBAL_LANE_TYPES: frozenset[str] = frozenset({
    "RiskEvaluated",
    "RiskLimitBreached",
    "RiskStateChanged",
    "SimulationEvaluated",
    "MonteCarloCompleted",
    "ConfigChanged",
    "ProjectionCorruptionDetected",
    "ProjectionMigrated",
})


class LaneScheduler:
    """Enruta eventos a Entity Lane o Global Lane.

    Es una función pura: dado un DomainEvent, devuelve el Lane.
    Sin I/O, sin estado, sin excepciones.
    """

    @staticmethod
    def route(event: DomainEvent) -> Lane:
        """Determinar el lane para un evento.

        Args:
            event: DomainEvent a enrutar.

        Returns:
            Lane.ENTITY o Lane.GLOBAL.

        Por defecto (evento desconocido): GLOBAL.
        Esto asegura que eventos futuros no planeados no se pierdan
        silenciosamente en el Entity Lane.
        """
        if event.event_type in ENTITY_LANE_TYPES:
            return Lane.ENTITY
        return Lane.GLOBAL

    @staticmethod
    def is_entity_event(event_type: str) -> bool:
        return event_type in ENTITY_LANE_TYPES

    @staticmethod
    def is_global_event(event_type: str) -> bool:
        return event_type in GLOBAL_LANE_TYPES or event_type not in ENTITY_LANE_TYPES
