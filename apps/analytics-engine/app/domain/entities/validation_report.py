"""Domain entity: ValidationReport — post-training evaluation."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class CheckStatus(str, Enum):
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"
    ERROR = "ERROR"


class CheckType(str, Enum):
    CLASS_DISTRIBUTION = "class_distribution"
    CONFUSION_MATRIX = "confusion_matrix"
    PROBABILITY_HISTOGRAM = "probability_histogram"
    CALIBRATION_CURVE = "calibration_curve"
    FEATURE_IMPORTANCE = "feature_importance"
    MC_DROPOUT = "mc_dropout"
    REGIME_BREAKDOWN = "regime_breakdown"


@dataclass(frozen=True)
class ValidationCheck:
    check_type: CheckType
    status: CheckStatus
    metrics: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    details_path: str | None = None


@dataclass(frozen=True)
class ValidationReport:
    symbol: str
    model_version: str
    trained_at: datetime
    validated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    checks: tuple[ValidationCheck, ...] = ()
    n_test_samples: int = 0
    n_features: int = 0
    lookback: int = 0
    n_windows: int = 0

    @property
    def status(self) -> CheckStatus:
        if any(c.status == CheckStatus.FAIL for c in self.checks):
            return CheckStatus.FAIL
        if any(c.status == CheckStatus.WARNING for c in self.checks):
            return CheckStatus.WARNING
        if any(c.status == CheckStatus.ERROR for c in self.checks):
            return CheckStatus.ERROR
        return CheckStatus.PASS

    @property
    def critical_failures(self) -> list[str]:
        return [
            f"{c.check_type.value}: {c.message}"
            for c in self.checks if c.status == CheckStatus.FAIL
        ]

    @property
    def warnings(self) -> list[str]:
        return [
            f"{c.check_type.value}: {c.message}"
            for c in self.checks if c.status == CheckStatus.WARNING
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "model_version": self.model_version,
            "trained_at": self.trained_at.isoformat(),
            "validated_at": self.validated_at.isoformat(),
            "status": self.status.value,
            "n_test_samples": self.n_test_samples,
            "n_features": self.n_features,
            "lookback": self.lookback,
            "n_windows": self.n_windows,
            "critical_failures": self.critical_failures,
            "warnings": self.warnings,
            "checks": [
                {
                    "check_type": c.check_type.value,
                    "status": c.status.value,
                    "metrics": c.metrics,
                    "message": c.message,
                    "details_path": c.details_path,
                }
                for c in self.checks
            ],
        }
