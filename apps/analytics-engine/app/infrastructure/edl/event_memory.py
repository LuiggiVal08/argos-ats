"""Event Memory — EventStore as a queryable cognitive memory system.

FASE 2.1 — EventAggregator:
  Groups settled episodes by regime, confidence bands, PnL outcome.
  Answers queries like:
    - "what contexts generate losses?"
    - "which regime produces false positives?"
    - "what confidence range has best edge?"

FASE 2.2 — MemoryIndex:
  Builds a context_hash → performance_stats index.
  Every settled episode produces a context record that can be queried
  to understand which trading contexts (regime × confidence × outcome)
  have historically performed well or poorly.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import structlog

from .trade_episode import TradeEpisode, TradeEpisodeStore

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Aggregation buckets
# ---------------------------------------------------------------------------

_CONFIDENCE_BANDS: list[tuple[str, float, float]] = [
    ("very_low", 0.0, 0.3),
    ("low", 0.3, 0.5),
    ("medium", 0.5, 0.7),
    ("high", 0.7, 0.85),
    ("very_high", 0.85, 1.0),
]


def _confidence_band(confidence: float) -> str:
    for name, lo, hi in _CONFIDENCE_BANDS:
        if lo <= confidence < hi:
            return name
    return "unknown"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class RegimeStats:
    """Performance stats grouped by market regime."""
    regime: str = ""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    avg_confidence: float = 0.0
    avg_likelihood: float = 0.0
    avg_posterior_edge: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0

    @property
    def loss_trades(self) -> int:
        return self.total_trades - self.wins


@dataclass
class ConfidenceBandStats:
    """Performance stats grouped by confidence band."""
    band: str = ""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    avg_posterior_edge: float = 0.0
    win_rate: float = 0.0
    avg_edge: float = 0.0


@dataclass
class OutcomeStats:
    """Aggregated stats by execution outcome."""
    outcome: str = ""
    count: int = 0
    total_pnl: float = 0.0
    avg_confidence: float = 0.0
    avg_likelihood: float = 0.0
    avg_posterior_edge: float = 0.0
    total_posterior_confidence: float = 0.0


@dataclass
class MemoryRecord:
    """A single record in the cognitive memory index.

    Keyed by context_hash = SHA256(regime + confidence_band + outcome).
    """
    context_hash: str = ""
    regime: str = ""
    confidence_band: str = ""
    outcome: str = ""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    avg_posterior_edge: float = 0.0
    avg_likelihood_score: float = 0.0
    avg_confidence: float = 0.0
    recent_episodes: list[str] = field(default_factory=list)
    _last_pnl: float = 0.0

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return round(self.wins / self.total_trades, 4)


@dataclass
class AggregatedReport:
    """Full aggregated report from EventAggregator."""
    by_regime: list[RegimeStats] = field(default_factory=list)
    by_confidence: list[ConfidenceBandStats] = field(default_factory=list)
    by_outcome: list[OutcomeStats] = field(default_factory=list)
    memory_index: list[MemoryRecord] = field(default_factory=list)
    total_trades: int = 0
    total_settled: int = 0
    overall_win_rate: float = 0.0
    overall_pnl: float = 0.0


# ---------------------------------------------------------------------------
# Event Aggregator
# ---------------------------------------------------------------------------


class EventAggregator:
    """Aggregates settled episodes by regime, confidence band, and outcome.

    Enables queries like:
      "what regimes generate losses?" → by_regime with low win_rate
      "which confidence band has best edge?" → by_confidence with highest win_rate
      "what outcome patterns exist?" → by_outcome distribution
    """

    def __init__(self, episode_store: TradeEpisodeStore) -> None:
        self._episode_store = episode_store

    def aggregate(self) -> AggregatedReport:
        """Scan episode store and produce aggregated stats."""
        settled = self._episode_store.all_settled
        report = AggregatedReport(
            total_settled=len(settled),
            total_trades=len(settled),
        )

        if not settled:
            return report

        # Group by regime
        regime_map: dict[str, list[TradeEpisode]] = {}
        for ep in settled:
            regime_map.setdefault(ep.regime_at_entry or "UNKNOWN", []).append(ep)

        for regime, episodes in regime_map.items():
            wins = sum(1 for e in episodes if e.execution_outcome == "WIN")
            tot_pnl = sum(float(e.pnl) for e in episodes)
            confidences = [e.likelihood_confidence for e in episodes if e.likelihood_confidence > 0]
            likelihoods = [e.likelihood_score for e in episodes if e.likelihood_score > 0]
            posteriors = [e.posterior_edge for e in episodes if e.posterior_edge > 0]
            report.by_regime.append(RegimeStats(
                regime=regime,
                total_trades=len(episodes),
                wins=wins,
                losses=len(episodes) - wins,
                total_pnl=round(tot_pnl, 2),
                avg_confidence=round(sum(confidences) / len(confidences), 4) if confidences else 0.0,
                avg_likelihood=round(sum(likelihoods) / len(likelihoods), 4) if likelihoods else 0.0,
                avg_posterior_edge=round(sum(posteriors) / len(posteriors), 4) if posteriors else 0.0,
                win_rate=round(wins / len(episodes), 4),
                profit_factor=round(self._compute_profit_factor(episodes), 4),
            ))

        # Group by confidence band
        band_map: dict[str, list[TradeEpisode]] = {}
        for ep in settled:
            band = _confidence_band(ep.likelihood_confidence)
            band_map.setdefault(band, []).append(ep)

        for band, episodes in band_map.items():
            wins = sum(1 for e in episodes if e.execution_outcome == "WIN")
            tot_pnl = sum(float(e.pnl) for e in episodes)
            posteriors = [e.posterior_edge for e in episodes if e.posterior_edge > 0]
            report.by_confidence.append(ConfidenceBandStats(
                band=band,
                total_trades=len(episodes),
                wins=wins,
                losses=len(episodes) - wins,
                total_pnl=round(tot_pnl, 2),
                avg_posterior_edge=round(sum(posteriors) / len(posteriors), 4) if posteriors else 0.0,
                win_rate=round(wins / len(episodes), 4) if episodes else 0.0,
            ))

        # Group by outcome
        outcome_map: dict[str, list[TradeEpisode]] = {}
        for ep in settled:
            outcome_map.setdefault(ep.execution_outcome or "UNKNOWN", []).append(ep)

        for outcome, episodes in outcome_map.items():
            tot_pnl = sum(float(e.pnl) for e in episodes)
            confidences = [e.likelihood_confidence for e in episodes if e.likelihood_confidence > 0]
            likelihoods = [e.likelihood_score for e in episodes if e.likelihood_score > 0]
            posteriors = [e.posterior_edge for e in episodes if e.posterior_edge > 0]
            report.by_outcome.append(OutcomeStats(
                outcome=outcome,
                count=len(episodes),
                total_pnl=round(tot_pnl, 2),
                avg_confidence=round(sum(confidences) / len(confidences), 4) if confidences else 0.0,
                avg_likelihood=round(sum(likelihoods) / len(likelihoods), 4) if likelihoods else 0.0,
                avg_posterior_edge=round(sum(posteriors) / len(posteriors), 4) if posteriors else 0.0,
            ))

        # Overall stats
        total_wins = sum(1 for e in settled if e.execution_outcome == "WIN")
        report.overall_win_rate = round(total_wins / len(settled), 4) if settled else 0.0
        report.overall_pnl = round(sum(float(e.pnl) for e in settled), 2)

        return report

    @staticmethod
    def _compute_profit_factor(episodes: list[TradeEpisode]) -> float:
        gross_profit = sum(float(e.pnl) for e in episodes if e.pnl > Decimal("0"))
        gross_loss = abs(sum(float(e.pnl) for e in episodes if e.pnl < Decimal("0")))
        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 1.0
        return gross_profit / gross_loss


# ---------------------------------------------------------------------------
# Memory Index
# ---------------------------------------------------------------------------


class MemoryIndex:
    """Contextual memory index — context_hash → performance_stats.

    Every settled episode produces a context keyed by:
        context_hash = SHA256(regime + confidence_band + outcome)

    This enables the system to recall:
        "in this regime with this confidence, what was my historical win rate?"
    """

    def __init__(self, episode_store: TradeEpisodeStore) -> None:
        self._episode_store = episode_store
        self._index: dict[str, MemoryRecord] = {}

    def build(self) -> dict[str, MemoryRecord]:
        """Scan all settled episodes and build the memory index."""
        self._index = {}
        for ep in self._episode_store.all_settled:
            self._ingest(ep)
        return self._index

    def _ingest(self, episode: TradeEpisode) -> None:
        regime = episode.regime_at_entry or "UNKNOWN"
        band = _confidence_band(episode.likelihood_confidence)
        outcome = episode.execution_outcome or "UNKNOWN"
        context_key = self._context_key(regime, band, outcome)

        if context_key not in self._index:
            self._index[context_key] = MemoryRecord(
                context_hash=context_key,
                regime=regime,
                confidence_band=band,
                outcome=outcome,
            )

        record = self._index[context_key]
        record.total_trades += 1
        record.total_pnl += float(episode.pnl)
        record.avg_posterior_edge = (
            (record.avg_posterior_edge * (record.total_trades - 1) + episode.posterior_edge)
            / record.total_trades
        )
        record.avg_likelihood_score = (
            (record.avg_likelihood_score * (record.total_trades - 1) + episode.likelihood_score)
            / record.total_trades
        )
        record.avg_confidence = (
            (record.avg_confidence * (record.total_trades - 1) + episode.likelihood_confidence)
            / record.total_trades
        )

        if outcome == "WIN":
            record.wins += 1
        elif outcome == "LOSS":
            record.losses += 1

        record.recent_episodes.insert(0, episode.episode_id)
        if len(record.recent_episodes) > 10:
            record.recent_episodes.pop()

        record._last_pnl = float(episode.pnl)

    def query(
        self,
        regime: str | None = None,
        confidence_band: str | None = None,
        outcome: str | None = None,
        min_trades: int = 1,
    ) -> list[MemoryRecord]:
        """Query the memory index by context dimensions.

        Args:
            regime: Filter by regime (RANGING, TRENDING, VOLATILE).
            confidence_band: Filter by band name.
            outcome: Filter by outcome (WIN, LOSS, BREAK_EVEN).
            min_trades: Minimum trade count for a record to appear.

        Returns:
            Matching MemoryRecord list, sorted by win_rate descending.
        """
        if not self._index:
            return []

        results = []
        for record in self._index.values():
            if record.total_trades < min_trades:
                continue
            if regime and record.regime != regime:
                continue
            if confidence_band and record.confidence_band != confidence_band:
                continue
            if outcome and record.outcome != outcome:
                continue
            results.append(record)

        results.sort(key=lambda r: r.win_rate, reverse=True)
        return results

    def worst_contexts(self, top_n: int = 5) -> list[MemoryRecord]:
        """Return the contexts with lowest win rates."""
        if not self._index:
            return []
        sorted_records = sorted(
            [r for r in self._index.values() if r.total_trades >= 2],
            key=lambda r: r.win_rate,
        )
        return sorted_records[:top_n]

    def best_contexts(self, top_n: int = 5) -> list[MemoryRecord]:
        """Return the contexts with highest win rates."""
        if not self._index:
            return []
        sorted_records = sorted(
            [r for r in self._index.values() if r.total_trades >= 2],
            key=lambda r: r.win_rate,
            reverse=True,
        )
        return sorted_records[:top_n]

    @staticmethod
    def _context_key(regime: str, band: str, outcome: str) -> str:
        raw = f"{regime}|{band}|{outcome}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @property
    def size(self) -> int:
        return len(self._index)

    def to_dict(self) -> dict[str, dict[str, Any]]:
        if not self._index:
            self.build()
        return {
            k: {
                "context_hash": v.context_hash,
                "regime": v.regime,
                "confidence_band": v.confidence_band,
                "outcome": v.outcome,
                "total_trades": v.total_trades,
                "wins": v.wins,
                "losses": v.losses,
                "win_rate": v.win_rate,
                "total_pnl": round(v.total_pnl, 2),
            }
            for k, v in self._index.items()
        }
