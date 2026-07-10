from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar


@dataclass(frozen=True)
class DomainEvent:
    event_index: int = 0
    event_id: str = ""
    event_type: str = ""
    source: str = ""
    version: int = 0
    timestamp: str = ""
    timestamp_bucket: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    _PREFIX: ClassVar[str] = "evt"

    @classmethod
    def create(
        cls,
        event_type: str,
        source: str,
        version: int = 1,
        timestamp_bucket: str | None = None,
        data: dict[str, Any] | None = None,
        *,
        prefix: str | None = None,
    ) -> DomainEvent:
        if not event_type or not source:
            raise ValueError(
                f"event_type and source required: event_type={event_type!r}, source={source!r}"
            )
        if data is None:
            data = {}

        ts = datetime.now(timezone.utc)
        timestamp = ts.isoformat()
        if timestamp_bucket is None:
            bucket = str(int(ts.timestamp()))
        else:
            bucket = timestamp_bucket

        prefix_str = prefix if prefix is not None else cls._PREFIX

        raw_inputs = {
            "event_type": event_type,
            "source": source,
            "version": str(version),
            "timestamp_bucket": bucket,
            "data": _canonical_json(data),
        }
        digest = hashlib.sha256(
            _canonical_json(raw_inputs).encode("utf-8")
        ).hexdigest()[:32]
        event_id = f"{prefix_str}_{digest}"

        return cls(
            event_index=0,
            event_id=event_id,
            event_type=event_type,
            source=source,
            version=version,
            timestamp=timestamp,
            timestamp_bucket=bucket,
            data=data,
        )

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> DomainEvent:
        raw = row["data"]
        if isinstance(raw, str):
            raw = json.loads(raw)
        return cls(
            event_index=row.get("event_index", 0),
            event_id=row["event_id"],
            event_type=row["event_type"],
            source=row["source"],
            version=row["version"],
            timestamp=row["timestamp"],
            timestamp_bucket=row["timestamp_bucket"],
            data=raw,
        )

    def to_row(self) -> dict[str, Any]:
        return {
            "event_index": self.event_index,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "source": self.source,
            "version": self.version,
            "timestamp": self.timestamp,
            "timestamp_bucket": self.timestamp_bucket,
            "data": _canonical_json(self.data),
        }


def _canonical_json(obj: dict[str, Any]) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
