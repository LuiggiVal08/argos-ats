"""Experiment Control Plane (ECL) — Modules 1, 2, 3, 5.

Módulo 1: ExperimentSnapshot — captura estado inicial del experimento.
Módulo 2: RollingSharpeGuard — monitorea Sharpe ratio rolling 7d.
Módulo 3: BaselineDominanceChecker — detecta outperformance de EMA Cross.
Módulo 5: HealthCheckAggregator — emite phase_b_experiment_health cada 60s.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from math import sqrt
from statistics import mean, stdev
from typing import Any

import structlog

log = structlog.get_logger()

_SHARPE_MIN = 0.5
_BASELINE_CONSECUTIVE_DAYS = 7
_TRADING_DAYS_PER_YEAR = 252
_DRAWDOWN_MAX_PCT = 0.05
_MIN_DAILY_RETURNS = 5


# ── Module 1: Experiment Snapshot ───────────────────────────────────────────


class ExperimentSnapshot:
    """Captura el snapshot de inicio del experimento.

    Emite ``phase_b_experiment_started`` una única vez al arrancar
    PAPER_TRADING mode.
    """

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        execution_mode: str,
        initial_capital: Decimal,
        model_version: str = "",
        git_commit: str = "",
    ) -> None:
        self._ts = datetime.now(timezone.utc)
        self._symbol = symbol
        self._timeframe = timeframe
        self._execution_mode = execution_mode
        self._initial_capital = initial_capital
        self._model_version = model_version
        self._git_commit = git_commit
        self._config_hash = self._compute_config_hash()

    def _compute_config_hash(self) -> str:
        raw = {
            "symbol": self._symbol,
            "timeframe": self._timeframe,
            "execution_mode": self._execution_mode,
            "initial_capital": str(self._initial_capital),
            "confidence_threshold": os.environ.get("CONFIDENCE_THRESHOLD", "0.75"),
            "min_confidence": os.environ.get("MIN_CONFIDENCE", "0.3"),
            "cooldown_seconds": os.environ.get("COOLDOWN_SECONDS", "300"),
            "dd_threshold": os.environ.get("KILL_SWITCH_DRAWDOWN_PCT", "0.05"),
        }
        return hashlib.sha256(
            json.dumps(raw, sort_keys=True).encode()
        ).hexdigest()[:16]

    def emit(self) -> None:
        log.info(
            "phase_b_experiment_started",
            timestamp=self._ts.isoformat(),
            initial_capital=str(self._initial_capital),
            model_version=self._model_version,
            config_hash=self._config_hash,
            git_commit=self._git_commit,
            symbol=self._symbol,
            timeframe=self._timeframe,
            execution_mode=self._execution_mode,
        )

    @property
    def initial_capital(self) -> Decimal:
        return self._initial_capital


# ── Module 2: Rolling Sharpe Guard ─────────────────────────────────────────


class RollingSharpeGuard:
    """Monitorea Sharpe ratio rolling 7d.

    Emite ``[ecl:sharpe]`` con status OK / WARNING.
    NO detiene ejecución — solo advierte.
    """

    def __init__(self) -> None:
        self._risk_state: str = "NORMAL"
        self._last_sharpe: float = 0.0

    def check(self, daily_returns: dict[str, Decimal]) -> float:
        if not daily_returns:
            log.info("[ecl:sharpe] no_daily_returns")
            return 0.0
        returns = [float(r) for r in daily_returns.values()]
        if len(returns) < _MIN_DAILY_RETURNS:
            log.info(
                "[ecl:sharpe] insufficient_data",
                days=len(returns),
                min_required=_MIN_DAILY_RETURNS,
            )
            return 0.0
        s = stdev(returns)
        if s == 0:
            return 0.0
        sharpe = mean(returns) / s * sqrt(_TRADING_DAYS_PER_YEAR)
        status = "OK" if sharpe >= _SHARPE_MIN else "WARNING"
        self._risk_state = "HIGH_RISK" if sharpe < _SHARPE_MIN else "NORMAL"
        self._last_sharpe = sharpe
        log.info(
            "[ecl:sharpe] check",
            value=round(sharpe, 4),
            threshold=_SHARPE_MIN,
            status=status,
        )
        return sharpe

    @property
    def risk_state(self) -> str:
        return self._risk_state

    @property
    def last_sharpe(self) -> float:
        return self._last_sharpe


# ── Module 3: Baseline Dominance Checker ────────────────────────────────────


class BaselineDominanceChecker:
    """Detecta si EMA Cross supera al sistema por N días consecutivos.

    Cuando el baseline (EMA Cross) genera más PnL que el sistema
    durante ``_BASELINE_CONSECUTIVE_DAYS`` días seguidos, marca
    ``outperformance_detected = True``.
    """

    def __init__(self) -> None:
        self._consecutive_days: int = 0
        self._outperformance_detected: bool = False

    def check(self, system_pnl: Decimal, ema_cross_pnl: Decimal) -> None:
        if ema_cross_pnl > system_pnl:
            self._consecutive_days += 1
        else:
            self._consecutive_days = 0

        self._outperformance_detected = (
            self._consecutive_days >= _BASELINE_CONSECUTIVE_DAYS
        )

        log.info(
            "[ecl:baseline] check",
            system_pnl=str(system_pnl),
            ema_cross_pnl=str(ema_cross_pnl),
            consecutive_days=self._consecutive_days,
            threshold=_BASELINE_CONSECUTIVE_DAYS,
            outperformance_detected=self._outperformance_detected,
            status="UNDERPERFORMING" if self._outperformance_detected else "OK",
        )

    @property
    def outperformance_detected(self) -> bool:
        return self._outperformance_detected

    @property
    def consecutive_days(self) -> int:
        return self._consecutive_days


# ── Module 5: Health Check Aggregator ──────────────────────────────────────


class HealthCheckAggregator:
    """Agrega M1+M2+M3 en un snapshot unificado.

    Emite ``phase_b_experiment_health`` cada 60s con:
      - system_pnl_pct, baseline_ema_pct, buy_hold_pct
      - sharpe, drawdown_pct
      - status: STABLE | AT_RISK
    """

    def __init__(
        self,
        sharpe_guard: RollingSharpeGuard,
        baseline_checker: BaselineDominanceChecker,
        initial_capital: Decimal,
    ) -> None:
        self._sharpe_guard = sharpe_guard
        self._baseline_checker = baseline_checker
        self._initial_capital = initial_capital
        self._last_status: str = "STABLE"

    def emit(
        self,
        system_pnl: Decimal,
        bnh_pnl: Decimal,
        ema_pnl: Decimal,
        daily_returns: dict[str, Decimal],
        drawdown_pct: float,
    ) -> None:
        ic = float(self._initial_capital)
        system_pnl_pct = (float(system_pnl) / ic * 100) if ic > 0 else 0.0
        bnh_pnl_pct = (float(bnh_pnl) / ic * 100) if ic > 0 else 0.0
        ema_pnl_pct = (float(ema_pnl) / ic * 100) if ic > 0 else 0.0

        sharpe = self._sharpe_guard.check(daily_returns)
        self._baseline_checker.check(system_pnl, ema_pnl)

        if not daily_returns:
            status = "NO_DATA"
        elif (
            sharpe < _SHARPE_MIN
            or self._baseline_checker.outperformance_detected
            or drawdown_pct >= _DRAWDOWN_MAX_PCT
        ):
            status = "AT_RISK"
        else:
            status = "STABLE"

        self._last_status = status

        log.info(
            "phase_b_experiment_health",
            system_pnl_pct=round(system_pnl_pct, 4),
            baseline_ema_pct=round(ema_pnl_pct, 4),
            buy_hold_pct=round(bnh_pnl_pct, 4),
            sharpe=round(sharpe, 4),
            drawdown_pct=round(drawdown_pct, 4),
            sharpe_status="OK" if sharpe >= _SHARPE_MIN else "WARNING",
            baseline_outperformance=self._baseline_checker.outperformance_detected,
            baseline_consecutive_days=self._baseline_checker.consecutive_days,
            risk_state=self._sharpe_guard.risk_state,
            status=status,
        )

    @property
    def last_status(self) -> str:
        return self._last_status
