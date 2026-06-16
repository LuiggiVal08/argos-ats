"""Value objects for validation metrics."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ClassDistribution:
    counts: dict[str, int]
    percentages: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "counts": self.counts,
            "percentages": {k: round(v, 4) for k, v in self.percentages.items()},
        }


@dataclass(frozen=True)
class ConfusionMetrics:
    matrix: list[list[int]]
    labels: list[str]
    precision: dict[str, float]
    recall: dict[str, float]
    f1: dict[str, float]
    accuracy: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "matrix": self.matrix,
            "labels": self.labels,
            "precision": {k: round(v, 4) for k, v in self.precision.items()},
            "recall": {k: round(v, 4) for k, v in self.recall.items()},
            "f1": {k: round(v, 4) for k, v in self.f1.items()},
            "accuracy": round(self.accuracy, 4),
        }


@dataclass(frozen=True)
class BrierScore:
    overall: float
    per_class: tuple[float, float, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall": round(self.overall, 6),
            "per_class": [round(v, 6) for v in self.per_class],
        }


@dataclass(frozen=True)
class ECE:
    overall: float
    n_bins: int = 10

    def to_dict(self) -> dict[str, Any]:
        return {"overall": round(self.overall, 6), "n_bins": self.n_bins}


@dataclass(frozen=True)
class CalibrationMetrics:
    brier: BrierScore
    ece: ECE
    n_bins: int = 10
    calibration_data: list[tuple[float, float]] = ()  # (predicted_confidence, fraction_positives)

    def to_dict(self) -> dict[str, Any]:
        return {
            "brier": self.brier.to_dict(),
            "ece": self.ece.to_dict(),
            "n_bins": self.n_bins,
        }


@dataclass(frozen=True)
class RegimeMetrics:
    regime: str
    n_signals: int
    win_rate: float
    profit_factor: float
    avg_confidence: float
    avg_uncertainty: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime,
            "n_signals": self.n_signals,
            "win_rate": round(self.win_rate, 4),
            "profit_factor": round(self.profit_factor, 4),
            "avg_confidence": round(self.avg_confidence, 4),
            "avg_uncertainty": round(self.avg_uncertainty, 6),
        }


@dataclass(frozen=True)
class RegimeBreakdown:
    regimes: list[RegimeMetrics]
    n_total: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "regimes": [r.to_dict() for r in self.regimes],
            "n_total": self.n_total,
        }


@dataclass(frozen=True)
class UncertaintyMetrics:
    mean_std: float
    median_std: float
    p95_std: float
    min_std: float
    max_std: float
    n_samples: int = 30
    histogram: list[float] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "mean_std": round(self.mean_std, 6),
            "median_std": round(self.median_std, 6),
            "p95_std": round(self.p95_std, 6),
            "min_std": round(self.min_std, 6),
            "max_std": round(self.max_std, 6),
            "n_samples": self.n_samples,
        }


@dataclass(frozen=True)
class FeatureImportance:
    ranking: list[tuple[str, float]]
    n_features: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "ranking": [(name, round(imp, 6)) for name, imp in self.ranking],
            "n_features": self.n_features,
        }
