"""ShadowOutcomeWorker: evaluates decisions against subsequent candle data.

Reads decisions from shadow:decisions:{symbol}, waits for lookahead
candles to complete, then computes close-based and range-based outcomes.

Range-based evaluation captures worst-case MAE/MFE to avoid optimistic
bias from close-only evaluation.

Deduplication via decision_id: outcome is stored with SETNX; if the
same decision_id already has an outcome, the write is skipped.
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from typing import Any

import structlog

log = structlog.get_logger()

LOOKAHEAD_CANDLES = 5
POLL_INTERVAL_S = 10.0


def _compute_atr(candles: list[dict], period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    tr_sum = 0.0
    for i in range(-period, 0):
        prev_close = candles[i - 1].get("close", 0) if abs(i - 1) <= len(candles) else 0
        high = candles[i].get("high", 0)
        low = candles[i].get("low", 0)
        close = candles[i].get("close", 0)
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_sum += tr
    return tr_sum / period


def _range_outcome(
    entry_price: float,
    high: float,
    low: float,
    side: str,
) -> dict:
    direction = 1.0 if side == "BUY" else -1.0
    mae = (low - entry_price) * direction if side == "BUY" else (entry_price - high) * direction
    mfe = (high - entry_price) * direction if side == "BUY" else (entry_price - low) * direction
    return {"mae": round(mae / entry_price * 100, 4), "mfe": round(mfe / entry_price * 100, 4)}


async def shadow_outcome_worker_loop(
    client: Any,
    candle_buffer: Any,
    symbol: str,
) -> None:
    log.info("shadow_outcome_worker_started", symbol=symbol)
    stream = f"shadow:decisions:{symbol.replace('/', '').lower()}"
    last_id = "$"

    while True:
        try:
            await asyncio.sleep(POLL_INTERVAL_S)
            res = await client.xread({stream: last_id}, block=2000, count=100)
            if not res:
                continue

            for item in res:
                entries = getattr(item, "entries", item[1] if isinstance(item, (list, tuple)) else [])
                for entry_id, fields in entries:
                    last_id = (
                        entry_id.decode() if isinstance(entry_id, (bytes, bytearray)) else entry_id
                    )
                    if isinstance(fields, dict):
                        decoded = {
                            (k.decode() if isinstance(k, (bytes, bytearray)) else k): (
                                v.decode() if isinstance(v, (bytes, bytearray)) else v
                            )
                            for k, v in fields.items()
                        }
                    elif isinstance(fields, list):
                        decoded = {
                            k.decode() if isinstance(k, bytes) else k: (
                                v.decode() if isinstance(v, bytes) else v
                            )
                            for k, v in fields
                        }
                    else:
                        continue

                    payload = decoded.get("p")
                    if not payload:
                        continue
                    try:
                        decision = json.loads(payload)
                    except Exception:
                        continue

                    decision_id = decision.get("decision_id", "")
                    if not decision_id:
                        continue

                    candle_ts = int(decision.get("candle_ts", 0))
                    lookahead_ms = LOOKAHEAD_CANDLES * 3600_000
                    deadline_ts = candle_ts + lookahead_ms
                    now_ms = int(time.time() * 1000)

                    if now_ms < deadline_ts:
                        continue

                    candles = candle_buffer.to_ohlcv_dicts()
                    exit_candle = _find_candle_at(candles, deadline_ts)
                    if exit_candle is None:
                        continue

                    entry_price = float(decision.get("price_at_decision", "0"))
                    action = decision.get("action", "")
                    if entry_price <= 0 or action not in ("BUY", "SELL"):
                        continue

                    exit_close = float(exit_candle.get("close", entry_price))
                    exit_high = float(exit_candle.get("high", entry_price))
                    exit_low = float(exit_candle.get("low", entry_price))

                    atr = _compute_atr(candles)

                    direction = 1.0 if action == "BUY" else -1.0
                    close_pnl = (exit_close - entry_price) / entry_price * direction * 100
                    r_multiple = close_pnl / (atr / entry_price * 100) if atr > 0 else 0.0
                    range_ = _range_outcome(entry_price, exit_high, exit_low, action)

                    regime = decision.get("regime", "unknown")

                    outcome = {
                        "decision_id": decision_id,
                        "candle_ts": candle_ts,
                        "action": action,
                        "entry_price": str(entry_price),
                        "exit_price": str(exit_close),
                        "close_pnl_pct": round(close_pnl, 4),
                        "r_multiple": round(r_multiple, 4),
                        "mae_pct": range_["mae"],
                        "mfe_pct": range_["mfe"],
                        "regime": regime,
                        "model_version": decision.get("model_version", ""),
                        "computed_ts_ms": now_ms,
                    }

                    outcome_key = f"shadow:outcome:{decision_id}"
                    set_ok = await client.setnx(outcome_key, json.dumps(outcome))
                    if set_ok:
                        await client.expire(outcome_key, 86400 * 30)
                        log.info(
                            "shadow_outcome_computed",
                            decision_id=decision_id,
                            close_pnl_pct=round(close_pnl, 2),
                            r_multiple=round(r_multiple, 2),
                        )

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("shadow_outcome_worker_error", error=str(exc))


def _find_candle_at(candles: list[dict], target_ts: int) -> dict | None:
    for c in reversed(candles):
        ts = int(c.get("timestamp", 0))
        if ts >= target_ts:
            return c
    return None
