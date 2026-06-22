#!/usr/bin/env python3
"""Control Layer — Section 12 implementation.

FORMAL INVARIANTS:
1. ControlLayer NEVER observes P(edge|D). Only P(failure|D).
   Zero knowledge of model quality; acts only on hazard values.
2. h*(t) = max(h_interno, h_externo) is the ONLY decision variable.
   No additional heuristics, scores, or utility access.
3. Output is exactly one of: HALT, REDUCE, CONTINUE.
   No intermediate states, no "soft halt", no WARNING level.
4. REDUCE has a HARD TIMELINE of 7 calendar days.
   After 7 days, either hazard drops below theta_safe
   or escalates to HALT. Non-extendable.
5. HALT is IRREVERSIBLE without manual --reset.
   No automatic recovery, no timer-based re-entry.
6. This module receives h_interno and h_externo as scalar inputs.
   It does NOT import utility_measure, trajectory_model,
   or external_reference. Completely decoupled.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Literal

logger = logging.getLogger("control_layer")

# ── Thresholds (configurable by operator, NOT by system) ──────────────────
THETA_IMMEDIATE = 0.15    # h* > 15% → HALT immediately
THETA_CUMULATIVE = 0.50    # H(t) > 50% → HALT at day end
THETA_SAFE = 0.15          # h* < 15% → CONTINUE normally
REDUCE_MAX_DAYS = 7        # max days in REDUCE before escalating to HALT

Action = Literal["CONTINUE", "REDUCE", "HALT"]


@dataclass(frozen=True)
class HazardThresholds:
    """Operator-defined thresholds for hazard-based control.

    Invariant: these are CONFIGURABLE by operator, not by system.
    The system only reports H(t); thresholds are external policy.
    """
    theta_immediate: float = THETA_IMMEDIATE
    theta_cumulative: float = THETA_CUMULATIVE
    theta_safe: float = THETA_SAFE
    reduce_max_days: int = REDUCE_MAX_DAYS


DEFAULT_THRESHOLDS = HazardThresholds()


@dataclass
class ControlDecision:
    action: Action
    hazard_immediate: float
    hazard_cumulative: float
    h_interno: float
    h_externo: float
    days_in_reduce: int = 0
    reason: str | None = None
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return asdict(self)


class ControlLayer:
    """Hazard-based control layer.

    Blind to model quality: receives only hazard values.
    Produces exactly one of: CONTINUE, REDUCE, HALT.

    Invariant: this module has NO knowledge of U(tau), P(edge|D),
    model performance, or any system internals. It is a pure
    hazard-thresholding machine.
    """

    def __init__(self, thresholds: HazardThresholds = DEFAULT_THRESHOLDS):
        self.thresholds = thresholds
        self._cumulative_hazard = 0.0
        self._hazard_history: list[float] = []
        self._days_in_reduce = 0
        self._reduce_start: datetime | None = None
        self._is_halted = False
        self._halt_reason: str | None = None
        self._last_decision: ControlDecision | None = None

    @property
    def is_halted(self) -> bool:
        return self._is_halted

    @property
    def halt_reason(self) -> str | None:
        return self._halt_reason

    def evaluate(self, h_interno: float, h_externo: float,
                 force_daily: bool = True) -> ControlDecision:
        """Evaluate hazard and produce decision.

        Args:
            h_interno: P(failure|D) from trajectory model (Section 11.6).
            h_externo: P(U(H0) > U(model)|D) from reference layer (Section 14.5).
            force_daily: if True, only updates cumulative hazard once per day
                         (tracked internally via _hazard_history).

        Returns:
            ControlDecision with action and metadata.
        """
        if self._is_halted:
            decision = ControlDecision(
                action="HALT",
                hazard_immediate=0.0,
                hazard_cumulative=self._cumulative_hazard,
                h_interno=h_interno,
                h_externo=h_externo,
                days_in_reduce=self._days_in_reduce,
                reason=f"Already halted: {self._halt_reason}",
            )
            self._last_decision = decision
            return decision

        h_star = max(h_interno, h_externo)
        self._hazard_history.append(h_star)

        # Update cumulative hazard (exponentially weighted)
        if len(self._hazard_history) == 1:
            self._cumulative_hazard = h_star
        else:
            decay = 0.9
            self._cumulative_hazard = (
                decay * self._cumulative_hazard + (1 - decay) * h_star
            )

        action: Action
        reason: str | None = None

        # Check HALT conditions
        if h_star > self.thresholds.theta_immediate:
            action = "HALT"
            reason = (
                f"h*={h_star:.4f} > theta_immediate={self.thresholds.theta_immediate}"
            )
        elif self._cumulative_hazard > self.thresholds.theta_cumulative:
            action = "HALT"
            reason = (
                f"H(t)={self._cumulative_hazard:.4f} > "
                f"theta_cumulative={self.thresholds.theta_cumulative}"
            )
        else:
            # Check REDUCE conditions
            in_reduce_zone = (
                h_star >= self.thresholds.theta_safe
                and h_star <= self.thresholds.theta_cumulative
            )
            # Also trigger REDUCE when h_externo > h_interno repeatedly
            h_externo_significant = (
                h_externo > h_interno
                and h_externo > self.thresholds.theta_safe * 0.5
            )

            if in_reduce_zone or h_externo_significant:
                if self._reduce_start is None:
                    self._reduce_start = datetime.now()
                    self._days_in_reduce = 1
                else:
                    elapsed = (datetime.now() - self._reduce_start).days
                    self._days_in_reduce = min(elapsed, self.thresholds.reduce_max_days)

                if self._days_in_reduce >= self.thresholds.reduce_max_days:
                    action = "HALT"
                    reason = (
                        f"REDUCE exceeded {self.thresholds.reduce_max_days} days. "
                        f"H(t)={self._cumulative_hazard:.4f} not below "
                        f"theta_safe={self.thresholds.theta_safe}"
                    )
                else:
                    action = "REDUCE"
                    reason = (
                        f"h*={h_star:.4f} in REDUCE zone, "
                        f"day {self._days_in_reduce}/{self.thresholds.reduce_max_days}"
                    )
            else:
                action = "CONTINUE"
                self._days_in_reduce = 0
                self._reduce_start = None

        if action == "HALT":
            self._is_halted = True
            self._halt_reason = reason

        decision = ControlDecision(
            action=action,
            hazard_immediate=round(h_star, 4),
            hazard_cumulative=round(self._cumulative_hazard, 4),
            h_interno=round(h_interno, 4),
            h_externo=round(h_externo, 4),
            days_in_reduce=self._days_in_reduce,
            reason=reason,
        )
        self._last_decision = decision
        return decision

    def reset(self):
        """Reset control layer state (manual intervention only)."""
        self._cumulative_hazard = 0.0
        self._hazard_history = []
        self._days_in_reduce = 0
        self._reduce_start = None
        self._is_halted = False
        self._halt_reason = None
        self._last_decision = None
        logger.info("ControlLayer reset (manual intervention)")

    def status_dict(self) -> dict:
        """Return current status for health endpoint or logging."""
        return {
            "is_halted": self._is_halted,
            "halt_reason": self._halt_reason,
            "cumulative_hazard": round(self._cumulative_hazard, 4),
            "days_in_reduce": self._days_in_reduce,
            "hazard_history_n": len(self._hazard_history),
            "last_decision": (
                self._last_decision.to_dict() if self._last_decision else None
            ),
        }
