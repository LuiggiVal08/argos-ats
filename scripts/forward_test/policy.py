"""STEP 1 — Freeze Execution Policy v1.0.

Versioned policy object. Immutable during execution.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("forward_test.policy")

DEFAULT_POLICY_FILENAME = "policy_v1.json"


@dataclass(frozen=True)
class PolicyV1:
    version: str = "1.0.0"
    entry_threshold: float = 0.60
    exit_threshold: float = 0.40
    max_hold_bars: int = 5
    symbol: str = "BTC"
    timeframe: str = "1h"
    lookahead: int = 5
    stride: int = 5
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def get_signal(self, y_proba: float) -> str:
        if y_proba > self.entry_threshold:
            return "BUY"
        if y_proba < self.exit_threshold:
            return "SELL"
        return "HOLD"

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, directory: Path | str) -> Path:
        path = Path(directory) / DEFAULT_POLICY_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info(f"Policy v{self.version} saved to {path}")
        return path

    @classmethod
    def load(cls, path: Path | str) -> PolicyV1:
        with open(path) as f:
            data = json.load(f)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @classmethod
    def default(cls, symbol: str = "BTC") -> PolicyV1:
        return cls(symbol=symbol)
