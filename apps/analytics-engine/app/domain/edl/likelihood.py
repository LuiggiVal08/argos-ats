"""Likelihood: P(episodes | H₁) / P(episodes | H₀).

Given a prediction (side, confidence) and the actual outcome (PNL),
compute the likelihood ratio:

    LR = P(outcome | H₁) / P(outcome | H₀)

Where H₁ = "model has edge" and H₀ = "model has no edge".

The likelihood is a pure function of prediction quality and outcome.
It does NOT depend on prior beliefs or episode history.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MIN_CONFIDENCE_CLAMP: float = 0.01
"""Minimum confidence to avoid log(0) or division by zero."""
_MAX_CONFIDENCE_CLAMP: float = 0.99
"""Maximum confidence to avoid log(0) or division by zero."""

_CONFIDENCE_SIGMA: float = 0.25
"""Scaling for confidence-weighted likelihood (smaller = sharper)."""


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LikelihoodComponents:
    """Decomposition of the likelihood computation for auditability."""

    clamped_confidence: float = 0.5
    likelihood_h1: float = 0.5
    likelihood_h0: float = 0.5
    likelihood_ratio: float = 1.0
    normalized_pnl: float = 0.0
    pnl_sign: float = 0.0


@dataclass(frozen=True)
class LikelihoodResult:
    """Result of a likelihood computation for a single trade.

    Attributes:
        likelihood_score: P(outcome | H₁) in [0, 1].
            How compatible the observed outcome is with the hypothesis
            that the model has a genuine edge.
        likelihood_confidence: Confidence in the estimate in [0, 1].
            Derived from prediction confidence magnitude — higher
            confidence predictions yield more informative likelihoods.
        components: Full breakdown for audit trails.
    """

    likelihood_score: float = 0.5
    likelihood_confidence: float = 0.5
    components: LikelihoodComponents = field(default_factory=LikelihoodComponents)


# ---------------------------------------------------------------------------
# Likelihood calculator
# ---------------------------------------------------------------------------


class LikelihoodCalculator:
    """Computes likelihood of a trade outcome given a prediction.

    Thread-safe and stateless. Pure mathematical function.
    """

    @staticmethod
    def compute(
        prediction_confidence: float,
        prediction_side: str,
        actual_pnl: Decimal,
        regime: str = "",
        entry_price: Decimal | None = None,
    ) -> LikelihoodResult:
        """Compute the likelihood ratio for a single trade.

        Parameters
        ----------
        prediction_confidence : float
            Model confidence at prediction time (0..1).
        prediction_side : str
            "BUY" or "SELL".
        actual_pnl : Decimal
            Realized PnL for this trade (positive = win, negative = loss).
        regime : str
            Market regime at entry (RANGING, TRENDING, etc.).
        entry_price : Decimal or None
            Entry price for normalization (optional).

        Returns
        -------
        LikelihoodResult
            Likelihood score, confidence, and component breakdown.
        """
        # Clamp confidence to avoid edge cases
        conf = _clamp(prediction_confidence, _MIN_CONFIDENCE_CLAMP, _MAX_CONFIDENCE_CLAMP)

        # Determine outcome sign and magnitude
        pnl_float = float(actual_pnl)
        pnl_sign = 1.0 if pnl_float > 0 else (-1.0 if pnl_float < 0 else 0.0)

        if pnl_sign == 0.0:
            return LikelihoodResult(
                likelihood_score=0.5,
                likelihood_confidence=0.0,
                components=LikelihoodComponents(
                    clamped_confidence=conf,
                    likelihood_h1=0.5,
                    likelihood_h0=0.5,
                    likelihood_ratio=1.0,
                    normalized_pnl=0.0,
                    pnl_sign=0.0,
                ),
            )

        # Normalize PnL magnitude relative to ATR-like expected move
        if entry_price is not None and entry_price > 0:
            normalized_pnl = min(1.0, abs(pnl_float) / (float(entry_price) * 0.01))
        else:
            normalized_pnl = min(1.0, abs(pnl_float) / 100.0)

        # Likelihood ratio for a single trade:
        #   H₁: model has edge → confident wins, hedged losses
        #   H₀: model has no edge → outcomes are random
        #
        # Under H₁: P(outcome | H₁) = weight_win if profitable else weight_loss
        #   - weight_win = confidence × normalized_pnl
        #   - weight_loss = (1 - confidence) × normalized_pnl (loss expected when wrong)
        #
        # Under H₀: P(outcome | H₀) = 0.5 (random walk)

        if pnl_sign > 0:
            likelihood_h1 = conf * (0.5 + 0.5 * normalized_pnl)
            likelihood_h0 = 0.5
        else:
            likelihood_h1 = (1.0 - conf) * (0.5 + 0.5 * normalized_pnl)
            likelihood_h0 = 0.5

        likelihood_h1 = _clamp(likelihood_h1, 0.01, 0.99)
        likelihood_h0 = _clamp(likelihood_h0, 0.01, 0.99)

        likelihood_ratio = likelihood_h1 / likelihood_h0

        # Likelihood score = sigmoid of log likelihood ratio, mapped to [0, 1]
        log_lr = math.log(likelihood_ratio)
        likelihood_score = 1.0 / (1.0 + math.exp(-log_lr / _CONFIDENCE_SIGMA))

        # Confidence in the estimate = function of prediction confidence
        # Higher confidence predictions give more information about edge
        likelihood_confidence = 0.5 + 0.5 * (conf - 0.5) * 2.0 * normalized_pnl
        likelihood_confidence = _clamp(likelihood_confidence, 0.0, 1.0)

        return LikelihoodResult(
            likelihood_score=round(likelihood_score, 4),
            likelihood_confidence=round(likelihood_confidence, 4),
            components=LikelihoodComponents(
                clamped_confidence=round(conf, 4),
                likelihood_h1=round(likelihood_h1, 4),
                likelihood_h0=round(likelihood_h0, 4),
                likelihood_ratio=round(likelihood_ratio, 4),
                normalized_pnl=round(normalized_pnl, 4),
                pnl_sign=pnl_sign,
            ),
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float, hi: float) -> float:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value
