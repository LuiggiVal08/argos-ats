"""MonitorPositionsUseCase — monitorea posiciones y gestiona SL/TP/BE/trail.

Pipeline:
  1. Cargar posiciones abiertas desde PositionRepository
  2. Para cada posición, obtener precio actual
  3. Evaluar via PositionManager (SL → TP → BE → trail → HOLD)
  4. Ejecutar acciones según decisión (cerrar, parcial, actualizar SL)
  5. Actualizar estado en repositorio
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Awaitable, Callable
from uuid import uuid4

import structlog

from ...domain.entities.position_manager import (
    PositionAction,
    PositionManager,
)
from ...domain.entities.position_tracker import PositionTracker, TrackerVerdict
from ...domain.value_objects.execution_report import ExecutionReport
from ...domain.value_objects.live_position import LivePosition
from ...domain.value_objects.order import OrderSide
from ..ports.execution_logger import ExecutionLogger

log = structlog.get_logger()
from ..ports.exchange_order_client import ExchangeOrderClient
from ..ports.position_repository import PositionRepository

PriceProvider = Callable[[str], Awaitable[Decimal]]


@dataclass(frozen=True)
class MonitorResult:
    closed: int = 0
    held: int = 0
    partial_closes: int = 0
    sl_updates: int = 0
    be_activations: int = 0
    trail_activations: int = 0
    details: list[dict] = field(default_factory=list)


class MonitorPositionsError(RuntimeError):
    """Raised when position monitoring fails."""


class MonitorPositionsUseCase:
    """Monitorea posiciones y ejecuta acciones inteligentes.

    Args:
        position_repo:    Repositorio de posiciones.
        exchange_client:  Cliente para cerrar posiciones a mercado.
        execution_logger: Logger de eventos.
        price_provider:   Callable async que retorna el precio actual
                          de un símbolo.
        position_manager: Domain entity for BE/trail/partial decisions.
        atr_provider:     Callable async que retorna el ATR actual.
    """

    def __init__(
        self,
        position_repo: PositionRepository,
        exchange_client: ExchangeOrderClient,
        execution_logger: ExecutionLogger,
        price_provider: PriceProvider,
        position_manager: PositionManager | None = None,
        atr_provider: Callable[[str], Awaitable[Decimal]] | None = None,
        algo_client: Any = None,
        order_cleanup: Any = None,
        trade_journal: Any = None,
    ) -> None:
        self._position_repo = position_repo
        self._exchange = exchange_client
        self._logger = execution_logger
        self._price_provider = price_provider
        self._position_manager = position_manager or PositionManager()
        self._atr_provider = atr_provider
        self._algo_client = algo_client
        self._order_cleanup = order_cleanup
        self._trade_journal = trade_journal

    async def run(self) -> MonitorResult:
        positions = await self._position_repo.list_open()
        if not positions:
            return MonitorResult()

        closed = 0
        held = 0
        partial_closes = 0
        sl_updates = 0
        be_activations = 0
        trail_activations = 0
        details: list[dict] = []

        for pos in positions:
            price = await self._price_provider(pos.symbol)

            atr: Decimal | None = None
            if self._atr_provider is not None:
                try:
                    atr = await self._atr_provider(pos.symbol)
                except Exception:
                    atr = None

            decision = self._position_manager.evaluate(pos, price, atr)

            if decision.action == PositionAction.HOLD:
                held += 1
                await self._logger.log_monitoring(
                    pos.position_id, pos.compute_pnl_at(price), price
                )
                details.append({
                    "position_id": pos.position_id,
                    "action": "HOLD",
                    "reason": decision.reason,
                })
                continue

            if decision.action == PositionAction.CLOSE:
                try:
                    await self._exchange.close_position(pos.symbol)
                except Exception as e:
                    raise MonitorPositionsError(
                        f"failed to close {pos.position_id}: {e}"
                    ) from e

                pnl = pos.compute_pnl_at(price)
                close_reason = decision.reason
                if "stop_loss" in close_reason:
                    close_status = "SL_HIT"
                elif "tp" in close_reason or "full_close" in close_reason:
                    close_status = "TP_HIT"
                else:
                    close_status = "CLOSED"
                closed_pos = LivePosition(
                    position_id=pos.position_id,
                    symbol=pos.symbol,
                    side=pos.side,
                    units=pos.units,
                    entry_price=pos.entry_price,
                    current_price=price,
                    sl_price=pos.sl_price,
                    tp_price=pos.tp_price,
                    opened_at=pos.opened_at,
                    closed_at=datetime.now(timezone.utc),
                    realized_pnl=pnl,
                    status=close_status,
                )
                await self._position_repo.save(closed_pos)

                report = ExecutionReport(
                    report_id=uuid4().hex[:12],
                    signal_id="",
                    symbol=pos.symbol,
                    side=pos.side,
                    status="FILLED",
                    filled_qty=pos.units,
                    avg_price=price,
                    pnl=pnl,
                    position_id=pos.position_id,
                )
                await self._logger.log_execution(report)
                closed += 1
                details.append({
                    "position_id": pos.position_id,
                    "action": "CLOSE",
                    "reason": decision.reason,
                    "pnl": str(pnl),
                })
                continue

            if decision.action == PositionAction.PARTIAL_CLOSE:
                try:
                    close_units = (pos.units * decision.close_pct).quantize(
                        Decimal("0.00000001")
                    )
                    await self._exchange.close_partial(
                        pos.symbol, close_units
                    )
                except Exception as e:
                    raise MonitorPositionsError(
                        f"failed partial close {pos.position_id}: {e}"
                    ) from e

                remaining = pos.units - close_units
                pnl = pos.compute_pnl_at(price) * decision.close_pct
                updated_pos = LivePosition(
                    position_id=pos.position_id,
                    symbol=pos.symbol,
                    side=pos.side,
                    units=remaining,
                    entry_price=pos.entry_price,
                    current_price=price,
                    sl_price=pos.sl_price,
                    tp_price=pos.tp_price,
                    tp2_price=pos.tp2_price,
                    tp3_price=pos.tp3_price,
                    trail_activated=pos.trail_activated,
                    trail_offset=pos.trail_offset,
                    break_even_activated=pos.break_even_activated,
                    atr_at_entry=pos.atr_at_entry,
                    initial_units=pos.initial_units,
                    tp1_pct=pos.tp1_pct,
                    tp2_pct=pos.tp2_pct,
                    opened_at=pos.opened_at,
                    status="PARTIALLY_CLOSED" if remaining > 0 else "CLOSED",
                )
                await self._position_repo.save(updated_pos)

                if remaining <= 0:
                    report = ExecutionReport(
                        report_id=uuid4().hex[:12],
                        signal_id="",
                        symbol=pos.symbol,
                        side=pos.side,
                        status="FILLED",
                        filled_qty=pos.units,
                        avg_price=price,
                        pnl=pnl,
                        position_id=pos.position_id,
                    )
                    await self._logger.log_execution(report)

                partial_closes += 1
                details.append({
                    "position_id": pos.position_id,
                    "action": "PARTIAL_CLOSE",
                    "close_pct": str(decision.close_pct),
                    "tp_level": decision.tp_level,
                    "reason": decision.reason,
                })
                continue

            if decision.action in (
                PositionAction.ACTIVATE_BREAK_EVEN,
                PositionAction.UPDATE_SL,
                PositionAction.ACTIVATE_TRAIL,
            ):
                is_be = decision.action == PositionAction.ACTIVATE_BREAK_EVEN
                is_trail = decision.action == PositionAction.ACTIVATE_TRAIL
                new_sl = decision.new_sl_price

                sl_side = (
                    OrderSide.SELL if pos.side is OrderSide.BUY else OrderSide.BUY
                )

                # Place new SL FIRST, then cancel old only if new succeeds.
                # CRITICAL: never cancel old SL before new SL is confirmed.
                new_sl_order_id: str | None = None
                if new_sl is not None:
                    try:
                        sl_result = await self._exchange.place_stop_loss_order(
                            symbol=pos.symbol,
                            side=sl_side,
                            amount=pos.units,
                            stop_price=new_sl,
                        )
                        new_sl_order_id = sl_result.id
                    except Exception as e:
                        log.critical(
                            "sl_update_failed_new_sl_not_placed",
                            position_id=pos.position_id,
                            new_sl=str(new_sl),
                            old_sl_order_id=pos.sl_order_id,
                            error=str(e),
                        )
                        # Keep old SL intact — do NOT cancel it.
                        # Skip this update entirely; next monitor run will retry.
                        continue

                # New SL confirmed on exchange — safe to cancel old.
                if pos.sl_order_id:
                    try:
                        await self._exchange.cancel_order(
                            pos.sl_order_id, pos.symbol
                        )
                    except Exception as e:
                        log.warning(
                            "cancel_old_sl_failed",
                            position_id=pos.position_id,
                            sl_order_id=pos.sl_order_id,
                            error=str(e),
                        )

                updated_pos = LivePosition(
                    position_id=pos.position_id,
                    symbol=pos.symbol,
                    side=pos.side,
                    units=pos.units,
                    entry_price=pos.entry_price,
                    current_price=price,
                    sl_price=new_sl or pos.sl_price,
                    tp_price=pos.tp_price,
                    tp2_price=pos.tp2_price,
                    tp3_price=pos.tp3_price,
                    trail_activated=is_trail or pos.trail_activated,
                    trail_offset=(
                        atr * Decimal("1.5") if atr else pos.atr_at_entry
                    ) if is_trail and pos.trail_offset is None else pos.trail_offset,
                    break_even_activated=is_be or pos.break_even_activated,
                    atr_at_entry=pos.atr_at_entry,
                    initial_units=pos.initial_units,
                    tp1_pct=pos.tp1_pct,
                    tp2_pct=pos.tp2_pct,
                    opened_at=pos.opened_at,
                    status="OPEN",
                    sl_order_id=new_sl_order_id or pos.sl_order_id,
                    tp_order_id=pos.tp_order_id,
                )
                await self._position_repo.save(updated_pos)

                if is_be:
                    be_activations += 1
                elif is_trail:
                    trail_activations += 1
                else:
                    sl_updates += 1

                details.append({
                    "position_id": pos.position_id,
                    "action": decision.action.value,
                    "new_sl": str(new_sl) if new_sl else None,
                    "reason": decision.reason,
                    "sl_order_id": new_sl_order_id,
                })

        return MonitorResult(
            closed=closed,
            held=held,
            partial_closes=partial_closes,
            sl_updates=sl_updates,
            be_activations=be_activations,
            trail_activations=trail_activations,
            details=details,
        )
