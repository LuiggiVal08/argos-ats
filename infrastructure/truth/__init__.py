"""Truth Layer — Single Source of Truth via SQLite event sourcing.

Spec §15-19 (implicit). Canonical record of ALL events in the trading system.
Append-only, hash-chained, deterministic replay.

Event types:
    TRADE      — trade opened/closed/updated
    EPISODE    — episode created/settled
    TENSOR     — Φ edge tensor stored
    INFERENCE  — inference engine output (P(edge), P(failure), EIA)
    CONTROL    — control layer decision (HALT/REDUCE/CONTINUE)
"""

from .store.event_store import TruthStore

__all__ = ["TruthStore"]
