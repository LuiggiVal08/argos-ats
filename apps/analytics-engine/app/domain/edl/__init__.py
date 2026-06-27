"""EDL — Edge Decision Layer domain primitives.

This package contains the pure-domain, side-effect-free modules that
implement the hierarchical Bayesian evidence system described in
spec.md Sections 7–10.

Domain rules
============
- No module in this package may import from application or infrastructure.
- No module may reference TradeEpisode, PnL, baselines, or runtime metrics.
- The only data sources are model structure, regime context, and
  configuration constants (P_base, lambda, thresholds).

Modules
=======
prior.py         — Prior specification: P(edge | model, regime)
likelihood.py    — Likelihood: P(episodes | H₁) / P(episodes | H₀)
bayesian_update.py — Posterior = Prior × Likelihood + decay
state_mapper.py  — Probability → state mapping (per regime)
"""

from .prior import (
    DEFAULT_LAMBDA,
    P_BASE,
    ModelFamily,
    PriorComponents,
    PriorResult,
    PriorCalculator,
    Regime,
)
from .likelihood import LikelihoodCalculator
from .bayesian_update import BayesianUpdater, PosteriorResult

__all__ = [
    "DEFAULT_LAMBDA",
    "P_BASE",
    "BayesianUpdater",
    "LikelihoodCalculator",
    "ModelFamily",
    "PosteriorResult",
    "PriorComponents",
    "PriorResult",
    "PriorCalculator",
    "Regime",
]
