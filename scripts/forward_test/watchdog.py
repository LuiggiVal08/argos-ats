"""STEP 5 — Safety / Control Layer.

Watchdog with kill switch. Monitors drawdown, data feed health,
and prediction quality.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

logger = logging.getLogger("forward_test.watchdog")

INCIDENT_LOG_DIR = Path(__file__).parent.parent.parent / "logs" / "forward_test" / "incidents"


class SafetyError(Exception):
    """Raised when the watchdog triggers a stop condition."""


class Watchdog:
    """Safety monitor with hard-stop conditions.

    Tracks:
    - drawdown (balance-based)
    - consecutive data feed failures
    - NaN in features or predictions
    """

    def __init__(
        self,
        max_drawdown: float = 0.05,
        max_consecutive_failures: int = 5,
        initial_balance: float = 0.0,
        dry_run: bool = True,
    ):
        self.max_drawdown = max_drawdown
        self.max_failures = max_consecutive_failures
        self.peak_balance = initial_balance
        self.current_balance = initial_balance
        self.consecutive_failures = 0
        self.frozen = False
        self.freeze_reason: str | None = None
        self.dry_run = dry_run
        self.incident_log: list[dict] = []

    def update_balance(self, balance: float) -> None:
        self.current_balance = balance
        self.peak_balance = max(self.peak_balance, balance)

    def check_drawdown(self) -> float:
        if self.peak_balance <= 0:
            return 0.0
        dd = (self.peak_balance - self.current_balance) / self.peak_balance
        if dd >= self.max_drawdown:
            self._freeze(
                f"Drawdown {dd:.2%} >= limit {self.max_drawdown:.2%}",
                severity="HARD_STOP",
            )
        return dd

    def check_data_feed(self, success: bool) -> None:
        if success:
            self.consecutive_failures = 0
        else:
            self.consecutive_failures += 1
            if self.consecutive_failures >= self.max_failures:
                self._freeze(
                    f"Data feed failure: {self.consecutive_failures} consecutive cycles",
                    severity="HARD_STOP",
                )

    def check_prediction(self, y_proba: float) -> None:
        if math.isnan(y_proba) or y_proba < -1e-6 or y_proba > 1 + 1e-6:
            self._freeze(
                f"Invalid prediction: y_proba={y_proba}",
                severity="HARD_STOP",
            )

    def check_features(self, X_row: np.ndarray) -> None:
        if np.any(np.isnan(X_row)) or np.any(np.isinf(X_row)):
            nan_cols = np.where(np.isnan(X_row))[1] if X_row.ndim == 2 else np.where(np.isnan(X_row))[0]
            self._freeze(
                f"NaN in features at columns {nan_cols.tolist()[:10]}",
                severity="HARD_STOP",
            )

    def _freeze(self, reason: str, severity: str = "HARD_STOP") -> None:
        self.frozen = True
        self.freeze_reason = reason
        incident = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "severity": severity,
            "reason": reason,
            "balance": self.current_balance,
            "peak_balance": self.peak_balance,
        }
        self.incident_log.append(incident)
        INCIDENT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        incident_path = INCIDENT_LOG_DIR / f"incident_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(incident_path, "w") as f:
            json.dump(incident, f, indent=2)

        if severity == "HARD_STOP":
            logger.critical(f"🛑 WATCHDOG TRIGGERED: {reason}")
            if not self.dry_run:
                raise SafetyError(reason)
            logger.warning("(dry-run: would have raised SafetyError)")

    def status(self) -> dict:
        dd = self.check_drawdown()
        return {
            "frozen": self.frozen,
            "freeze_reason": self.freeze_reason,
            "drawdown": round(dd, 6),
            "consecutive_failures": self.consecutive_failures,
            "peak_balance": self.peak_balance,
            "current_balance": self.current_balance,
        }
