"""STEP 4 — Regime-aware logging (observational only).

Classifies each bar as trending/ranging/volatile using ADX + ATR.
NO decision use — logging only.
"""

from __future__ import annotations

from enum import Enum


class Regime(Enum):
    TRENDING = "trending"
    RANGING = "ranging"
    VOLATILE = "volatile"
    UNDEFINED = "undefined"


# Thresholds (standard TA conventions)
ADX_TRENDING = 25
ADX_RANGING = 20
ATR_VOLATILE_PCT = 0.02


def classify(features: dict | None) -> str:
    """Classify a single bar's regime using pre-computed features.

    Uses ADX for trending vs ranging, ATR% for volatility overlay.
    Volatile overrides trending/ranging when ATR% is extreme.

    Args:
        features: dict with at least 'adx', 'atr', 'close' keys
            (from the 53-feature pipeline).

    Returns:
        One of 'trending', 'ranging', 'volatile', 'undefined'.
    """
    if features is None:
        return Regime.UNDEFINED.value

    adx = features.get("adx")
    atr = features.get("atr")
    close = features.get("close")

    if adx is None or atr is None or close is None or close == 0:
        return Regime.UNDEFINED.value

    atr_pct = atr / close

    if atr_pct > ATR_VOLATILE_PCT:
        return Regime.VOLATILE.value

    if adx >= ADX_TRENDING:
        return Regime.TRENDING.value

    if adx <= ADX_RANGING:
        return Regime.RANGING.value

    return Regime.UNDEFINED.value
