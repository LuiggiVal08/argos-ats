"""ModelComposer — multi-model divergence comparison.

The Composer receives features from the pipeline and queries all
registered active models, collecting their outputs for comparison.

This is an OBSERVER layer: it does NOT replace or modify the primary
model's signal. It only logs divergence between model predictions.

Ready for future multi-model routing (e.g., BTC model vs ETH model
vs SOL model).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelOutput:
    """Output of a single model in the composer."""

    model_id: str = ""
    symbol: str = ""
    side: str = ""
    confidence: float = 0.0
    regime: str = ""
    version: str = ""


@dataclass(frozen=True)
class ComposerResult:
    """Result of a multi-model comparison round.

    Attributes:
        primary: Output from the currently active model.
        alternatives: Outputs from other registered models.
        divergence_detected: True if any alternative model disagrees
            with the primary on direction (BUY vs SELL).
        divergence_details: Per-model divergence reasons.
        all_outputs: Complete list of all model outputs.
    """

    primary: ModelOutput | None = None
    alternatives: List[ModelOutput] = field(default_factory=list)
    divergence_detected: bool = False
    divergence_details: Dict[str, str] = field(default_factory=dict)
    all_outputs: List[ModelOutput] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Composer
# ---------------------------------------------------------------------------


class ModelComposer:
    """Multi-model divergence observer.

    Thread-safe and stateless. Pure comparison logic.
    """

    @staticmethod
    def compare(
        primary: ModelOutput,
        models: List[ModelOutput],
    ) -> ComposerResult:
        """Compare primary model output against alternatives.

        Parameters
        ----------
        primary : ModelOutput
            Output from the currently active (primary) model.
        models : list[ModelOutput]
            Outputs from alternative models.

        Returns
        -------
        ComposerResult
            Comparison results with divergence detection.
        """
        alternatives: List[ModelOutput] = []
        divergence_details: Dict[str, str] = {}
        all_outputs: List[ModelOutput] = [primary] + models

        for alt in models:
            if alt.model_id == primary.model_id:
                continue
            alternatives.append(alt)

            if alt.side == "":
                divergence_details[alt.model_id] = "no_prediction"
            elif alt.side != primary.side:
                divergence_details[alt.model_id] = (
                    f"side_mismatch: primary={primary.side} "
                    f"alt={alt.side} (conf: {alt.confidence:.3f})"
                )
            elif abs(alt.confidence - primary.confidence) > 0.3:
                divergence_details[alt.model_id] = (
                    f"confidence_divergence: primary={primary.confidence:.3f} "
                    f"alt={alt.confidence:.3f} (delta>0.3)"
                )
            else:
                divergence_details[alt.model_id] = "aligned"

        divergence_detected = any(
            "mismatch" in v or "divergence" in v
            for v in divergence_details.values()
        )

        return ComposerResult(
            primary=primary,
            alternatives=alternatives,
            divergence_detected=divergence_detected,
            divergence_details=divergence_details,
            all_outputs=all_outputs,
        )
