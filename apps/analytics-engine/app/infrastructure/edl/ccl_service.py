"""CCLService — Cognitive Completion Layer orchestrator.

Wires together PriorCalculator, LikelihoodCalculator, BayesianUpdater,
ModelComposer, EventStore, and Redis into a single dependency for the
decision loop.

Two hooks provided:
  1. enrich_prediction() — called after pipeline.predict()
  2. attach_posterior() — called before ExecutionGuard.execute()
  3. cache_prior() — stores prior for later settlement
  4. settle_episode() — called when a trade is closed
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

import structlog

from ...domain.edl import (
    BayesianUpdater,
    LikelihoodCalculator,
    PosteriorResult,
    PriorCalculator,
    PriorResult,
    Regime,
)
from ...domain.edl.composer import ComposerResult, ModelOutput
from ...domain.edl.composer import ModelComposer as DomainModelComposer
from ..edl.trade_episode import TradeEpisode, TradeEpisodeStore
from ..event_sourcing import EventRecorder

log = structlog.get_logger()

_REDIS_CCL_STREAM = "ccl:updates"


class CCLService:
    """Cognitive Completion Layer — enriches every trade with Bayesian reasoning.
    """

    def __init__(
        self,
        redis_client: Any,
        event_store: Any,
        episode_store: TradeEpisodeStore,
        redis_stream: str = _REDIS_CCL_STREAM,
        prior_calculator: PriorCalculator | None = None,
    ) -> None:
        self._redis = redis_client
        self._event_store = event_store
        self._episode_store = episode_store
        self._redis_stream = redis_stream
        self._prior_calc = prior_calculator or PriorCalculator()
        self._likelihood_calc = LikelihoodCalculator()
        self._bayesian = BayesianUpdater()
        self._composer = DomainModelComposer()

        # Cache: store PriorResult keyed by episode_id for settlement
        self._prior_cache: dict[str, PriorResult] = {}

    # ── Hook 1: enrich prediction with prior ──────────────────────────

    async def enrich_prediction(
        self,
        result: Any,
        features: dict[str, float] | None,
    ) -> PriorResult | None:
        """Compute prior and log it. Called after pipeline.predict()."""
        if result.signal is None:
            return None

        regime_str = result.regime or "UNKNOWN"
        eval_regime = (
            Regime(regime_str)
            if regime_str in ("RANGING", "TRENDING", "VOLATILE")
            else None
        )

        from ...domain.edl import ModelFamily

        prior = self._prior_calc.compute(
            model_id=result.model_version or "unknown",
            complexity_score=0.3,
            training_regimes=frozenset({Regime.RANGING, Regime.TRENDING}),
            evaluation_regime=eval_regime,
            model_family=ModelFamily.NEW,
        )

        prior_edge = prior.probability(regime_str) if regime_str in prior.prior_logodds else 0.5
        prior_logodds = prior.prior_logodds.get(regime_str, 0.0)

        log.info(
            "ccl:prior_computed",
            model=result.model_version,
            regime=regime_str,
            prior_edge=round(prior_edge, 4),
            prior_logodds=round(prior_logodds, 4),
        )

        await EventRecorder.record(
            self._event_store,
            event_type="ccl.prior_computed",
            source="ccl_service",
            data={
                "model_version": result.model_version,
                "regime": regime_str,
                "prior_edge": prior_edge,
                "prior_logodds": prior_logodds,
                "components": {
                    "p_base": prior.components.p_base,
                    "complexity_penalty": prior.components.complexity_penalty,
                    "regime_penalty": prior.components.regime_penalty,
                    "historical_factor": prior.components.historical_factor,
                    "transfer_penalty": prior.components.transfer_penalty,
                },
            },
        )

        return prior

    # ── Hook 2: attach posterior to signal before execution ───────────

    async def attach_posterior(
        self,
        execution_signal: Any,
        prior: PriorResult | None,
        result: Any,
    ) -> None:
        """Attach prior info to execution signal metadata.

        Called BEFORE ExecutionGuard.execute(). Full posterior is
        computed at settlement time when outcome data is available.
        """
        if prior is None:
            return

        regime_str = result.regime or "UNKNOWN"
        prior_edge = prior.probability(regime_str) if regime_str in prior.prior_logodds else 0.5

        execution_signal.metadata["ccl_prior_edge"] = round(prior_edge, 4)
        execution_signal.metadata["ccl_posterior_edge"] = round(prior_edge, 4)
        execution_signal.metadata["ccl_prior_logodds"] = round(
            prior.prior_logodds.get(regime_str, 0.0), 4
        )
        execution_signal.metadata["ccl_regime"] = regime_str

        log.info(
            "ccl:posterior_attached",
            signal_id=execution_signal.signal_id,
            side=execution_signal.side.name if hasattr(execution_signal.side, "name") else str(execution_signal.side),
            prior_edge=round(prior_edge, 4),
        )

    # ── Cache prior for later settlement ──────────────────────────────

    def cache_prior(self, episode_id: str, prior: PriorResult) -> None:
        self._prior_cache[episode_id] = prior

    def get_prior(self, episode_id: str) -> PriorResult | None:
        """Retrieve cached PriorResult for an episode."""
        return self._prior_cache.get(episode_id)

    # ── Hook 3: settle episode with likelihood + posterior ────────────

    async def settle_episode(
        self,
        episode: TradeEpisode,
        pnl: Decimal,
        t_exit: datetime,
        prediction_confidence: float,
        prediction_side: str,
        regime: str,
        prior: PriorResult | None,
        total_episodes: int,
        entry_price: Decimal | None = None,
    ) -> TradeEpisode | None:
        """Compute likelihood and posterior, then settle the episode.

        Called when a trade closes (from phase_b_loop).
        """
        if prior is None:
            return None

        # Compute likelihood from actual PnL
        likelihood = self._likelihood_calc.compute(
            prediction_confidence=prediction_confidence,
            prediction_side=prediction_side,
            actual_pnl=pnl,
            regime=regime,
            entry_price=entry_price,
        )

        # Compute posterior
        posterior = self._bayesian.compute(
            prior=prior,
            likelihood=likelihood,
            regime=regime,
            total_episodes=total_episodes,
        )

        # Classify outcome
        outcome = "WIN" if pnl > Decimal("0") else ("LOSS" if pnl < Decimal("0") else "BREAK_EVEN")

        # Settle the episode with likelihood and posterior
        settled = episode.settle(
            t_exit=t_exit,
            pnl=pnl,
            execution_outcome=outcome,
            likelihood_score=likelihood.likelihood_score,
            likelihood_confidence=likelihood.likelihood_confidence,
            posterior_edge=posterior.posterior_edge,
            posterior_confidence=posterior.posterior_confidence,
            updated_trade_belief=posterior.updated_trade_belief,
        )

        # Publish to Redis stream
        await self._publish_ccl_update(settled, likelihood, posterior)

        # Write event
        await EventRecorder.record(
            self._event_store,
            event_type="ccl.episode_settled",
            source="ccl_service",
            data={
                "episode_id": settled.episode_id,
                "pnl": str(pnl),
                "outcome": outcome,
                "likelihood_score": likelihood.likelihood_score,
                "likelihood_confidence": likelihood.likelihood_confidence,
                "posterior_edge": posterior.posterior_edge,
                "posterior_confidence": posterior.posterior_confidence,
                "updated_trade_belief": posterior.updated_trade_belief,
                "regime": regime,
            },
        )

        log.info(
            "ccl:episode_settled",
            episode_id=settled.episode_id,
            pnl=str(pnl),
            outcome=outcome,
            likelihood_score=likelihood.likelihood_score,
            posterior_edge=posterior.posterior_edge,
            belief=posterior.updated_trade_belief,
        )

        # Clean up cache
        self._prior_cache.pop(episode.episode_id, None)

        return settled

    # ── Composer: multi-model comparison ──────────────────────────────

    async def compare_models(
        self,
        primary_side: str,
        primary_confidence: float,
        model_version: str,
        regime: str,
        feature_hash: str,
    ) -> ComposerResult:
        """Run multi-model comparison and log divergence."""
        primary = ModelOutput(
            model_id=model_version or "primary",
            symbol="BTC/USDT",
            side=primary_side,
            confidence=primary_confidence,
            regime=regime,
            version=model_version or "1.0.0",
        )

        alternatives: list[ModelOutput] = []

        result = self._composer.compare(primary, alternatives)

        if result.divergence_detected:
            log.warning(
                "ccl:model_divergence_detected",
                primary_side=primary_side,
                primary_confidence=primary_confidence,
                divergence_details=result.divergence_details,
            )
        else:
            log.info(
                "ccl:model_comparison",
                primary_side=primary_side,
                primary_confidence=primary_confidence,
                model_count=len(result.all_outputs),
                divergence_detected=False,
            )

        await EventRecorder.record(
            self._event_store,
            event_type="ccl.model_comparison",
            source="ccl_service",
            data={
                "primary_side": primary_side,
                "primary_confidence": primary_confidence,
                "primary_model": model_version,
                "regime": regime,
                "divergence_detected": result.divergence_detected,
                "alternative_count": len(alternatives),
            },
        )

        return result

    # ── Internal helpers ──────────────────────────────────────────────

    async def _publish_ccl_update(
        self,
        episode: TradeEpisode,
        likelihood: Any,
        posterior: PosteriorResult,
    ) -> None:
        """Publish CCL update to Redis stream."""
        if self._redis is None:
            return
        try:
            payload = {
                "episode_id": episode.episode_id,
                "side": episode.side,
                "pnl": str(episode.pnl),
                "outcome": episode.execution_outcome,
                "likelihood_score": likelihood.likelihood_score,
                "likelihood_confidence": likelihood.likelihood_confidence,
                "posterior_edge": posterior.posterior_edge,
                "posterior_confidence": posterior.posterior_confidence,
                "belief": posterior.updated_trade_belief,
                "regime": episode.regime_at_entry,
            }
            await self._redis.xadd(
                self._redis_stream,
                {"data": json.dumps(payload)},
                maxlen=1000,
            )
            log.debug("ccl:redis_published", stream=self._redis_stream)
        except Exception as exc:
            log.warning("ccl:redis_publish_failed", error=str(exc))
