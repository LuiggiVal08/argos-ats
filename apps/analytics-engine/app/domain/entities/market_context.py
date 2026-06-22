from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..value_objects.market_regime import RegimeType


@dataclass(frozen=True)
class MarketContext:
    regime: RegimeType
    adx: float
    bbw: float
    atr: float
    ema_slope: float
    additional: dict[str, float] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_actionable(self) -> bool:
        return self.regime != RegimeType.UNKNOWN

    @property
    def features(self) -> dict[str, float]:
        result: dict[str, float] = {
            "adx": self.adx,
            "bbw": self.bbw,
            "atr": self.atr,
            "ema_slope": self.ema_slope,
        }
        result.update(self.additional)
        return result
