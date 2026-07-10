"""Decision Augmentation Layer — closes the feedback loop.

FASE 3.1 — DecisionAugmentation:
  After posterior is computed, adjusts execution parameters:
    - confidence: clamped by posterior_edge
    - position_size_multiplier: scaled by posterior_edge
    - risk_multiplier: scaled by posterior_confidence

  Does NOT replace the model. Does NOT modify the signal direction.
  Only adjusts: confidence, position size, risk.

FASE 3.2 — PolicyUpdateHook:
  After each settled episode, updates the system belief state.
  The belief state is a rolling window of posterior values per regime.

FASE 3.3 — AdaptiveRiskAdjustment:
  If historical posterior in the current regime is negative (edge < 0.5),
  reduces position size and tightens SL.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import structlog

from ...domain.edl import PosteriorResult
from .event_memory import MemoryIndex
from .trade_episode import TradeEpisode, TradeEpisodeStore

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MIN_CONFIDENCE: float = 0.3
"""Minimum confidence after posterior adjustment."""
_MAX_CONFIDENCE: float = 0.95
"""Maximum confidence after posterior adjustment."""
_MIN_POSITION_MULT: float = 0.0
"""Minimum position size multiplier (can zero out a trade)."""
_MAX_POSITION_MULT: float = 1.0
"""Maximum position size multiplier (no boost above 1.0)."""
_MIN_RISK_MULT: float = 0.5
"""Minimum risk multiplier (tighten SL to 50% of normal)."""
_MAX_RISK_MULT: float = 1.0
"""Maximum risk multiplier (normal risk)."""

_ADAPTIVE_WINDOW_SIZE: int = 20
"""Number of recent trades per regime for adaptive risk."""


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class AugmentedParameters:
    """Execution parameters after posterior augmentation.

    These are multiplicative modifiers applied to standard execution:
      - final_confidence: used for signal validation (higher = more selective)
      - position_size_mult: 0.0 to 1.0, scales position size
      - risk_mult: 0.5 to 1.0, scales SL distance (lower = tighter SL)
    """
    final_confidence: float = 0.0
    position_size_mult: float = 1.0
    risk_mult: float = 1.0
    edge: float = 0.5
    augmentation_applied: bool = False
    reason: str = ""


@dataclass
class RegimeBeliefState:
    """Rolling belief state for a single regime."""
    regime: str = ""
    recent_posteriors: list[float] = field(default_factory=list)
    edge: float = 0.5
    confidence: float = 0.5
    trade_count: int = 0

    @property
    def avg_posterior(self) -> float:
        if not self.recent_posteriors:
            return 0.5
        return sum(self.recent_posteriors) / len(self.recent_posteriors)

    @property
    def is_negative(self) -> bool:
        """Edge < 0.5 means model is underperforming random in this regime."""
        return self.avg_posterior < 0.45


# ---------------------------------------------------------------------------
# Decision Augmentation Layer
# ---------------------------------------------------------------------------


class DecisionAugmentation:
    """Adjusts execution parameters based on posterior edge.

    Called BEFORE execution with the prior and signal info.
    Produces AugmentedParameters consumed by ExecuteSignalUseCase.
    """

    @staticmethod
    def augment(
        model_confidence: float,
        posterior_edge: float,
        posterior_confidence: float,
        regime: str = "",
        *,
        position_size_mult_override: float | None = None,
        risk_mult_override: float | None = None,
    ) -> AugmentedParameters:
        """Compute augmented execution parameters from posterior.

        Args:
            model_confidence: Raw model confidence (0..1).
            posterior_edge: P(edge | outcome) from Bayesian update.
            posterior_confidence: Confidence in posterior estimate.
            regime: Market regime for logging.
            position_size_mult_override: Bypass augmentation (for testing).
            risk_mult_override: Bypass augmentation (for testing).

        Returns:
            AugmentedParameters with final confidence, position and risk multipliers.
        """
        if position_size_mult_override is not None:
            return AugmentedParameters(
                final_confidence=model_confidence,
                position_size_mult=position_size_mult_override,
                risk_mult=risk_mult_override or 1.0,
                edge=posterior_edge,
                augmentation_applied=False,
                reason="overridden",
            )

        # Edge < 0.5 means posterior says model underperforms random
        # Edge > 0.5 means posterior says model has edge
        edge = posterior_edge
        conf = posterior_confidence

        # 1. Adjust confidence: scale by (edge - 0.5) * 2 → [-1, 1]
        edge_delta = (edge - 0.5) * 2.0
        confidence_adjustment = edge_delta * conf * 0.15
        final_confidence = model_confidence + confidence_adjustment
        final_confidence = max(_MIN_CONFIDENCE, min(_MAX_CONFIDENCE, final_confidence))
        confidence_changed = abs(final_confidence - model_confidence) > 0.01

        # 2. Position size multiplier: edge > 0.6 → full size, edge < 0.4 → reduced
        if edge >= 0.55:
            position_size_mult = _MAX_POSITION_MULT
        elif edge >= 0.45:
            position_size_mult = 0.75 + (edge - 0.45) * 2.5
        else:
            position_size_mult = _MIN_POSITION_MULT + edge * 1.0
        position_size_mult = max(_MIN_POSITION_MULT, min(_MAX_POSITION_MULT, position_size_mult))

        # 3. Risk multiplier: lower edge → tighter SL
        risk_mult = _MIN_RISK_MULT + (edge * (_MAX_RISK_MULT - _MIN_RISK_MULT))
        risk_mult = max(_MIN_RISK_MULT, min(_MAX_RISK_MULT, risk_mult))

        augmentation_applied = confidence_changed or position_size_mult < 0.95 or risk_mult < 0.95

        reasons = []
        if confidence_changed:
            reasons.append(f"confidence:{model_confidence:.3f}->{final_confidence:.3f}")
        if position_size_mult < 0.95:
            reasons.append(f"size:{position_size_mult:.3f}")
        if risk_mult < 0.95:
            reasons.append(f"risk:{risk_mult:.3f}")

        if augmentation_applied:
            log.info(
                "ccl:augmented",
                edge=round(edge, 4),
                edge_delta=round(edge_delta, 4),
                regime=regime,
                final_confidence=round(final_confidence, 4),
                position_size_mult=round(position_size_mult, 4),
                risk_mult=round(risk_mult, 4),
                reason="; ".join(reasons),
            )

        return AugmentedParameters(
            final_confidence=round(final_confidence, 4),
            position_size_mult=round(position_size_mult, 4),
            risk_mult=round(risk_mult, 4),
            edge=round(edge, 4),
            augmentation_applied=augmentation_applied,
            reason="; ".join(reasons),
        )


# ---------------------------------------------------------------------------
# Policy Update Hook (FASE 3.2)
# ---------------------------------------------------------------------------


class PolicyUpdateHook:
    """Updates system belief state after each settled episode.

    Maintains per-regime rolling window of posterior values.
    """

    def __init__(self) -> None:
        self._belief_states: dict[str, RegimeBeliefState] = {}
        self._global_edge: float = 0.5
        self._global_confidence: float = 0.0
        self._total_settled: int = 0

    def update(self, episode: TradeEpisode) -> RegimeBeliefState:
        """Update belief state from a settled episode.

        Called after CCLService.settle_episode().
        """
        regime = episode.regime_at_entry or "UNKNOWN"

        if regime not in self._belief_states:
            self._belief_states[regime] = RegimeBeliefState(regime=regime)

        state = self._belief_states[regime]
        state.recent_posteriors.append(episode.posterior_edge)
        if len(state.recent_posteriors) > _ADAPTIVE_WINDOW_SIZE:
            state.recent_posteriors.pop(0)
        state.edge = round(state.avg_posterior, 4)
        state.confidence = round(episode.posterior_confidence, 4)
        state.trade_count += 1
        self._total_settled += 1

        # Global estimate
        all_posteriors = []
        for s in self._belief_states.values():
            all_posteriors.extend(s.recent_posteriors)
        if all_posteriors:
            self._global_edge = round(sum(all_posteriors) / len(all_posteriors), 4)
            self._global_confidence = min(0.5 + 0.1 * len(all_posteriors) ** 0.5, 0.95)

        log.info(
            "ccl:belief_updated",
            regime=regime,
            edge=state.edge,
            confidence=state.confidence,
            trade_count=state.trade_count,
            global_edge=self._global_edge,
            total_settled=self._total_settled,
            is_negative=state.is_negative,
        )

        return state

    def get_regime_state(self, regime: str) -> RegimeBeliefState | None:
        return self._belief_states.get(regime)

    @property
    def global_edge(self) -> float:
        return self._global_edge

    @property
    def global_confidence(self) -> float:
        return self._global_confidence

    @property
    def total_settled(self) -> int:
        return self._total_settled

    def snapshot(self) -> dict[str, Any]:
        return {
            "global_edge": self._global_edge,
            "global_confidence": self._global_confidence,
            "total_settled": self._total_settled,
            "regimes": {
                regime: {
                    "edge": state.edge,
                    "confidence": state.confidence,
                    "trade_count": state.trade_count,
                    "is_negative": state.is_negative,
                    "avg_posterior": state.avg_posterior,
                }
                for regime, state in self._belief_states.items()
            },
        }


# ---------------------------------------------------------------------------
# Adaptive Risk Adjustment (FASE 3.3)
# ---------------------------------------------------------------------------


class AdaptiveRiskAdjuster:
    """Reduces exposure based on historical posterior performance.

    If the system's belief state for a regime is negative (edge < 0.45),
    reduce position size and tighten SL for trades in that regime.
    """

    def __init__(
        self,
        belief_state: PolicyUpdateHook,
        memory_index: MemoryIndex | None = None,
    ) -> None:
        self._belief = belief_state
        self._memory = memory_index
        self._consecutive_losses: int = 0
        self._max_consecutive_losses: int = 3

    def get_adjustments(
        self,
        regime: str,
        confidence_band: str = "",
    ) -> AugmentedParameters:
        """Compute risk adjustments based on historical performance.

        Args:
            regime: Current market regime.
            confidence_band: Current prediction confidence band.

        Returns:
            AugmentedParameters (only position_size_mult and risk_mult matter).
        """
        state = self._belief.get_regime_state(regime)
        if state is None:
            return AugmentedParameters(
                final_confidence=0.0,
                position_size_mult=1.0,
                risk_mult=1.0,
                augmentation_applied=False,
                reason="no_regime_data",
            )

        # Base adjustments from belief state
        if state.is_negative:
            position_size_mult = max(_MIN_POSITION_MULT, 0.3)
            risk_mult = _MIN_RISK_MULT
            reason = f"regime_belief_negative(edge={state.edge})"
        elif state.edge < 0.48:
            position_size_mult = 0.6
            risk_mult = 0.75
            reason = f"regime_belief_cautious(edge={state.edge})"
        else:
            position_size_mult = 1.0
            risk_mult = 1.0
            reason = f"regime_belief_neutral(edge={state.edge})"

        # Check memory index for worst context match
        if self._memory is not None and confidence_band:
            self._memory.build()
            worst = self._memory.worst_contexts(top_n=3)
            for ctx in worst:
                if ctx.regime == regime and ctx.confidence_band == confidence_band:
                    if ctx.win_rate < 0.3:
                        position_size_mult *= 0.5
                        risk_mult = _MIN_RISK_MULT
                        reason += f"; memory_worst_context(wr={ctx.win_rate})"
                    break

        position_size_mult = max(_MIN_POSITION_MULT, min(_MAX_POSITION_MULT, position_size_mult))
        risk_mult = max(_MIN_RISK_MULT, min(_MAX_RISK_MULT, risk_mult))

        if position_size_mult < 1.0 or risk_mult < 1.0:
            log.info(
                "ccl:adaptive_risk",
                regime=regime,
                confidence_band=confidence_band,
                position_size_mult=round(position_size_mult, 3),
                risk_mult=round(risk_mult, 3),
                reason=reason,
                belief_edge=state.edge,
            )

        return AugmentedParameters(
            final_confidence=0.0,
            position_size_mult=round(position_size_mult, 4),
            risk_mult=round(risk_mult, 4),
            edge=state.edge,
            augmentation_applied=position_size_mult < 1.0 or risk_mult < 1.0,
            reason=reason,
        )
