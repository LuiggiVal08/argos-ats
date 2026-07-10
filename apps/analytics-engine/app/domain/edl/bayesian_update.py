"""Bayesian Update — Posterior = Prior × Likelihood + decay.

Combines the institutional prior P(edge | model, regime) with the
observed likelihood P(outcome | H₁) / P(outcome | H₀) to produce
a posterior belief about the model's edge.

    posterior_logodds = prior_logodds + log(likelihood_ratio)

The posterior is the system's updated belief that the model has
a genuine statistical edge after observing the trade outcome.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Optional

from .likelihood import LikelihoodResult
from .prior import PriorResult


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_POSTERIOR_DECAY_FACTOR: float = 0.95
"""Decay applied to the posterior log-odds before the next update.
Prevents unbounded growth of confidence from repeated observations."""

_POSTERIOR_LOGODDS_CLAMP: float = 5.0
"""Maximum absolute log-odds to avoid numerical overflow.
Corresponds to P ≈ 0.993 — sufficient for trading decisions."""

_MIN_EPISODES_FOR_CONFIDENCE: int = 3
"""Minimum episodes before posterior confidence exceeds 0.5."""


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PosteriorComponents:
    """Decomposition of the posterior computation for auditability."""

    prior_logodds: float = 0.0
    likelihood_logodds: float = 0.0
    posterior_logodds_raw: float = 0.0
    decay_applied: bool = False
    clamped: bool = False


@dataclass(frozen=True)
class PosteriorResult:
    """Result of a Bayesian update for a single trade.

    Attributes:
        posterior_edge: P(edge | outcome) in [0, 1].
            Updated belief that the model has a genuine edge.
        posterior_confidence: Confidence in the posterior estimate in [0, 1].
            Scales with total observed episodes.
        updated_trade_belief: Qualitative assessment of what this
            trade's outcome implies about the model:
            - "STRENGTHENED" — outcome supports edge hypothesis
            - "WEAKENED" — outcome contradicts edge hypothesis
            - "NEUTRAL" — inconclusive outcome
        components: Full breakdown for audit trails.
    """

    posterior_edge: float = 0.5
    posterior_confidence: float = 0.5
    updated_trade_belief: str = "NEUTRAL"
    components: PosteriorComponents = field(default_factory=PosteriorComponents)


# ---------------------------------------------------------------------------
# Bayesian updater
# ---------------------------------------------------------------------------


class BayesianUpdater:
    """Combines prior and likelihood to compute posterior edge belief.

    Thread-safe and stateless (all state is in the input parameters).
    """

    @staticmethod
    def compute(
        prior: PriorResult,
        likelihood: LikelihoodResult,
        regime: str,
        total_episodes: int = 1,
        previous_posterior_edge: Optional[float] = None,
    ) -> PosteriorResult:
        """Compute the posterior edge belief after observing a trade.

        Parameters
        ----------
        prior : PriorResult
            Prior belief from PriorCalculator.
        likelihood : LikelihoodResult
            Likelihood from LikelihoodCalculator.
        regime : str
            Market regime for which to compute posterior.
        total_episodes : int
            Total number of episodes observed (for confidence scaling).
        previous_posterior_edge : float or None
            Previous posterior edge value for decay application.
            If None, no decay is applied.

        Returns
        -------
        PosteriorResult
            Updated posterior edge, confidence, and belief assessment.
        """
        # 1. Get prior log-odds for the given regime
        prior_logodds = prior.prior_logodds.get(regime, 0.0)

        # 2. Get likelihood log-odds
        lr = likelihood.components.likelihood_ratio
        if lr <= 0:
            lr = 0.01
        likelihood_logodds = math.log(lr)

        # 3. Apply decay to historical posterior if available
        decay_logodds = 0.0
        decay_applied = False
        if previous_posterior_edge is not None:
            prev_logodds = _prob_to_logodds(
                _clamp(previous_posterior_edge, 0.01, 0.99)
            )
            decay_logodds = prev_logodds * (1.0 - _POSTERIOR_DECAY_FACTOR)
            decay_applied = True

        # 4. Combine: posterior_logodds = prior + likelihood + decay_adjustment
        posterior_logodds = prior_logodds + likelihood_logodds + decay_logodds

        # 5. Clamp to avoid numerical overflow
        clamped = False
        if abs(posterior_logodds) > _POSTERIOR_LOGODDS_CLAMP:
            posterior_logodds = math.copysign(
                _POSTERIOR_LOGODDS_CLAMP, posterior_logodds
            )
            clamped = True

        # 6. Convert to probability
        posterior_edge = _logodds_to_prob(posterior_logodds)
        posterior_edge = round(posterior_edge, 4)

        # 7. Confidence scales with total episodes observed
        posterior_confidence = min(
            1.0,
            0.5 + 0.1 * math.sqrt(total_episodes),
        )
        posterior_confidence = round(posterior_confidence, 4)

        # 8. Qualitative belief assessment
        if likelihood.likelihood_score > 0.6:
            updated_trade_belief = "STRENGTHENED"
        elif likelihood.likelihood_score < 0.4:
            updated_trade_belief = "WEAKENED"
        else:
            updated_trade_belief = "NEUTRAL"

        return PosteriorResult(
            posterior_edge=posterior_edge,
            posterior_confidence=posterior_confidence,
            updated_trade_belief=updated_trade_belief,
            components=PosteriorComponents(
                prior_logodds=round(prior_logodds, 4),
                likelihood_logodds=round(likelihood_logodds, 4),
                posterior_logodds_raw=round(posterior_logodds, 4),
                decay_applied=decay_applied,
                clamped=clamped,
            ),
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _prob_to_logodds(p: float) -> float:
    return math.log(p / (1.0 - p))


def _logodds_to_prob(lo: float) -> float:
    return 1.0 / (1.0 + math.exp(-lo))


def _clamp(value: float, lo: float, hi: float) -> float:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value
