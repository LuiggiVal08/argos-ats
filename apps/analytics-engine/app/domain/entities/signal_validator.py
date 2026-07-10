"""Domain entity: SignalValidator.

Pure-domain. Validates incoming signals before execution:
  - confidence >= threshold (regime-aware when regime_thresholds is set)
  - cooldown per symbol (no duplicate signals too close together)
  - dedup by signal_id (reject already-seen IDs)

The application layer feeds it an ExecutionSignal and gets back
a ValidationResult (VALID or REJECTED with reason).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

from ..value_objects.execution_signal import ExecutionSignal


class RejectionReason(str, Enum):
    CONFIDENCE_TOO_LOW = "CONFIDENCE_TOO_LOW"
    COOLDOWN_ACTIVE = "COOLDOWN_ACTIVE"
    DUPLICATE_SIGNAL_ID = "DUPLICATE_SIGNAL_ID"
    SIGNAL_EXPIRED = "SIGNAL_EXPIRED"


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    reason: RejectionReason | None = None
    message: str = ""

    @classmethod
    def ok(cls) -> ValidationResult:
        return cls(valid=True)

    @classmethod
    def rejected(cls, reason: RejectionReason, message: str = "") -> ValidationResult:
        return cls(valid=False, reason=reason, message=message)


class SignalValidator:
    """Validates ExecutionSignals with configurable thresholds.

    Args:
        min_confidence: Minimum confidence to accept a signal (0..1).
        regime_thresholds: Optional per-regime overrides, e.g.
            {"TRENDING": 0.55, "RANGING": 0.62}.
            When set, the regime is read from signal.metadata["regime"].
            Falls back to min_confidence when regime is unknown.
        cooldown_seconds: Min seconds between signals for the same symbol.
        max_age_seconds: Max age of a signal before it's expired.
    """

    def __init__(
        self,
        min_confidence: float = 0.7,
        regime_thresholds: dict[str, float] | None = None,
        cooldown_seconds: int = 60,
        max_age_seconds: int = 300,
    ) -> None:
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError(
                f"min_confidence must be in [0, 1], got {min_confidence}"
            )
        if regime_thresholds:
            for regime, thresh in regime_thresholds.items():
                if not 0.0 <= thresh <= 1.0:
                    raise ValueError(
                        f"regime_thresholds[{regime!r}] must be in [0, 1], got {thresh}"
                    )
        if cooldown_seconds < 0:
            raise ValueError(
                f"cooldown_seconds must be >= 0, got {cooldown_seconds}"
            )
        if max_age_seconds < 0:
            raise ValueError(
                f"max_age_seconds must be >= 0, got {max_age_seconds}"
            )

        self._min_confidence = min_confidence
        self._regime_thresholds = regime_thresholds or {}
        self._cooldown = timedelta(seconds=cooldown_seconds)
        self._max_age = timedelta(seconds=max_age_seconds)

        self._seen_ids: set[str] = set()
        self._last_signal_time: dict[str, datetime] = {}

    def _get_threshold(self, signal: ExecutionSignal) -> float:
        """Return the effective confidence threshold for the signal."""
        if not self._regime_thresholds:
            return self._min_confidence
        regime = (signal.metadata or {}).get("regime", "") or \
                 (signal.metadata or {}).get("regime_at_dispatch", "")
        return self._regime_thresholds.get(regime, self._min_confidence)

    def validate(self, signal: ExecutionSignal) -> ValidationResult:
        if signal.signal_id in self._seen_ids:
            return ValidationResult.rejected(
                RejectionReason.DUPLICATE_SIGNAL_ID,
                f"signal_id {signal.signal_id!r} already processed",
            )

        age = datetime.now(timezone.utc) - signal.timestamp
        if age > self._max_age:
            return ValidationResult.rejected(
                RejectionReason.SIGNAL_EXPIRED,
                f"signal age {age.total_seconds():.0f}s exceeds max {self._max_age.total_seconds():.0f}s",
            )

        threshold = self._get_threshold(signal)
        if signal.confidence < threshold:
            return ValidationResult.rejected(
                RejectionReason.CONFIDENCE_TOO_LOW,
                f"confidence {signal.confidence} < {threshold}",
            )

        last = self._last_signal_time.get(signal.symbol)
        if last is not None:
            elapsed = datetime.now(timezone.utc) - last
            if elapsed < self._cooldown:
                return ValidationResult.rejected(
                    RejectionReason.COOLDOWN_ACTIVE,
                    f"cooldown active for {signal.symbol}: "
                    f"{elapsed.total_seconds():.0f}s < {self._cooldown.total_seconds():.0f}s",
                )

        # mark as seen and update cooldown
        self._seen_ids.add(signal.signal_id)
        self._last_signal_time[signal.symbol] = datetime.now(timezone.utc)
        return ValidationResult.ok()
