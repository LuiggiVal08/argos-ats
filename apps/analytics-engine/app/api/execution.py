"""Execution API: orquestación de señales en vivo.

POST /execute/signal  -> ejecuta una señal manualmente
GET  /position/list   -> lista posiciones abiertas
GET  /execution/log   -> eventos de ejecución recientes

Sad paths (422):
  - Señal rechazada (confidence, cooldown, duplicated, expired)
  - Circuit Breaker HALTED
  - Error de mercado (balance/ATR)
  - Error de colocación de orden
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..application.use_cases.execute_signal import (
    ExecuteSignalError,
    ExecuteSignalUseCase,
)
from ..application.use_cases.monitor_positions import (
    MonitorPositionsUseCase,
)
from ..composition import (
    get_execute_signal_usecase,
    get_monitor_positions_usecase,
    get_position_repo,
)
from ..domain.value_objects.execution_signal import ExecutionSignal
from ..domain.value_objects.signal_side import SignalSide

router = APIRouter(prefix="/execute", tags=["execution"])


@router.post(
    "/signal",
    summary="Execute a trading signal manually.",
)
async def execute_signal(
    request: Request,
    side: str = "BUY",
    confidence: float = 0.85,
    symbol: str = "BTC/USDT",
    strategy_id: str = "manual",
    price: float | None = None,
) -> dict:
    """Ejecuta una señal de trading manualmente.

    Args:
        request: FastAPI request (para DI).
        side: Dirección (BUY/SELL).
        confidence: Confianza de la señal (0..1, default 0.85).
        symbol: Par de trading (default: BTC/USDT).
        strategy_id: Estrategia origen (default: manual).
        price: Precio estimado de entrada (opcional).

    Returns:
        ExecutionReport en formato dict.

    Raises:
        422: Si la señal es rechazada o la ejecución falla.
    """
    try:
        signal_side = SignalSide(side.upper())
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid side: {side}")

    if not 0.0 <= confidence <= 1.0:
        raise HTTPException(
            status_code=422,
            detail=f"confidence must be in [0, 1], got {confidence}",
        )

    signal = ExecutionSignal(
        side=signal_side,
        confidence=confidence,
        symbol=symbol,
        strategy_id=strategy_id,
        price=Decimal(str(price)) if price is not None else None,
    )

    use_case = get_execute_signal_usecase(request)
    try:
        result = await use_case.execute(signal)
    except ExecuteSignalError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if not hasattr(result, "report"):
        reason = getattr(result, "reason", "gate_blocked")
        raise HTTPException(status_code=425, detail=reason)

    return _report_to_dict(result.report)


@router.get(
    "/position/list",
    summary="List open positions.",
)
async def list_positions(
    request: Request,
    all: bool = False,
) -> list[dict]:
    """Lista posiciones abiertas (o todas si all=true).

    Args:
        request: FastAPI request (para DI).
        all: Si True, lista todas las posiciones.

    Returns:
        Lista de posiciones en formato dict.
    """
    repo = get_position_repo(request)
    if all:
        positions = await repo.list_all()
    else:
        positions = await repo.list_open()
    return [_position_to_dict(p) for p in positions]


@router.get(
    "/execution/log",
    summary="Recent execution log entries.",
)
async def execution_log(
    request: Request,
    limit: int = 20,
) -> list[dict]:
    """Retorna los eventos de ejecución más recientes.

    Args:
        request: FastAPI request (para DI).
        limit: Máximo de entradas (default 20).

    Returns:
        Lista de eventos en formato dict.
    """
    use_case = get_execute_signal_usecase(request)
    logger = use_case._logger
    entries = await logger.recent(limit=limit)
    return entries


def _report_to_dict(report: Any) -> dict:
    return {
        "report_id": report.report_id,
        "signal_id": report.signal_id,
        "symbol": report.symbol,
        "side": report.side.value,
        "status": report.status,
        "filled_qty": float(report.filled_qty) if report.filled_qty else 0,
        "avg_price": float(report.avg_price) if report.avg_price else None,
        "pnl": float(report.pnl) if report.pnl else None,
        "order_id": report.order_id,
        "position_id": report.position_id,
        "errors": report.errors,
        "timestamp": report.timestamp.isoformat(),
    }


def _position_to_dict(pos: Any) -> dict:
    return {
        "position_id": pos.position_id,
        "symbol": pos.symbol,
        "side": pos.side.value,
        "units": float(pos.units),
        "entry_price": float(pos.entry_price),
        "current_price": float(pos.current_price),
        "sl_price": float(pos.sl_price) if pos.sl_price else None,
        "tp_price": float(pos.tp_price) if pos.tp_price else None,
        "unrealized_pnl": float(pos.unrealized_pnl),
        "opened_at": pos.opened_at.isoformat(),
        "closed_at": pos.closed_at.isoformat() if pos.closed_at else None,
        "realized_pnl": float(pos.realized_pnl) if pos.realized_pnl else None,
        "status": pos.status,
    }
