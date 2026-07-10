"""CCLConsistency — validation and trace reconstruction for the CCL pipeline.

FASE 1.2 — Consistency checks:
  - verify_posterior_coverage(): every executed trade has posterior
  - verify_episode_id_chain(): episode_id flows through events
  - verify_signal_likelihood(): every signal has likelihood

FASE 1.3 — Replay validation:
  - replay_event_chain(trade_id): reconstruct tick → signal → execution → outcome → posterior
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

import structlog

from ..event_sourcing import EventRecorder
from .trade_episode import TradeEpisode, TradeEpisodeStore

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@dataclass
class ConsistencyReport:
    """Report of CCL consistency checks."""
    total_episodes: int = 0
    total_settled: int = 0
    missing_posterior: list[str] = field(default_factory=list)
    missing_likelihood: list[str] = field(default_factory=list)
    missing_outcome: list[str] = field(default_factory=list)
    broken_chains: list[dict[str, Any]] = field(default_factory=list)
    passes: int = 0
    failures: int = 0

    @property
    def posterior_coverage_pct(self) -> float:
        if self.total_episodes == 0:
            return 100.0
        covered = self.total_settled - len(self.missing_posterior)
        return round(covered / self.total_episodes * 100, 1)

    @property
    def all_ok(self) -> bool:
        return (
            not self.missing_posterior
            and not self.missing_likelihood
            and not self.missing_outcome
            and not self.broken_chains
        )


@dataclass
class EventChain:
    """Reconstructed event chain for a single trade."""
    trade_id: str = ""
    episode_id: str = ""
    tick_events: list[dict[str, Any]] = field(default_factory=list)
    inference_event: dict[str, Any] | None = None
    signal_event: dict[str, Any] | None = None
    execution_event: dict[str, Any] | None = None
    prior_event: dict[str, Any] | None = None
    posterior_event: dict[str, Any] | None = None
    model_comparison_event: dict[str, Any] | None = None
    chain_complete: bool = False
    chain_break_reason: str = ""
    missing_links: list[str] = field(default_factory=list)
    total_events: int = 0


# ---------------------------------------------------------------------------
# CCL Consistency
# ---------------------------------------------------------------------------


class CCLConsistency:
    """Validates CCL event coverage and chain integrity.

    Reads from event_store and episode_store to verify that every
    executed trade has a complete CCL trace.
    """

    def __init__(
        self,
        event_store: Any,
        episode_store: TradeEpisodeStore,
    ) -> None:
        self._event_store = event_store
        self._episode_store = episode_store

    async def run_all(
        self,
        *,
        log_report: bool = True,
    ) -> ConsistencyReport:
        """Run all consistency checks and return a report."""
        report = ConsistencyReport()

        # 1. Check episode posterior coverage
        report.total_episodes = self._episode_store.count
        report.total_settled = self._episode_store.count_settled
        settled = self._episode_store.all_settled
        for ep in settled:
            if ep.posterior_edge <= 0.0 and ep.updated_trade_belief == "":
                report.missing_posterior.append(ep.episode_id)
            if ep.likelihood_score <= 0.0:
                report.missing_likelihood.append(ep.episode_id)
            if ep.execution_outcome == "":
                report.missing_outcome.append(ep.episode_id)

        # 2. Check event chain integrity
        for ep in settled:
            chain = await self.replay_event_chain(
                episode_id=ep.episode_id,
                trade_id=ep.episode_id,
            )
            if not chain.chain_complete:
                report.broken_chains.append({
                    "episode_id": ep.episode_id,
                    "reason": chain.chain_break_reason,
                    "missing": chain.missing_links,
                    "total_events": chain.total_events,
                })

        report.passes = report.total_settled - len(report.missing_posterior)
        report.failures = (
            len(report.missing_posterior)
            + len(report.broken_chains)
        )

        if log_report:
            self._log_report(report)

        return report

    async def replay_event_chain(
        self,
        episode_id: str | None = None,
        trade_id: str | None = None,
        signal_id: str | None = None,
    ) -> EventChain:
        """Reconstruct the full event chain for a single trade.

        Searches the event store for all events related to a given
        episode_id, trade_id, or signal_id and reconstructs:
            tick → inference → prior → signal → execution → posterior

        At least one of episode_id, trade_id, or signal_id must be provided.
        """
        lookup = episode_id or trade_id or signal_id
        if not lookup:
            raise ValueError("one of episode_id, trade_id, or signal_id required")

        chain = EventChain(
            trade_id=trade_id or lookup,
            episode_id=episode_id or lookup,
        )

        events: list[Any] = []
        async for ev in self._event_store.stream_by_index_range(0):
            matched = False
            ed = ev.data

            if episode_id and isinstance(ed, dict):
                if ed.get("episode_id") == episode_id:
                    matched = True
            if trade_id and isinstance(ed, dict):
                if ed.get("trade_id") == trade_id or ed.get("position_id") == trade_id:
                    matched = True
            if signal_id and isinstance(ed, dict):
                if ed.get("signal_id") == signal_id:
                    matched = True

            if matched:
                events.append(ev)

        chain.total_events = len(events)

        # Classify events into pipeline stages
        for ev in sorted(events, key=lambda e: e.timestamp):
            ed = ev.data if isinstance(ev.data, dict) else {}

            if ev.event_type == "ccl.prior_computed":
                chain.prior_event = ed
            elif ev.event_type == "inference.completed":
                chain.inference_event = ed
            elif ev.event_type == "signal.generated":
                chain.signal_event = ed
            elif ev.event_type == "trade.executed":
                chain.execution_event = ed
            elif ev.event_type == "ccl.episode_settled":
                chain.posterior_event = ed
            elif ev.event_type == "ccl.model_comparison":
                chain.model_comparison_event = ed
            elif ev.event_type.startswith("tick."):
                chain.tick_events.append(ed)

        # Determine completeness
        required = ["inference.completed", "signal.generated", "trade.executed"]
        present = {
            "inference.completed": chain.inference_event is not None,
            "signal.generated": chain.signal_event is not None,
            "trade.executed": chain.execution_event is not None,
        }
        chain.missing_links = [k for k, v in present.items() if not v]

        if chain.posterior_event and chain.signal_event and chain.execution_event:
            chain.chain_complete = True
        elif chain.missing_links:
            chain.chain_complete = False
            chain.chain_break_reason = (
                f"missing events: {', '.join(chain.missing_links)}"
            )
        else:
            chain.chain_complete = False
            chain.chain_break_reason = (
                "trade still open — posterior not yet computed"
            )

        return chain

    async def verify_posterior_coverage(self) -> dict[str, Any]:
        """Verify every executed trade has a posterior."""
        settled = self._episode_store.all_settled
        total = len(settled)
        missing = [
            ep.episode_id for ep in settled
            if ep.posterior_edge <= 0.0 and ep.updated_trade_belief == ""
        ]
        covered = total - len(missing)
        pct = round(covered / total * 100, 1) if total > 0 else 100.0
        return {
            "total_settled": total,
            "posterior_covered": covered,
            "posterior_coverage_pct": pct,
            "missing_episodes": missing,
            "all_covered": len(missing) == 0,
        }

    def _log_report(self, report: ConsistencyReport) -> None:
        log.info(
            "ccl:consistency_report",
            total_episodes=report.total_episodes,
            total_settled=report.total_settled,
            posterior_coverage_pct=report.posterior_coverage_pct,
            missing_posterior=len(report.missing_posterior),
            missing_likelihood=len(report.missing_likelihood),
            missing_outcome=len(report.missing_outcome),
            broken_chains=len(report.broken_chains),
            passes=report.passes,
            failures=report.failures,
            all_ok=report.all_ok,
        )
        if report.missing_posterior:
            for eid in report.missing_posterior[:5]:
                log.warning("ccl:missing_posterior", episode_id=eid)
        if report.broken_chains:
            for bc in report.broken_chains:
                log.warning(
                    "ccl:broken_chain",
                    episode_id=bc["episode_id"],
                    reason=bc["reason"],
                )
