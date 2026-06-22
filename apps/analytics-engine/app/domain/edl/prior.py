"""Prior specification — P(edge | model, regime).

Implements spec Section 8: the Prior represents institutional skepticism.
It is a pure mathematical function of model structure and regime context.
It must NOT depend on PnL, TradeEpisodes, baselines, or any runtime data.

Usage
-----
    calc = PriorCalculator(lambda_=1.0)
    result = calc.compute(
        model_id="ema-cross-v1",
        complexity_score=0.1,
        training_regimes={Regime.TRENDING},
        evaluation_regime=Regime.TRENDING,
        model_family=ModelFamily.NEW,
    )
    # result.prior_logodds["TRENDING"]  →  log(P/(1-P))
    # result.components.complexity_penalty → exp(-1.0 * 0.1)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, FrozenSet, Optional

# ---------------------------------------------------------------------------
# Domain enums
# ---------------------------------------------------------------------------


class Regime(str, Enum):
    """Market regimes the EDL reasons about."""

    TRENDING = "TRENDING"
    RANGING = "RANGING"
    VOLATILE = "VOLATILE"


class ModelFamily(str, Enum):
    """Historical lineage of a model — affects H(model)."""

    NEW = "NEW"                        # H = 1.0
    CHAMPION_DERIVED = "CHAMPION_DERIVED"  # H = 1.2
    POOR_HISTORY = "POOR_HISTORY"      # H = 0.8


# ---------------------------------------------------------------------------
# Constants (spec Section 8)
# ---------------------------------------------------------------------------

P_BASE: float = 0.15
"""Institutional skepticism: 85% prior probability that no edge exists."""

DEFAULT_LAMBDA: float = 1.0
"""Provisional decay / complexity-penalty rate.  Final value via Monte Carlo
calibration (spec Section 8.11)."""

# ── Transfer factor T(train, eval) ─────────────────────────────────────────
# spec Section 8.8

_SAME_REGIME_T: float = 1.0
_CROSS_REGIME_T: float = 0.7
_UNKNOWN_REGIME_T: float = 0.5

# ── Historical factors H(model) ────────────────────────────────────────────
# spec Section 8.7

_H_NEW: float = 1.0
_H_CHAMPION: float = 1.2
_H_POOR: float = 0.8

# ── Regime-coverage penalties R(model) ─────────────────────────────────────
# spec Section 8.6

_R_SINGLE_REGIME: float = 0.7
_R_MULTI_REGIME: float = 1.0

# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PriorComponents:
    """Breakdown of the prior computation for auditability."""

    p_base: float = P_BASE
    complexity_penalty: float = 1.0
    regime_penalty: float = 1.0
    historical_factor: float = 1.0
    transfer_penalty: float = 1.0
    """T(eval_regime) for the *first* evaluation regime in the computed vector.
    When computing a single-regime prior, this matches that regime's T.
    For vector priors, see per-regime log-odds for the full picture."""


@dataclass(frozen=True)
class PriorResult:
    """Complete prior result for a single model.

    The prior lives as a vector per evaluation regime (spec Section 10.2).
    """

    prior_logodds: Dict[str, float]
    """Per-regime log-odds of edge.  Keys are Regime values."""

    components: PriorComponents = field(default_factory=PriorComponents)
    """Decomposition of the computation for auditability."""

    lambda_used: float = DEFAULT_LAMBDA
    """λ value used in this computation."""

    def probability(self, regime: str) -> float:
        """Recover probability from log-odds for a given regime."""
        lo = self.prior_logodds.get(regime)
        if lo is None:
            raise ValueError(f"Unknown regime: {regime}")
        return _logodds_to_prob(lo)

    def __repr__(self) -> str:
        lo_str = ", ".join(f"{k}={v:.4f}" for k, v in self.prior_logodds.items())
        return (
            f"PriorResult(prior_logodds={{{lo_str}}}, "
            f"components={self.components}, "
            f"lambda_used={self.lambda_used})"
        )


# ---------------------------------------------------------------------------
# Core calculator
# ---------------------------------------------------------------------------


class PriorCalculator:
    """Computes P(edge | model, regime) — the institutional prior.

    This is a pure function of model structure and regime context.
    It must never depend on PnL, episodes, baselines, or runtime state.

    Thread-safe and stateless (all state is in the configuration parameters).
    """

    def __init__(self, lambda_: float = DEFAULT_LAMBDA) -> None:
        if lambda_ <= 0:
            raise ValueError(f"lambda must be positive, got {lambda_}")
        self._lambda = lambda_

    # ── Public API ──────────────────────────────────────────────────────

    def compute(
        self,
        model_id: str,
        complexity_score: float,
        *,
        training_regimes: FrozenSet[Regime],
        evaluation_regime: Optional[Regime] = None,
        model_family: ModelFamily = ModelFamily.NEW,
        n_params: Optional[int] = None,
        n_trades_projected: Optional[int] = None,
    ) -> PriorResult:
        """Compute the prior for *model_id*.

        Parameters
        ----------
        model_id : str
            Human-readable identifier (for audit trails only).
        complexity_score : float
            Value in [0, 1] representing model flexibility
            (0 = simplest baseline, 1 = maximally adaptive).
            See spec Section 8.5.
        training_regimes : FrozenSet[Regime]
            Set of regimes the model was trained on.
        evaluation_regime : Regime or None
            If provided, compute prior only for this regime.
            If None, compute for all defined regimes (vector prior).
        model_family : ModelFamily
            Historical lineage class (spec Section 8.7).
        n_params : int or None
            Reserved for future calibration.  Not used in v0.
        n_trades_projected : int or None
            Reserved for future calibration.  Not used in v0.

        Returns
        -------
        PriorResult
            Per-regime log-odds and component breakdown.
        """
        _validate_inputs(complexity_score, training_regimes)

        # ── Component penalties (regime-independent) ─────────────────
        complexity_penalty = _complexity_penalty(complexity_score, self._lambda)
        regime_penalty = _regime_penalty(training_regimes)
        historical_factor = _historical_factor(model_family)

        # ── Regime-dependent transfer penalty ────────────────────────
        regimes_to_evaluate: tuple[Regime, ...]
        if evaluation_regime is not None:
            regimes_to_evaluate = (evaluation_regime,)
        else:
            regimes_to_evaluate = (Regime.TRENDING, Regime.RANGING, Regime.VOLATILE)

        prior_logodds: Dict[str, float] = {}
        for reg in regimes_to_evaluate:
            transfer_penalty = _transfer_penalty(training_regimes, reg)

            p_edge = (
                P_BASE
                * complexity_penalty
                * regime_penalty
                * historical_factor
                * transfer_penalty
            )

            p_edge = _clamp_probability(p_edge)
            prior_logodds[reg.value] = _prob_to_logodds(p_edge)

        return PriorResult(
            prior_logodds=prior_logodds,
            components=PriorComponents(
                p_base=P_BASE,
                complexity_penalty=complexity_penalty,
                regime_penalty=regime_penalty,
                historical_factor=historical_factor,
                transfer_penalty=_transfer_penalty(
                    training_regimes,
                    next(iter(regimes_to_evaluate)),
                ),
            ),
            lambda_used=self._lambda,
        )

    @property
    def lambda_(self) -> float:
        return self._lambda


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _complexity_penalty(complexity_score: float, lambda_: float) -> float:
    """C(model) = exp(-λ · complexity_score)  (spec Section 8.5)."""
    return math.exp(-lambda_ * complexity_score)


def _regime_penalty(training_regimes: FrozenSet[Regime]) -> float:
    """R(model) — penalize single-regime training (spec Section 8.6)."""
    if len(training_regimes) <= 1:
        return _R_SINGLE_REGIME
    return _R_MULTI_REGIME


def _historical_factor(model_family: ModelFamily) -> float:
    """H(model) — historical lineage (spec Section 8.7)."""
    mapping = {
        ModelFamily.NEW: _H_NEW,
        ModelFamily.CHAMPION_DERIVED: _H_CHAMPION,
        ModelFamily.POOR_HISTORY: _H_POOR,
    }
    return mapping[model_family]


def _transfer_penalty(
    training_regimes: FrozenSet[Regime],
    eval_regime: Regime,
) -> float:
    """T(train, eval) — transfer between regimes (spec Section 8.8).

    Returns
    -------
    float
        1.0 if eval_regime is in training set,
        0.7 if cross-regime,
        0.5 if training set is empty (unknown).
    """
    if not training_regimes:
        return _UNKNOWN_REGIME_T
    if eval_regime in training_regimes:
        return _SAME_REGIME_T
    return _CROSS_REGIME_T


def _prob_to_logodds(p: float) -> float:
    """p ∈ (0,1) → log(p / (1-p))."""
    return math.log(p / (1.0 - p))


def _logodds_to_prob(lo: float) -> float:
    """log-odds → probability ∈ (0,1)."""
    return 1.0 / (1.0 + math.exp(-lo))


def _clamp_probability(p: float) -> float:
    """Keep probability away from 0 and 1 to avoid numerical overflow
    in log-odds conversion."""
    EPS = 1e-12
    if p <= 0.0:
        return EPS
    if p >= 1.0:
        return 1.0 - EPS
    return p


def _validate_inputs(
    complexity_score: float,
    training_regimes: FrozenSet[Regime],
) -> None:
    if not 0.0 <= complexity_score <= 1.0:
        raise ValueError(
            f"complexity_score must be in [0, 1], got {complexity_score}"
        )
    if not isinstance(training_regimes, frozenset):
        raise TypeError(
            f"training_regimes must be a frozenset, got {type(training_regimes)}"
        )
    for r in training_regimes:
        if not isinstance(r, Regime):
            raise TypeError(f"Each element must be a Regime, got {r!r}")
