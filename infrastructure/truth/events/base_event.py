from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class TruthEventType(str, Enum):
    TRADE = "TRADE"
    EPISODE = "EPISODE"
    TENSOR = "TENSOR"
    INFERENCE = "INFERENCE"
    CONTROL = "CONTROL"


@dataclass(frozen=True)
class TruthEvent:
    """Base event in the Truth Layer event sourcing system.

    Every event is immutable, append-only, and hash-chained.
    """

    event_type: TruthEventType
    timestamp: str
    run_id: str
    symbol: str
    payload: dict[str, Any]
    state_hash: str = ""
    parent_hash: str = ""
    event_id: int = 0

    @property
    def canonical_json(self) -> str:
        """Deterministic JSON serialization for hashing."""
        raw = {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "run_id": self.run_id,
            "symbol": self.symbol,
            "payload": self.payload,
        }
        return json.dumps(raw, sort_keys=True, separators=(",", ":"))

    def compute_hash(self, parent_hash: str = "") -> str:
        """Compute state_hash = SHA256(payload + parent_hash + event_type + timestamp)."""
        raw = f"{self.canonical_json}{parent_hash}{self.event_type.value}{self.timestamp}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def with_chain(self, parent_hash: str = "") -> TruthEvent:
        """Return a new event with hash chain populated."""
        h = self.compute_hash(parent_hash)
        return TruthEvent(
            event_type=self.event_type,
            timestamp=self.timestamp,
            run_id=self.run_id,
            symbol=self.symbol,
            payload=self.payload,
            state_hash=h,
            parent_hash=parent_hash,
        )

    def to_db_row(self) -> dict[str, str]:
        return {
            "timestamp": self.timestamp,
            "run_id": self.run_id,
            "symbol": self.symbol,
            "event_type": self.event_type.value,
            "payload": json.dumps(self.payload, sort_keys=True),
            "state_hash": self.state_hash,
            "parent_hash": self.parent_hash,
        }

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> TruthEvent:
        return cls(
            event_id=row["id"],
            event_type=TruthEventType(row["event_type"]),
            timestamp=row["timestamp"],
            run_id=row["run_id"],
            symbol=row["symbol"],
            payload=json.loads(row["payload"]),
            state_hash=row["state_hash"],
            parent_hash=row["parent_hash"],
        )

    @classmethod
    def now_utc(cls) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
