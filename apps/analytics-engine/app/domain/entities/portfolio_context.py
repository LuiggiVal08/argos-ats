from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ..value_objects.portfolio_context_snapshot import (
    PortfolioContextSnapshot,
    SignalCluster,
    TradeRecord,
)


@dataclass(frozen=True)
class PortfolioContextDecision:
    cluster_id: str | None = None
    cluster_size: int = 0
    cooldown_remaining_s: int = 0
    size_multiplier: float = 1.0
    block_reason: str | None = None


CLUSTER_WINDOW_S = 21_600
COOLDOWN_S = 10_800
MAX_CLUSTER_SIZE = 3
REGIME_MULTIPLIERS: dict[str, float] = {
    "TRENDING": 0.25,
    "RANGING": 1.0,
}


class PortfolioContextStore(Protocol):
    async def load_context(self, symbol: str) -> PortfolioContextSnapshot: ...
    async def record_signal(
        self, symbol: str, direction: str, timestamp: float
    ) -> None: ...
    async def save_trade_record(
        self, symbol: str, record: TradeRecord
    ) -> None: ...
    async def clear_cluster(self, symbol: str) -> None: ...


def _cluster_id_for(direction: str, now: float) -> str:
    ts_str = datetime.utcfromtimestamp(now).strftime("%Y-%m-%dT%H")
    return f"{direction}_{ts_str}"


class PortfolioContext:
    """Evaluates portfolio-wide context for incoming signals.

    Pure domain: no I/O. Receives state via snapshot and returns decisions.
    Caller is responsible for calling record_signal_event for every signal
    and record_trade_outcome when trades resolve.
    """

    def __init__(self, store: PortfolioContextStore) -> None:
        self._store = store

    async def evaluate(
        self,
        symbol: str,
        direction: str,
        regime: str,
        now: float | None = None,
    ) -> PortfolioContextDecision:
        if now is None:
            now = time.time()
        snapshot = await self._store.load_context(symbol)

        cluster_id: str | None = None
        cluster_size = 0
        block_reason: str | None = None
        cooldown_remaining = 0

        # 1. Cluster check: count ALL same-direction signals in window
        if snapshot.active_cluster is not None:
            age = now - snapshot.active_cluster.last_timestamp
            if snapshot.active_cluster.direction == direction and age < CLUSTER_WINDOW_S:
                cluster_id = snapshot.active_cluster.cluster_id
                cluster_size = snapshot.active_cluster.signal_count + 1
                if cluster_size > MAX_CLUSTER_SIZE:
                    block_reason = "cluster_limit"

        # 2. Cooldown after a trade loss
        if (
            snapshot.last_trade is not None
            and snapshot.last_trade.direction == direction
            and snapshot.last_trade.outcome == "SL"
        ):
            elapsed = now - snapshot.last_trade.timestamp
            if elapsed < COOLDOWN_S:
                cooldown_remaining = int(COOLDOWN_S - elapsed)
                block_reason = block_reason or "cooldown_after_loss"

        # 3. Rate limit: too many same-direction signals in window
        if block_reason is None and snapshot.same_direction_count_6h >= MAX_CLUSTER_SIZE:
            elapsed = now - snapshot.last_same_direction_ts
            if elapsed < CLUSTER_WINDOW_S:
                block_reason = "same_direction_rate_limit"

        size_multiplier = REGIME_MULTIPLIERS.get(regime, 1.0)

        return PortfolioContextDecision(
            cluster_id=cluster_id,
            cluster_size=cluster_size or 1,
            cooldown_remaining_s=cooldown_remaining,
            size_multiplier=size_multiplier,
            block_reason=block_reason,
        )

    async def record_signal_event(
        self,
        symbol: str,
        direction: str,
        now: float | None = None,
    ) -> None:
        """Record a signal event for cluster tracking. Always called for
        every signal that reaches Portfolio Context, regardless of whether
        the signal was blocked or executed.
        """
        if now is None:
            now = time.time()
        snapshot = await self._store.load_context(symbol)

        inherit_cluster = (
            snapshot.active_cluster is not None
            and snapshot.active_cluster.direction == direction
            and (now - snapshot.active_cluster.last_timestamp) < CLUSTER_WINDOW_S
        )
        if inherit_cluster:
            c = snapshot.active_cluster
            cluster_id = c.cluster_id
            first_ts = c.first_timestamp
            count = c.signal_count + 1
        else:
            cluster_id = _cluster_id_for(direction, now)
            first_ts = now
            count = 1

        cluster = SignalCluster(
            cluster_id=cluster_id,
            direction=direction,
            first_timestamp=first_ts,
            last_timestamp=now,
            signal_count=count,
        )
        await self._store.record_signal(symbol, direction, now)

    async def record_trade_outcome(
        self,
        symbol: str,
        direction: str,
        outcome: str,
        regime: str,
        confidence: float,
        timestamp: float,
    ) -> None:
        """Record a trade outcome for cooldown tracking."""
        await self._store.save_trade_record(
            symbol,
            TradeRecord(
                direction=direction,
                outcome=outcome,
                regime=regime,
                timestamp=timestamp,
                confidence=confidence,
            ),
        )
        if outcome == "TP":
            await self._store.clear_cluster(symbol)
