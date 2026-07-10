"""Composer Activation — multi-model routing and divergence detection.

FASE 4.1 — ModelRegistryActiveScan:
  Scans the model checkpoints directory and detects available models
  for each symbol (BTC, ETH, SOL).

FASE 4.2 — ModelComparisonLayer:
  Runs inference through all available models and collects outputs.
  Each output is a ModelOutput (side, confidence, regime, model_id).

FASE 4.3 — ComposerDecisionPolicy (OBSERVATIONAL MODE):
  Compares outputs using ModelComposer, logs divergence/agreement.
  Does NOT execute multi-model trades — only observes and logs.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from ...domain.edl.composer import ComposerResult, ModelComposer, ModelOutput

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MODEL_BASE_DIRS = [
    "models",
    "checkpoints",
]
"""Directories to scan for model checkpoints."""

_KNOWN_SYMBOLS = ["BTC/USDT"]
"""Symbols to check for model availability.

Only BTC/USDT is currently compatible with TARGET_SPEC_V1 (ternary, 30 features).
ETH/USDT and SOL/USDT use binary V1 models (53 features) and are excluded until
they are migrated. See task H11.
"""


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ModelInfo:
    """Metadata about a discovered model."""
    symbol: str = ""
    model_id: str = ""
    version: str = ""
    path: str = ""
    is_loaded: bool = False
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class ComparisonReport:
    """Report of a multi-model comparison cycle."""
    timestamp: str = ""
    primary_symbol: str = ""
    primary_output: ModelOutput | None = None
    alternative_outputs: list[ModelOutput] = field(default_factory=list)
    composer_result: ComposerResult | None = None
    agreement_score: float = 0.0
    num_models: int = 0
    divergence_warning: bool = False


# ---------------------------------------------------------------------------
# Model Registry Active Scan (FASE 4.1)
# ---------------------------------------------------------------------------


class ModelRegistryScanner:
    """Scans checkpoint directories for available models per symbol.

    Detects models by looking for `.keras`, `.json` metadata files
    in symbol-named subdirectories.
    """

    def __init__(self, base_dirs: list[str] | None = None) -> None:
        self._base_dirs = base_dirs or _MODEL_BASE_DIRS
        self._models: dict[str, ModelInfo] = {}

    def scan(self) -> dict[str, ModelInfo]:
        """Scan all base directories for model checkpoints.

        Returns:
            dict[symbol_key, ModelInfo] — e.g. {"BTC/USDT": ModelInfo(...)}
        """
        self._models = {}
        for base in self._base_dirs:
            base_path = Path(base)
            if not base_path.exists():
                continue

            for symbol in _KNOWN_SYMBOLS:
                symbol_key = symbol.replace("/", "").lower()
                symbol_dir = base_path / symbol_key
                if not symbol_dir.exists():
                    continue

                model_path = self._find_model(symbol_dir)
                if model_path is None:
                    continue

                metadata = self._read_metadata(symbol_dir)
                self._models[symbol] = ModelInfo(
                    symbol=symbol,
                    model_id=metadata.get("model_id", f"{symbol_key}_model"),
                    version=metadata.get("version", "unknown"),
                    path=str(model_path),
                    is_loaded=False,
                    metrics=metadata.get("metrics", {}),
                )

        if self._models:
            log.info(
                "ccl:model_scan_complete",
                models_found=list(self._models.keys()),
                count=len(self._models),
            )
        else:
            log.info("ccl:model_scan_no_models", base_dirs=self._base_dirs)

        return self._models

    def has_model(self, symbol: str) -> bool:
        """Check if a model exists for a symbol."""
        return symbol in self._models

    @property
    def available_models(self) -> list[str]:
        return list(self._models.keys())

    def get_model_info(self, symbol: str) -> ModelInfo | None:
        return self._models.get(symbol)

    @staticmethod
    def _find_model(directory: Path) -> Path | None:
        """Find the primary model file in a directory."""
        for ext in [".keras", ".h5", ".pkl", ".pt", ".joblib"]:
            candidates = list(directory.glob(f"*{ext}"))
            if candidates:
                return candidates[0]
        return None

    @staticmethod
    def _read_metadata(directory: Path) -> dict[str, Any]:
        """Read model metadata JSON if present."""
        meta_file = directory / "metadata.json"
        if meta_file.exists():
            try:
                return json.loads(meta_file.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {}


# ---------------------------------------------------------------------------
# Model Comparison Layer (FASE 4.2)
# ---------------------------------------------------------------------------


class ModelComparisonLayer:
    """Runs inference through all available models and collects outputs.

    This is OBSERVATIONAL ONLY — does not execute trades from
    alternative models.
    """

    def __init__(
        self,
        scanner: ModelRegistryScanner,
        composer: ModelComposer | None = None,
    ) -> None:
        self._scanner = scanner
        self._composer = composer or ModelComposer()
        self._reports: list[ComparisonReport] = []

    def compare(
        self,
        primary_side: str,
        primary_confidence: float,
        primary_model: str,
        primary_symbol: str,
        regime: str,
    ) -> ComparisonReport:
        """Run multi-model comparison for a single decision point.

        In observational mode, only logs results — no execution.
        """
        primary = ModelOutput(
            model_id=primary_model,
            symbol=primary_symbol,
            side=primary_side,
            confidence=primary_confidence,
            regime=regime,
            version=primary_model,
        )

        alternatives: list[ModelOutput] = []
        models = self._scanner.scan()

        for sym, info in models.items():
            if sym == primary_symbol:
                continue
            alternatives.append(ModelOutput(
                model_id=info.model_id,
                symbol=sym,
                side="HOLD",
                confidence=0.5,
                regime=regime,
                version=info.version,
            ))

        result = self._composer.compare(primary, alternatives)

        # Compute agreement score
        if result.divergence_detected:
            agreement = 0.0
        elif not result.divergence_details:
            agreement = 1.0
        else:
            aligned = sum(1 for d in result.divergence_details if d.get("aligned", False))
            total = len(result.divergence_details)
            agreement = aligned / total if total > 0 else 1.0

        report = ComparisonReport(
            primary_symbol=primary_symbol,
            primary_output=primary,
            alternative_outputs=alternatives,
            composer_result=result,
            agreement_score=round(agreement, 4),
            num_models=1 + len(alternatives),
            divergence_warning=result.divergence_detected,
        )

        if result.divergence_detected:
            log.warning(
                "ccl:composer_divergence",
                primary_side=primary_side,
                primary_confidence=primary_confidence,
                alternatives=[
                    {"model": a.model_id, "symbol": a.symbol}
                    for a in alternatives
                ],
                details=result.divergence_details,
            )
        elif alternatives:
            log.info(
                "ccl:composer_aligned",
                primary_model=primary_model,
                primary_side=primary_side,
                alternative_count=len(alternatives),
                agreement_score=report.agreement_score,
            )
        else:
            log.debug("ccl:composer_single_model", primary_model=primary_model)

        self._reports.append(report)
        if len(self._reports) > 100:
            self._reports.pop(0)

        return report

    @property
    def recent_reports(self) -> list[ComparisonReport]:
        return self._reports

    def status(self) -> dict[str, Any]:
        """Return current composer status for observability."""
        models = self._scanner.available_models
        return {
            "status": "observational",
            "available_models": models,
            "model_count": len(models),
            "comparisons_run": len(self._reports),
            "divergences_detected": sum(
                1 for r in self._reports if r.divergence_warning
            ),
            "last_divergence": (
                self._reports[-1].composer_result.to_dict()
                if self._reports and self._reports[-1].divergence_warning
                else None
            ),
        }
