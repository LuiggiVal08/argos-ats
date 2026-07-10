"""OrderCleanupService — lifecycle cleanup for stale algo/conditional orders.

Ensures that the exchange never holds STOP_MARKET/TAKE_PROFIT orders that
are not linked to an active LivePosition. Runs AFTER every position state
change (merge, create, close, partial close, SL/TP update).

This addresses the "order lifecycle ownership gap": the exchange can hold
conditional orders that the local system has lost track of (e.g. old SL
after merge, orphan orders from a prior lifecycle, etc).

Design:
- Purely additive: no domain changes, no event sourcing, no refactoring.
- Idempotent: will only cancel orders that are provably stale.
- Deterministic: same inputs → same results.
"""
from __future__ import annotations

import structlog

from ...domain.value_objects.live_position import LivePosition
from ..ports.algo_order_client import AlgoOrderClient
from ..ports.position_repository import PositionRepository

log = structlog.get_logger()


class OrderCleanupService:
    """Lifecycle cleanup for stale algo/conditional orders.

    Args:
        algo_client: Client for Binance Futures Algo API.
        position_repo: Repository for local LivePositions.
    """

    __slots__ = ("_algo_client", "_position_repo")

    def __init__(
        self,
        algo_client: AlgoOrderClient,
        position_repo: PositionRepository,
    ) -> None:
        self._algo_client = algo_client
        self._position_repo = position_repo

    async def cleanup_stale_algo_orders(self, symbol: str) -> None:
        """Fetch all open algo orders for *symbol* and cancel any that are stale.

        Decision logic (strict):
          - If the symbol has NO open position locally → cancel ALL algo orders.
          - If the symbol HAS an open position → cancel any algo order whose ID
            does NOT match the current LivePosition.sl_order_id or .tp_order_id.
          - If the order IS referenced → keep (skip).

        Safety:
          - NEVER cancels an order actively referenced by the current LivePosition.
          - NEVER cancels if position is OPEN and order matches sl_order_id/tp_order_id.
          - ALWAYS logs every cancellation with reason.
        """
        open_orders: list[dict] = []
        try:
            open_orders = await self._algo_client.list_open_algo_orders(symbol)
        except Exception as exc:
            log.warning(
                "cleanup_fetch_failed",
                symbol=symbol,
                error=str(exc),
            )
            return

        if not open_orders:
            return

        current_position: LivePosition | None = None
        try:
            for pos in await self._position_repo.list_open():
                if pos.symbol == symbol:
                    current_position = pos
                    break
        except Exception as exc:
            log.warning(
                "cleanup_position_fetch_failed",
                symbol=symbol,
                error=str(exc),
            )
            return

        active_ids: set[str] = set()
        position_id: str | None = None
        if current_position is not None:
            position_id = current_position.position_id
            if current_position.sl_order_id:
                active_ids.add(current_position.sl_order_id)
            if current_position.tp_order_id:
                active_ids.add(current_position.tp_order_id)

        for order in open_orders:
            algo_id = order.get("algoId") or order.get("algoId", "")
            if not algo_id:
                continue

            if algo_id in active_ids:
                log.info(
                    "cleanup_skipped_order_active",
                    algo_id=algo_id,
                    symbol=symbol,
                    current_position_id=position_id,
                )
                continue

            if current_position is None:
                reason = "position_flat"
            else:
                reason = "order_orphaned"

            log.info(
                "stale_algo_order_detected",
                algo_id=algo_id,
                symbol=symbol,
                reason=reason,
                current_position_id=position_id,
            )

            try:
                cancelled = await self._algo_client.cancel_algo_order(algo_id)
                if cancelled:
                    log.info(
                        "algo_order_cancelled",
                        algo_id=algo_id,
                        symbol=symbol,
                        reason=reason,
                        current_position_id=position_id,
                    )
                else:
                    log.info(
                        "algo_order_already_gone",
                        algo_id=algo_id,
                        symbol=symbol,
                        reason=reason,
                        current_position_id=position_id,
                    )
            except Exception as exc:
                log.critical(
                    "algo_order_cancel_failed",
                    algo_id=algo_id,
                    symbol=symbol,
                    reason=reason,
                    error=str(exc),
                    current_position_id=position_id,
                )
