"""CheckDrawdownUseCase.

Reads the day's starting balance from the snapshot repo, sums
the realised P&L from the trade journal since the snapshot was
taken, and asks the pure-domain `CircuitBreaker` to evaluate.

Sad path handling (per spec section 5 Historia 3):
  - SnapshotRepoError: the day hasn't been opened yet. We raise
    `CheckDrawdownError("snapshot_missing")` — the caller is
    expected to call `OpenDayUseCase` first.
  - TradeJournalError: the journal can't be read. We raise
    `CheckDrawdownError("journal_unavailable")` — the caller
    halts the trading loop until the journal is back.
  - TRIP verdict: the use case dispatches `TripCircuitBreakerUseCase`
    and returns a result with `state=HALTED` and the trip detail.

The result is a `CheckDrawdownResult` carrying the snapshot, the
intraday P&L sum, the verdict, and the timestamp of the check.
The caller can use this to drive the trading loop (continue,
warn, halt) without re-deriving anything.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Awaitable, Callable, Optional

from ...domain.entities.circuit_breaker import CircuitBreaker
from ...domain.value_objects.drawdown_snapshot import DrawdownSnapshot
from ...domain.value_objects.drawdown_state import DrawdownState
from ...domain.value_objects.trip_action import TripAction
from ..ports.drawdown_snapshot_repo import (
    DrawdownSnapshotRepo,
    DrawdownSnapshotRepoError,
)
from ..ports.environment_mode_writer import (
    EnvironmentMode,
    EnvironmentModeWriter,
    EnvironmentModeError,
)
from ..ports.exchange_order_client import (
    ExchangeOrderClient,
    ExchangeOrderClientError,
)
from ..ports.trade_journal import TradeJournal, TradeJournalError


class CheckDrawdownError(RuntimeError):
    """Raised when a required input cannot be obtained (snapshot
    missing, journal unreachable). The caller should halt the
    trading loop until the error is resolved."""


@dataclass(frozen=True)
class CheckDrawdownResult:
    snapshot: DrawdownSnapshot
    state: DrawdownState
    trip_action: TripAction | None
    intraday_pnl: Decimal
    checked_at: datetime


class CheckDrawdownUseCase:
    def __init__(
        self,
        circuit_breaker: CircuitBreaker,
        snapshot_repo: DrawdownSnapshotRepo,
        trade_journal: TradeJournal,
        on_trip: Optional[
            Callable[[CheckDrawdownResult], Awaitable[None]]
        ] = None,
    ) -> None:
        self._cb = circuit_breaker
        self._snapshots = snapshot_repo
        self._journal = trade_journal
        self._on_trip = on_trip

    async def load_current_snapshot(self) -> DrawdownSnapshot | None:
        """Return the persisted snapshot without evaluating it.
        Used by the GET /risk/drawdown endpoint, which must NOT
        trigger a trip or dispatch any action. Raises
        `CheckDrawdownError` on repo failure."""
        try:
            return await self._snapshots.load()
        except DrawdownSnapshotRepoError as e:
            raise CheckDrawdownError(f"snapshot_unavailable: {e}") from e

    async def is_halted(self) -> bool:
        """Check whether the circuit breaker would trip right now.

        Non-destructive — does NOT trigger a trip or dispatch any
        action. Used by health checks and position monitor loops."""
        try:
            snapshot = await self._snapshots.load()
            if snapshot is None:
                return False
            intraday_pnl = await self._journal.realized_pnl_since(snapshot.taken_at)
            fresh = DrawdownSnapshot(
                starting_balance=snapshot.starting_balance,
                current_balance=snapshot.starting_balance + intraday_pnl,
                taken_at=datetime.now(tz=timezone.utc),
            )
            state = self._cb.evaluate(fresh)
            return state in (DrawdownState.TRIP, DrawdownState.HALTED)
        except Exception:
            return False

    async def execute(
        self, current_state: DrawdownState | None = None
    ) -> CheckDrawdownResult:
        # 1. Load today's snapshot.
        try:
            snapshot = await self._snapshots.load()
        except DrawdownSnapshotRepoError as e:
            raise CheckDrawdownError(f"snapshot_unavailable: {e}") from e
        if snapshot is None:
            raise CheckDrawdownError(
                "snapshot_missing: call OpenDayUseCase first"
            )

        # 2. Sum the day's realised P&L.
        try:
            intraday_pnl = await self._journal.realized_pnl_since(
                snapshot.taken_at
            )
        except TradeJournalError as e:
            raise CheckDrawdownError(f"journal_unavailable: {e}") from e

        # 3. Build the fresh snapshot (current_balance =
        # starting + pnl).
        fresh = DrawdownSnapshot(
            starting_balance=snapshot.starting_balance,
            current_balance=snapshot.starting_balance + intraday_pnl,
            taken_at=datetime.now(tz=timezone.utc),
        )

        # 4. Evaluate.
        state = self._cb.evaluate(fresh, current_state)
        trip_action: TripAction | None = (
            self._cb.trip_action() if state is DrawdownState.TRIP else None
        )

        result = CheckDrawdownResult(
            snapshot=fresh,
            state=state,
            trip_action=trip_action,
            intraday_pnl=intraday_pnl,
            checked_at=datetime.now(tz=timezone.utc),
        )

        # 5. If TRIP, dispatch the trip handler (the use case's
        # default is to call TripCircuitBreakerUseCase; composition
        # root wires it).
        if state is DrawdownState.TRIP and self._on_trip is not None:
            await self._on_trip(result)

        return result
