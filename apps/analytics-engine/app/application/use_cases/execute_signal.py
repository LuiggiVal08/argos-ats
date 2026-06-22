"""ExecuteSignalUseCase — valida y ejecuta una señal de trading.

Pipeline:
  1. Validar señal (confidence, cooldown, dedup)
  2. Verificar Circuit Breaker (no HALTED)
  3. Obtener balance libre + ATR
  4. Calcular tamaño de posición
  5. Armar orden compuesta (entry + SL + TP)
  6. Verificar idempotencia (Fix 2: no duplicados)
  7. Colocar orden vía ExchangeOrderClient
  8. Actualizar idempotency store con exchange_order_id
  9. Persistir posición (con retry)
  10. Flush snapshot (con retry)
  11. Loggear ejecución
  12. Retornar ExecutionReport
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable
from uuid import uuid4

import structlog

from ...domain.entities.signal_validator import SignalValidator
from ...domain.value_objects.event_id import uuid7
from ...domain.value_objects.execution_report import ExecutionReport
from ...domain.value_objects.execution_signal import ExecutionSignal
from ...domain.value_objects.live_position import LivePosition
from ...domain.value_objects.order import CompositeOrder, OrderSide
from ...domain.value_objects.symbol import Symbol
from ..ports.atr_calculator import AtrCalculator
from ..ports.balance_provider import BalanceProvider
from ..ports.execution_idempotency import ExecutionIdempotencyStore
from ..ports.exchange_order_client import (
    ExchangeOrderClient,
    SlPlacementError,
)
from ..ports.execution_logger import ExecutionLogger
from ..ports.position_repository import PositionRepository
from ..ports.snapshot_repository import (
    SnapshotRepository,
    SystemSnapshot,
)

IsHaltedFn = Callable[[], Awaitable[bool]]

MAX_RETRIES = 3
RETRY_DELAY_S = 0.2


log = structlog.get_logger()


class ExecuteSignalError(RuntimeError):
    """Raised when signal execution fails irrecoverably."""


@dataclass(frozen=True)
class ExecuteSignalResult:
    report: ExecutionReport
    position: LivePosition | None = None
    event_id: str = ""
    lineage_id: str = ""


class ExecuteSignalUseCase:
    """Orquesta la ejecución completa de una señal.

    Args:
        signal_validator:    Valida la señal entrante.
        balance_provider:    Provee el balance libre disponible.
        atr_calculator:      Calcula ATR para SL dinámico.
        exchange_client:     Coloca órdenes en el exchange.
        position_repo:       Persiste posiciones.
        execution_logger:    Registra eventos de ejecución.
        is_halted:           Callable que retorna True si el CB está HALTED.
        idempotency_store:   Store para dedup de ejecuciones (Fix 2).
        snapshot_repo:       System snapshot store (Fix 3: flush post-exec).
        risk_pct:            Fracción del balance a arriesgar (default 0.01).
        sl_atr_multiplier:   Multiplicador de ATR para SL (default 1.5).
        tp_atr_multiplier:   Multiplicador de ATR para TP (default 3.0).
    """

    def __init__(
        self,
        signal_validator: SignalValidator,
        balance_provider: BalanceProvider,
        atr_calculator: AtrCalculator,
        exchange_client: ExchangeOrderClient,
        position_repo: PositionRepository,
        execution_logger: ExecutionLogger,
        is_halted: IsHaltedFn,
        idempotency_store: ExecutionIdempotencyStore | None = None,
        snapshot_repo: SnapshotRepository | None = None,
        risk_pct: float = 0.01,
        sl_atr_multiplier: float = 1.5,
        tp_atr_multiplier: float = 3.0,
        regime_sl_mult_map: dict[str, float] | None = None,
        price_reconciliation_threshold: float = 0.003,
    ) -> None:
        self._validator = signal_validator
        self._balance_provider = balance_provider
        self._atr_calculator = atr_calculator
        self._exchange = exchange_client
        self._position_repo = position_repo
        self._logger = execution_logger
        self._is_halted = is_halted
        self._idempotency = idempotency_store
        self._snapshot_repo = snapshot_repo
        self._risk_pct = Decimal(str(risk_pct))
        self._sl_mult = Decimal(str(sl_atr_multiplier))
        self._tp_mult = Decimal(str(tp_atr_multiplier))
        if regime_sl_mult_map:
            self._regime_sl_mult = {k: Decimal(str(v)) for k, v in regime_sl_mult_map.items()}
        else:
            self._regime_sl_mult = {
                "TRENDING": Decimal("2.0"),
                "RANGING": Decimal("1.0"),
            }
        self._price_recon_threshold = Decimal(str(price_reconciliation_threshold))
        self._peak_balance: Decimal | None = None

    async def execute(self, signal: ExecutionSignal) -> ExecuteSignalResult:
        execution_event_id = str(uuid7())
        lineage_id = str(uuid7())

        # 1. Validar
        validation = self._validator.validate(signal)
        if not validation.valid:
            await self._logger.log_rejection(signal.signal_id, validation.message)
            raise ExecuteSignalError(
                f"signal {signal.signal_id} rejected: {validation.message}"
            )

        # 2. Circuit Breaker
        if await self._is_halted():
            msg = f"circuit breaker HALTED, rejecting signal {signal.signal_id}"
            await self._logger.log_rejection(signal.signal_id, msg)
            raise ExecuteSignalError(msg)

        # 3. Balance + ATR
        try:
            quote_currency = Symbol(signal.symbol).quote_currency
            balance = await self._balance_provider.get_free_balance(quote_currency)
            atr_value = await self._atr_calculator.get_atr(signal.symbol)
            atr = atr_value.value
        except Exception as e:
            raise ExecuteSignalError(
                f"failed to fetch market data: {e}"
            ) from e
        if self._peak_balance is None or balance > self._peak_balance:
            self._peak_balance = balance
        drawdown = (
            (self._peak_balance - balance) / self._peak_balance
            if self._peak_balance > 0
            else Decimal("0")
        )

        # 4. Tamaño de posición (risk multiplier from ExecutionGuard)
        entry_price = signal.price
        if entry_price is None:
            raise ExecuteSignalError(
                "signal has no price — price provider required for live execution"
            )
        if entry_price <= 0:
            raise ExecuteSignalError("invalid entry_price <= 0")

        if atr > 0 and entry_price > 0:
            signal.metadata["atr_price_ratio"] = float(atr / entry_price)
        risk_multiplier = Decimal(str(signal.metadata.get("risk_multiplier", 1.0)))
        risk_amount = balance * self._risk_pct * risk_multiplier

        # Volatility-regime-aware SL multiplier (Fix 2)
        regime = signal.metadata.get("regime", "UNKNOWN")
        sl_mult_effective = self._regime_sl_mult.get(regime, self._sl_mult)
        signal.metadata["regime_sl_multiplier"] = float(sl_mult_effective)

        sl_distance = max(atr * sl_mult_effective, entry_price * Decimal("0.005"))
        units = risk_amount / sl_distance
        if units <= 0:
            raise ExecuteSignalError("computed position size is zero")

        # 5. SL/TP prices
        side = OrderSide.BUY if signal.side.name == "BUY" else OrderSide.SELL
        if side == OrderSide.BUY:
            sl_price = entry_price - sl_distance
            tp_price = entry_price + (atr * self._tp_mult)
        else:
            sl_price = entry_price + sl_distance
            tp_price = entry_price - (atr * self._tp_mult)

        # Guard against negative/inverted SL/TP
        if side == OrderSide.BUY:
            sl_price = max(sl_price, Decimal("0.01"))
            tp_price = max(tp_price, sl_price + Decimal("0.01"))
        else:
            sl_price = max(sl_price, entry_price + Decimal("0.01"))
            tp_price = min(tp_price, entry_price - Decimal("0.01"))
            tp_price = max(tp_price, Decimal("0.01"))

        # 6. Fix 2: Idempotency check
        idempotency_key = f"exec:{signal.signal_id}"
        if self._idempotency is not None:
            is_new = await self._idempotency.check_and_record(
                idempotency_key,
                exchange_order_id="",
                event_id=execution_event_id,
            )
            if not is_new:
                raise ExecuteSignalError(
                    f"duplicate execution: signal {signal.signal_id} "
                    f"already executed (key={idempotency_key})"
                )

        # 7. Place order (the risky part — outside transaction boundary)
        order = CompositeOrder(
            symbol=signal.symbol,
            side=side,
            entry_amount=units,
            entry_price=entry_price,
            sl_price=sl_price,
            tp_price=tp_price,
        )
        try:
            order_result = await self._exchange.place_composite_order(order)
        except SlPlacementError as e:
            # Entry filled but SL failed — emergency close, persist CRITICAL_UNPROTECTED
            entry_ok = e.entry_order
            if entry_ok is not None and entry_ok.filled_amount and entry_ok.filled_amount > 0:
                try:
                    emergency = await self._exchange.place_emergency_market(
                        symbol=signal.symbol,
                        side=OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY,
                        amount=entry_ok.filled_amount,
                    )
                    log.critical(
                        "emergency_close_issued",
                        signal_id=signal.signal_id,
                        filled_amount=str(entry_ok.filled_amount),
                        emergency_order_id=emergency.id,
                    )
                except Exception as emg_err:
                    # Emergency close failed — position on exchange with no SL
                    unprot = LivePosition(
                        position_id=uuid4().hex[:12],
                        symbol=signal.symbol,
                        side=side,
                        units=entry_ok.filled_amount,
                        entry_price=entry_ok.avg_price or entry_price,
                        current_price=entry_ok.avg_price or entry_price,
                        sl_price=None,
                        tp_price=None,
                        lineage_id=lineage_id,
                        sl_order_id="",
                        tp_order_id="",
                        status="CRITICAL_UNPROTECTED",
                        metadata={
                            "signal_price": str(entry_price),
                            "emergency_close_failed": str(emg_err),
                        },
                    )
                    for attempt in range(1, MAX_RETRIES + 1):
                        try:
                            await self._position_repo.save(unprot)
                            break
                        except Exception as repo_err:
                            if attempt == MAX_RETRIES:
                                log.critical(
                                    "critical_unprotected_persist_failed",
                                    position_id=unprot.position_id,
                                    signal_id=signal.signal_id,
                                    error=str(repo_err),
                                )
                            else:
                                await asyncio.sleep(RETRY_DELAY_S)
                    log.critical(
                        "position_critical_unprotected",
                        position_id=unprot.position_id,
                        signal_id=signal.signal_id,
                        symbol=signal.symbol,
                        side=side.value,
                        filled_amount=str(entry_ok.filled_amount),
                    )
            raise ExecuteSignalError(
                f"SL placement failed after retry exhaustion: {e}"
            ) from e
        except Exception as e:
            raise ExecuteSignalError(f"order placement failed: {e}") from e

        # Price reconciliation: detect & correct divergence between signal_price and fill_price
        effective_sl_order_id = order_result.sl_order_id
        if order_result.avg_price is not None and entry_price > 0:
            fill_price = order_result.avg_price
            divergence = abs(fill_price - entry_price) / entry_price
            divergence_pct = float(divergence * 100)

            if divergence > Decimal("0.005"):
                log.critical(
                    "price_divergence_above_threshold",
                    signal_id=signal.signal_id,
                    signal_price=str(entry_price),
                    fill_price=str(fill_price),
                    divergence_pct=round(divergence_pct, 3),
                    threshold_pct=0.5,
                )

            # Fix 1: Recalculate SL/TP based on fill price if divergence > threshold
            if divergence > self._price_recon_threshold:
                fill_sl_distance = max(atr * sl_mult_effective, fill_price * Decimal("0.005"))
                if side == OrderSide.BUY:
                    corrected_sl = fill_price - fill_sl_distance
                    corrected_tp = fill_price + (atr * self._tp_mult)
                else:
                    corrected_sl = fill_price + fill_sl_distance
                    corrected_tp = fill_price - (atr * self._tp_mult)

                # Guards against negative/inverted SL/TP
                if side == OrderSide.BUY:
                    corrected_sl = max(corrected_sl, Decimal("0.01"))
                    corrected_tp = max(corrected_tp, corrected_sl + Decimal("0.01"))
                else:
                    corrected_sl = max(corrected_sl, fill_price + Decimal("0.01"))
                    corrected_tp = min(corrected_tp, fill_price - Decimal("0.01"))
                    corrected_tp = max(corrected_tp, Decimal("0.01"))

                # Only correct if SL moves by >10bps
                if abs(corrected_sl - sl_price) > fill_price * Decimal("0.001"):
                    corrected_sl_side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY
                    try:
                        new_sl = await self._exchange.place_stop_loss_order(
                            symbol=signal.symbol,
                            side=corrected_sl_side,
                            amount=order_result.filled_amount or order.entry_amount,
                            stop_price=corrected_sl,
                        )
                        # Cancel original SL (best-effort — old SL is wider, so if
                        # cancel fails the corrected SL triggers first anyway)
                        if order_result.sl_order_id:
                            try:
                                await self._exchange.cancel_order(
                                    order_result.sl_order_id, signal.symbol
                                )
                            except Exception:
                                pass

                        sl_price = corrected_sl
                        tp_price = corrected_tp
                        effective_sl_order_id = new_sl.id

                        log.info(
                            "price_reconciliation_applied",
                            signal_id=signal.signal_id,
                            fill_price=str(fill_price),
                            signal_price=str(entry_price),
                            divergence_pct=round(divergence_pct, 3),
                            original_sl=str(sl_price),
                            corrected_sl=str(corrected_sl),
                            new_sl_order_id=new_sl.id,
                        )
                    except Exception as e:
                        log.warning(
                            "price_reconciliation_failed",
                            signal_id=signal.signal_id,
                            error=str(e),
                            divergence_pct=round(divergence_pct, 3),
                            detail="original SL/TP preserved",
                        )

        # 8. Update idempotency store with actual exchange_order_id
        if self._idempotency is not None:
            try:
                await self._idempotency.update_exchange_order_id(
                    idempotency_key,
                    order_result.id,
                )
            except Exception as e:
                log.warning("idempotency_update_failed", signal_id=signal.signal_id, error=str(e))

        # 9. Persistir posición (Fix 1: ExecutionTransactionBoundary — retry)
        position = LivePosition(
            position_id=uuid4().hex[:12],
            symbol=signal.symbol,
            side=side,
            units=order_result.filled_amount,
            entry_price=order_result.avg_price or entry_price,
            current_price=order_result.avg_price or entry_price,
            sl_price=sl_price,
            tp_price=tp_price,
            lineage_id=lineage_id,
            sl_order_id=effective_sl_order_id,
            tp_order_id=order_result.tp_order_id,
            metadata={"signal_price": str(entry_price)},
        )
        position_saved = False
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                await self._position_repo.save(position)
                position_saved = True
                break
            except Exception as e:
                if attempt == MAX_RETRIES:
                    raise ExecuteSignalError(
                        f"position save failed after {MAX_RETRIES} retries: {e}"
                    ) from e
                await asyncio.sleep(RETRY_DELAY_S)

        # 10. Flush snapshot (Fix 3: best-effort with retry)
        if self._snapshot_repo is not None:
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    snapshot = SystemSnapshot(
                        timestamp=datetime.now(timezone.utc),
                        equity=balance,
                        drawdown=drawdown,
                        open_positions=[
                            {
                                "id": position.position_id,
                                "symbol": position.symbol,
                                "side": position.side.value,
                                "quantity": float(position.units),
                                "entryPrice": float(position.entry_price),
                                "currentPrice": float(position.current_price),
                            }
                        ],
                        mode="LIVE",
                        risk_state="OK",
                        active_symbols=[position.symbol],
                        event_id=execution_event_id,
                    )
                    await self._snapshot_repo.save_snapshot(snapshot)
                    break
                except Exception as e:
                    if attempt == MAX_RETRIES:
                        log.critical(
                            "snapshot_flush_failed",
                            position_id=position.position_id,
                            signal_id=signal.signal_id,
                            lineage_id=lineage_id,
                            retries=MAX_RETRIES,
                            error=str(e),
                        )
                    else:
                        await asyncio.sleep(RETRY_DELAY_S)

        # 11. Log + report (Fix 2: lineage_id propagation)
        report = ExecutionReport(
            report_id=uuid4().hex[:12],
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            side=side,
            status=order_result.status.value,
            filled_qty=order_result.filled_amount,
            avg_price=order_result.avg_price,
            order_id=order_result.id,
            position_id=position.position_id,
            lineage_id=lineage_id,
        )
        await self._logger.log_execution(report)

        return ExecuteSignalResult(
            report=report,
            position=position,
            event_id=execution_event_id,
            lineage_id=lineage_id,
        )
