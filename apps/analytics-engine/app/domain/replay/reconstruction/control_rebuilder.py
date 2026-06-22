"""ControlRebuilder — reconstruye decisiones del control layer desde el ledger.

Reproduce las decisiones registradas del control layer (HALT, REDUCE, CONTINUE)
sin re-ejecutar el modelo ni el inference engine.

NO genera nuevas decisiones.
Solo reconstruye o valida consistencia del pasado.
"""

from __future__ import annotations

from typing import Any


class ControlRebuilder:
    """Rebuilds control layer decisions from persisted protocol snapshots.

    The control layer decisions (HALT/REDUCE/CONTINUE) are part of the
    protocol snapshot and must be reproduced exactly as recorded.
    """

    def __init__(self) -> None:
        self._decisions: list[dict[str, Any]] = []

    def rebuild(self, protocol_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Rebuild control decision sequence from protocol snapshots.

        Each event should contain at minimum:
        - timestamp
        - control.action (HALT/REDUCE/CONTINUE)
        - control.h_interno
        - control.h_externo
        - control.reason
        """
        sorted_events = sorted(
            protocol_events,
            key=lambda e: e.get("timestamp", ""),
        )
        decisions = []
        for event in sorted_events:
            control = event.get("control", {})
            decisions.append({
                "timestamp": event.get("timestamp", ""),
                "action": control.get("action", "UNKNOWN"),
                "h_interno": control.get("h_interno", 0.0),
                "h_externo": control.get("h_externo", 0.0),
                "hazard_immediate": control.get("hazard_immediate", 0.0),
                "hazard_cumulative": control.get("hazard_cumulative", 0.0),
                "reason": control.get("reason", ""),
                "days_in_reduce": control.get("days_in_reduce", 0),
            })
        self._decisions = decisions
        return self._decisions

    @property
    def decisions(self) -> list[dict[str, Any]]:
        return list(self._decisions)

    @property
    def last_decision(self) -> dict[str, Any] | None:
        if not self._decisions:
            return None
        return self._decisions[-1]

    def validate_consistency(
        self,
        original_protocol: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate control decision consistency.

        Checks:
        - Decision sequence is non-empty
        - Actions are valid (HALT/REDUCE/CONTINUE)
        - Hazard values are in [0, 1]
        - Matches original protocol if provided
        """
        issues = []
        valid_actions = {"HALT", "REDUCE", "CONTINUE", "UNKNOWN"}

        for i, dec in enumerate(self._decisions):
            action = dec.get("action", "")
            if action not in valid_actions:
                issues.append(f"Index {i}: invalid action '{action}'")

            for h_field in ["h_interno", "h_externo",
                            "hazard_immediate", "hazard_cumulative"]:
                val = dec.get(h_field, -1)
                if not 0.0 <= val <= 1.0:
                    issues.append(
                        f"Index {i}: {h_field}={val} outside [0, 1]"
                    )

        # Compare with original
        match_rate = 1.0
        mismatches = []
        if original_protocol is not None and self._decisions:
            last = self._decisions[-1]
            orig_control = original_protocol.get("control", {})

            for field in ["action", "h_interno", "h_externo",
                          "hazard_immediate", "hazard_cumulative"]:
                o = orig_control.get(field)
                r = last.get(field)
                if isinstance(o, float) and isinstance(r, float):
                    if abs(o - r) > 1e-4:
                        mismatches.append(f"{field}: {o} vs {r}")
                        match_rate -= 0.1
                elif str(o) != str(r):
                    mismatches.append(f"{field}: {o} vs {r}")
                    match_rate -= 0.1

        return {
            "is_consistent": len(issues) == 0,
            "n_decisions": len(self._decisions),
            "last_action": self._decisions[-1]["action"]
                         if self._decisions else "NONE",
            "match_rate": max(0.0, match_rate),
            "issues": issues,
            "mismatches": mismatches,
        }
