"""RiskEngine logging adapter — wraps domain entity with structured logging.

Logs every risk assessment with verdict, reason, and portfolio state.
Maintains hexagonal integrity: domain entity is never modified.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from ...domain.entities.risk_engine import (
    PortfolioState,
    RiskEngine,
    RiskVerdict,
)
from .logging_config import get_logger
from .correlation import bind_correlation_context

RISK_LOG = get_logger("system")


class LoggingRiskEngine:
    """Adapter that wraps RiskEngine and logs every assessment.

    Delegates to the real RiskEngine for all domain logic, then
    emits structured log events for RISK_ENGINE_ACCEPT / REJECT.
    """

    def __init__(self, engine: RiskEngine | None = None) -> None:
        self._engine = engine or RiskEngine()

    def assess(
        self,
        state: PortfolioState,
        symbol: str | None = None,
        **extra_log: Any,
    ) -> Any:
        """Assess a trade proposal and log the result."""
        assessment = self._engine.assess(state, symbol=symbol)

        log_payload: dict[str, Any] = {
            "verdict": assessment.verdict.value,
            "reason": assessment.reason,
            "open_positions": assessment.open_positions,
            "max_positions": assessment.max_positions,
            "total_exposure_pct": float(assessment.total_exposure_pct),
            "max_total_exposure_pct": float(assessment.max_total_exposure_pct),
            "symbol_exposure_pct": float(assessment.symbol_exposure_pct),
            "max_symbol_exposure_pct": float(assessment.max_symbol_exposure_pct),
            "daily_drawdown_pct": float(assessment.daily_drawdown_pct),
            "max_daily_drawdown_pct": float(assessment.max_daily_drawdown_pct),
            "consecutive_losses": assessment.current_losses,
            "max_consecutive_losses": assessment.max_losses,
            "portfolio_balance": float(state.total_balance),
            "portfolio_open_positions": state.open_count,
        }
        log_payload.update(extra_log)

        if assessment.verdict == RiskVerdict.APPROVED:
            RISK_LOG.info("RISK_ENGINE_ACCEPT", **log_payload)
        else:
            RISK_LOG.warning("RISK_ENGINE_REJECT", **log_payload)

        return assessment
