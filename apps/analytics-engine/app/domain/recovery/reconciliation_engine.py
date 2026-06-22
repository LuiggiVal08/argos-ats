"""Reconciliation Engine — compares local state vs exchange state.

Rule: Exchange ALWAYS wins, BUT with guardrails (Fix 3):
  - Response must be COMPLETE (all expected fields present)
  - Timestamp must be FRESH (data not stale > 60s)
  - Retry threshold: at least 2 successful fetches before trusting

States:
  - MATCHED: local == exchange
  - MISSING_ON_EXCHANGE: position exists locally but not on exchange → close locally
  - MISSING_LOCAL: position exists on exchange but not locally → reconstruct
  - PARTIAL_MISMATCH: quantities differ → reconcile (with guardrails)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any


class ReconciliationStatus(Enum):
    MATCHED = "MATCHED"
    MISSING_ON_EXCHANGE = "MISSING_ON_EXCHANGE"
    MISSING_LOCAL = "MISSING_LOCAL"
    PARTIAL_MISMATCH = "PARTIAL_MISMATCH"


@dataclass(frozen=True)
class ReconciliationResult:
    status: ReconciliationStatus
    position_id: str
    symbol: str
    local_units: Decimal = Decimal("0")
    exchange_units: Decimal = Decimal("0")
    local_entry: Decimal = Decimal("0")
    exchange_entry: Decimal = Decimal("0")
    action_taken: str = ""
    guardrail_failed: bool = False
    guardrail_reason: str = ""


@dataclass(frozen=True)
class ReconciliationSummary:
    total: int = 0
    matched: int = 0
    missing_on_exchange: int = 0
    missing_local: int = 0
    partial_mismatch: int = 0
    skipped_guardrails: int = 0
    details: list[ReconciliationResult] = field(default_factory=list)

    @property
    def is_consistent(self) -> bool:
        return (
            self.missing_on_exchange == 0
            and self.missing_local == 0
            and self.skipped_guardrails == 0
        )


class ReconciliationEngine:
    """Pure-domain reconciliation logic with exchange guardrails.

    Stateless — receives local positions + exchange positions,
    returns a summary of differences.

    Guardrails (Fix 3):
      1. Completeness: exchange data must have symbol + quantity + entry_price
      2. Freshness: exchange data must be recent (configurable stale threshold)
      3. Retry: at least 2 successful fetches required (caller's responsibility)
    """

    STALE_THRESHOLD_SEC = 60  # exchange data older than this is stale

    def reconcile(
        self,
        local_positions: list[dict[str, Any]],
        exchange_positions: list[dict[str, Any]],
        exchange_timestamp: float | None = None,
    ) -> ReconciliationSummary:
        """Compare local vs exchange positions with guardrails.

        Args:
            local_positions: List of position dicts from SQLite
                (keys: id, symbol, side, entryPrice, quantity, status).
            exchange_positions: List of position dicts from CCXT
                (keys: symbol, side, quantity, entryPrice, currentPrice).
            exchange_timestamp: Unix timestamp of when exchange data was fetched.
                If None, guardrail freshness check is skipped.

        Returns:
            ReconciliationSummary with per-position results.
        """
        results: list[ReconciliationResult] = []
        local_map: dict[str, dict] = {}
        ex_map: dict[str, dict] = {}

        for lp in local_positions:
            sym = lp.get("symbol", "")
            if lp.get("status") in ("OPEN", "PARTIALLY_CLOSED"):
                local_map[sym] = lp

        for ep in exchange_positions:
            sym = ep.get("symbol", "")

            # Fix 3a: Guardrail — completeness check
            if not self._is_complete(ep):
                results.append(ReconciliationResult(
                    status=ReconciliationStatus.PARTIAL_MISMATCH,
                    position_id=local_map[sym].get("id", "") if sym in local_map else "",
                    symbol=sym,
                    guardrail_failed=True,
                    guardrail_reason="exchange_data_incomplete",
                ))
                continue

            qty = Decimal(str(ep.get("quantity", 0)))
            if qty > 0:
                ex_map[sym] = ep

        # Fix 3b: Guardrail — freshness check
        is_stale = False
        if exchange_timestamp is not None:
            age = time.time() - exchange_timestamp
            is_stale = age > self.STALE_THRESHOLD_SEC

        all_symbols = set(local_map.keys()) | set(ex_map.keys())

        for sym in sorted(all_symbols):
            local = local_map.get(sym)
            exchange = ex_map.get(sym)

            # Skip already-processed incomplete data
            existing = [r for r in results if r.symbol == sym]
            if existing and existing[0].guardrail_failed:
                continue

            if local and not exchange:
                action = "close_local"
                if is_stale:
                    action = "skip_stale_exchange_data"
                    results.append(ReconciliationResult(
                        status=ReconciliationStatus.PARTIAL_MISMATCH,
                        position_id=local.get("id", ""),
                        symbol=sym,
                        local_units=Decimal(str(local.get("quantity", 0))),
                        guardrail_failed=True,
                        guardrail_reason=f"exchange_data_stale_{int(age)}s",
                        action_taken=action,
                    ))
                    continue

                results.append(ReconciliationResult(
                    status=ReconciliationStatus.MISSING_ON_EXCHANGE,
                    position_id=local.get("id", ""),
                    symbol=sym,
                    local_units=Decimal(str(local.get("quantity", 0))),
                    action_taken=action,
                ))

            elif exchange and not local:
                results.append(ReconciliationResult(
                    status=ReconciliationStatus.MISSING_LOCAL,
                    position_id="",
                    symbol=sym,
                    exchange_units=Decimal(str(exchange.get("quantity", 0))),
                    exchange_entry=Decimal(str(exchange.get("entryPrice", 0))),
                    action_taken="reconstruct",
                ))

            elif local and exchange:
                local_qty = Decimal(str(local.get("quantity", 0)))
                ex_qty = Decimal(str(exchange.get("quantity", 0)))
                local_entry = Decimal(str(local.get("entryPrice", 0)))
                ex_entry = Decimal(str(exchange.get("entryPrice", 0)))

                if local_qty == ex_qty and local_entry == ex_entry:
                    results.append(ReconciliationResult(
                        status=ReconciliationStatus.MATCHED,
                        position_id=local.get("id", ""),
                        symbol=sym,
                        local_units=local_qty,
                        exchange_units=ex_qty,
                    ))
                else:
                    action = "reconcile_quantities"
                    if is_stale:
                        action = "skip_stale_exchange_data"
                        results.append(ReconciliationResult(
                            status=ReconciliationStatus.PARTIAL_MISMATCH,
                            position_id=local.get("id", ""),
                            symbol=sym,
                            local_units=local_qty,
                            exchange_units=ex_qty,
                            local_entry=local_entry,
                            exchange_entry=ex_entry,
                            guardrail_failed=True,
                            guardrail_reason=f"exchange_data_stale_{int(age)}s",
                            action_taken=action,
                        ))
                        continue

                    results.append(ReconciliationResult(
                        status=ReconciliationStatus.PARTIAL_MISMATCH,
                        position_id=local.get("id", ""),
                        symbol=sym,
                        local_units=local_qty,
                        exchange_units=ex_qty,
                        local_entry=local_entry,
                        exchange_entry=ex_entry,
                        action_taken=action,
                    ))

        # Aggregate summary
        summary = ReconciliationSummary(total=len(results), details=results)
        for r in results:
            if r.guardrail_failed:
                summary = ReconciliationSummary(
                    total=summary.total, matched=summary.matched,
                    missing_on_exchange=summary.missing_on_exchange,
                    missing_local=summary.missing_local,
                    partial_mismatch=summary.partial_mismatch,
                    skipped_guardrails=summary.skipped_guardrails + 1,
                    details=summary.details,
                )
            elif r.status == ReconciliationStatus.MATCHED:
                summary = ReconciliationSummary(
                    total=summary.total, matched=summary.matched + 1,
                    missing_on_exchange=summary.missing_on_exchange,
                    missing_local=summary.missing_local,
                    partial_mismatch=summary.partial_mismatch,
                    skipped_guardrails=summary.skipped_guardrails,
                    details=summary.details,
                )
            elif r.status == ReconciliationStatus.MISSING_ON_EXCHANGE:
                summary = ReconciliationSummary(
                    total=summary.total, matched=summary.matched,
                    missing_on_exchange=summary.missing_on_exchange + 1,
                    missing_local=summary.missing_local,
                    partial_mismatch=summary.partial_mismatch,
                    skipped_guardrails=summary.skipped_guardrails,
                    details=summary.details,
                )
            elif r.status == ReconciliationStatus.MISSING_LOCAL:
                summary = ReconciliationSummary(
                    total=summary.total, matched=summary.matched,
                    missing_on_exchange=summary.missing_on_exchange,
                    missing_local=summary.missing_local + 1,
                    partial_mismatch=summary.partial_mismatch,
                    skipped_guardrails=summary.skipped_guardrails,
                    details=summary.details,
                )
            elif r.status == ReconciliationStatus.PARTIAL_MISMATCH:
                summary = ReconciliationSummary(
                    total=summary.total, matched=summary.matched,
                    missing_on_exchange=summary.missing_on_exchange,
                    missing_local=summary.missing_local,
                    partial_mismatch=summary.partial_mismatch + 1,
                    skipped_guardrails=summary.skipped_guardrails,
                    details=summary.details,
                )
        return summary

    @staticmethod
    def _is_complete(position: dict[str, Any]) -> bool:
        """Check that exchange position data has all required fields."""
        required = ("symbol", "quantity", "entryPrice")
        for field_name in required:
            if field_name not in position or position[field_name] is None:
                return False
        try:
            qty = Decimal(str(position.get("quantity", 0)))
            if qty <= 0:
                return False
        except (InvalidOperation, ValueError, TypeError):
            return False
        return True
