"""Fase B — Continuous forward test on Binance Futures Demo.

Loads a serialized model + scaler from models/{symbol}/, connects to
Binance Futures Demo via CCXT, polls for new 1h bars, predicts y_proba,
and executes trades per the QV2 production thresholds.

Usage:
    python scripts/forward_test.py [--symbol BTC] [--interval 60] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pickle
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_project_root = Path(__file__).parent.parent.resolve()
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from experiments.quant_validation_v2.common import (
    compute_base_ta,
    compute_mtf_features,
    compute_funding_features,
    BASE_FEATURES,
    FUNDING_FEATURES,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("forward_test")

LOOKAHEAD = 5
STRIDE = 5
BUFFER_HOURS = 2000
FUNDING_BUFFER = 500

THRESHOLDS = {"BUY": 0.60, "SELL": 0.40}

TRADING_FEE = 0.0010
SLIPPAGE = 0.0005
HALF_SPREAD = 0.00005
RT_COST = TRADING_FEE * 2 + SLIPPAGE * 2 + HALF_SPREAD * 2


def load_model(symbol: str):
    model_dir = Path(_project_root) / "models" / symbol.lower()
    with open(model_dir / "model.pkl", "rb") as f:
        model = pickle.load(f)
    with open(model_dir / "scaler.pkl", "rb") as f:
        scaler = pickle.load(f)
    with open(model_dir / "metadata.json") as f:
        meta = json.load(f)
    expected_features = meta.get("features", 53)
    logger.info(f"[{symbol}] model loaded: {model.__class__.__name__}, {expected_features} features")
    return model, scaler, expected_features


def create_exchange():
    ak = os.environ.get("BINANCE_TESTNET_API_KEY", "")
    sk = os.environ.get("BINANCE_TESTNET_SECRET", "")
    ex_id = os.environ.get("EXCHANGE_ID", "binanceusdm")
    exchange_cls = getattr(__import__("ccxt"), ex_id)
    ex = exchange_cls({
        "apiKey": ak,
        "secret": sk,
        "enableRateLimit": True,
    })
    ex.enable_demo_trading(True)
    return ex


def bootstrap_ohlcv(exchange, symbol_ccxt, limit=1000):
    """Fetch the most recent candles (no since = latest)."""
    ohlcv = exchange.fetch_ohlcv(symbol_ccxt, "1h", limit=limit)
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    logger.info(f"[bootstrap] OHLCV: {len(df)} bars from {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}")
    return df


def bootstrap_funding(exchange, symbol_ccxt, limit=FUNDING_BUFFER):
    funding = exchange.fetch_funding_rate_history(symbol_ccxt, limit=limit)
    rows = []
    for f in funding:
        rows.append({
            "timestamp": pd.to_datetime(f["timestamp"], unit="ms"),
            "fundingRate": f["fundingRate"],
        })
    df = pd.DataFrame(rows).sort_values("timestamp").drop_duplicates(subset="timestamp")
    logger.info(f"[bootstrap] Funding: {len(df)} rates from {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}")
    return df


def fetch_latest_ohlcv(exchange, symbol_ccxt, since_ms, limit=10):
    ohlcv = exchange.fetch_ohlcv(symbol_ccxt, "1h", since=since_ms, limit=limit)
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


def fetch_latest_funding(exchange, symbol_ccxt, since_ms):
    try:
        funding = exchange.fetch_funding_rate_history(symbol_ccxt, since=since_ms, limit=50)
        rows = []
        for f in funding:
            rows.append({
                "timestamp": pd.to_datetime(f["timestamp"], unit="ms"),
                "fundingRate": f["fundingRate"],
            })
        return pd.DataFrame(rows) if rows else None
    except Exception as e:
        logger.warning(f"[funding] fetch failed: {e}")
        return None


def compute_latest_features(ohlcv_buffer, funding_buffer):
    features = compute_base_ta(ohlcv_buffer)
    mtf = compute_mtf_features(ohlcv_buffer, ("4h", "1d"))
    fund = compute_funding_features(ohlcv_buffer, funding_buffer)
    combined = pd.concat([features, mtf, fund], axis=1)
    combined = combined.bfill().ffill().fillna(0.0).astype(np.float64)
    return combined.iloc[-1:], combined.columns.tolist()


def main():
    parser = argparse.ArgumentParser(description="Forward test on Binance Futures Demo")
    parser.add_argument("--symbol", default="BTC", choices=["BTC", "ETH", "SOL"])
    parser.add_argument("--interval", type=int, default=60, help="Poll interval in seconds (default: 60)")
    parser.add_argument("--dry-run", action="store_true", help="Print signals without placing orders")
    parser.add_argument("--max-cycles", type=int, default=0, help="Max poll cycles (0 = infinite)")
    args = parser.parse_args()

    symbol = args.symbol.upper()
    symbol_ccxt = f"{symbol}/USDT"

    logger.info("=" * 60)
    logger.info(f"Fase B — Forward Test ({symbol})")
    logger.info(f"  Interval: {args.interval}s  Dry-run: {args.dry_run}  Max cycles: {args.max_cycles or 'infinite'}")
    logger.info(f"  Thresholds: BUY>{THRESHOLDS['BUY']} SELL<{THRESHOLDS['SELL']}")
    logger.info(f"  RT cost: {RT_COST:.4f}")
    logger.info("=" * 60)

    model, scaler, n_features = load_model(symbol)
    exchange = create_exchange()

    exchange.load_markets()
    ccxt_symbol = symbol_ccxt
    if ccxt_symbol not in exchange.markets:
        for s in exchange.markets:
            if symbol in s and "USDT" in s:
                ccxt_symbol = s
                break
    logger.info(f"[exchange] {exchange.id}  market: {ccxt_symbol}")

    def _closest_closed_hour() -> datetime:
        """Return the last fully closed 1h UTC bar timestamp (tz-naive)."""
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        return (now - timedelta(hours=1)).replace(tzinfo=None)

    ohlcv_buffer = bootstrap_ohlcv(exchange, ccxt_symbol)
    funding_buffer = bootstrap_funding(exchange, ccxt_symbol)

    max_init_ts = _closest_closed_hour()
    ohlcv_buffer = ohlcv_buffer[ohlcv_buffer["timestamp"] <= max_init_ts].reset_index(drop=True)

    last_bar_close = ohlcv_buffer["timestamp"].iloc[-1]
    position = None
    trade_count = 0
    cycle = 0
    log_path = Path(_project_root) / "logs" / f"forward_test_{symbol.lower()}.csv"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with open(log_path, "w") as log_f:
        log_f.write("timestamp,signal,y_proba,action,price,qty,pnl,position_side,cycle\n")

    logger.info(f"[log] writing to {log_path}")

    while True:
        try:
            cycle += 1
            if args.max_cycles and cycle > args.max_cycles:
                break

            max_bar_ts = _closest_closed_hour()
            since_ms = int(last_bar_close.timestamp() * 1000) - 3600 * 1000
            new_bars = fetch_latest_ohlcv(exchange, ccxt_symbol, since_ms)

            if len(new_bars) < 2:
                time.sleep(args.interval)
                continue

            # Only use bars whose timestamp ≤ last closed hour
            valid = new_bars[new_bars["timestamp"] <= max_bar_ts]
            if len(valid) == 0:
                time.sleep(args.interval)
                continue

            new_bar = valid.iloc[-1]
            latest_ts = new_bar["timestamp"]

            if latest_ts <= last_bar_close:
                time.sleep(args.interval)
                continue

            new_bars_to_add = new_bars[new_bars["timestamp"] > last_bar_close]
            ohlcv_buffer = pd.concat([ohlcv_buffer, new_bars_to_add], ignore_index=True)
            if len(ohlcv_buffer) > BUFFER_HOURS + 100:
                ohlcv_buffer = ohlcv_buffer.iloc[-(BUFFER_HOURS + 100):].reset_index(drop=True)

            latest_funding = fetch_latest_funding(exchange, ccxt_symbol, since_ms=int(last_bar_close.timestamp() * 1000))
            if latest_funding is not None and len(latest_funding) > 0:
                old_len = len(funding_buffer)
                funding_buffer = pd.concat(
                    [funding_buffer, latest_funding], ignore_index=True
                ).drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
            if len(funding_buffer) > FUNDING_BUFFER + 50:
                funding_buffer = funding_buffer.iloc[-(FUNDING_BUFFER + 50):].reset_index(drop=True)

            X_row, fnames = compute_latest_features(ohlcv_buffer, funding_buffer)
            if len(X_row) == 0 or X_row.isna().all(axis=None):
                last_bar_close = latest_ts
                time.sleep(args.interval)
                continue

            X_scaled = scaler.transform(X_row.values.reshape(1, -1))
            y_proba = model.predict_proba(X_scaled)[0, 1]

            current_time = latest_ts.strftime("%Y-%m-%d %H:%M:%S")
            bar_close = float(new_bar["close"])

            signal = "HOLD"
            if y_proba > THRESHOLDS["BUY"]:
                signal = "BUY"
            elif y_proba < THRESHOLDS["SELL"]:
                signal = "SELL"

            pnl = 0.0
            action = "NONE"

            # ── Close or flip existing position ─────────────────────
            if position is not None:
                bars_held = (latest_ts - position["entry_time"]) / timedelta(hours=1)
                close_side = "sell" if position["side"] == "BUY" else "buy"
                old_side = position["side"]

                should_close = bars_held >= LOOKAHEAD
                should_flip = signal != "HOLD" and signal != old_side

                if should_close or should_flip:
                    action = "CLOSE (time)" if should_close else "FLIP"
                    if not args.dry_run:
                        close_order = exchange.create_order(
                            ccxt_symbol, "MARKET", close_side, position["qty"],
                        )
                        exit_price = float(close_order.get("price", bar_close))
                    else:
                        exit_price = bar_close

                    pnl = (exit_price - position["entry_price"]) * position["qty"] * \
                          (1 if old_side == "BUY" else -1)
                    gross_pnl = pnl
                    pnl -= abs(position["qty"] * exit_price) * TRADING_FEE

                    logger.info(f"  {action} [{old_side}] entry={position['entry_price']:.2f} "
                                f"exit={exit_price:.2f} gross_pnl={gross_pnl:.2f} net_pnl={pnl:.2f}")
                    trade_count += 1
                    position = None

            # ── Open new position ───────────────────────────────────
            if position is None and signal != "HOLD":
                action = "OPEN"
                if not args.dry_run:
                    balance = exchange.fetch_balance()
                    usdt_bal = float(balance.get("USDT", {}).get("free", 0))
                    qty_f = max(0.001, usdt_bal * 0.95 / bar_close)
                    qty = exchange.amount_to_precision(ccxt_symbol, qty_f)
                    order = exchange.create_order(
                        ccxt_symbol, "MARKET", "buy" if signal == "BUY" else "sell", qty,
                    )
                    entry_price = float(order.get("price", bar_close))
                    logger.info(f"  OPEN {signal} qty={qty} price={entry_price:.2f}")
                else:
                    qty = exchange.amount_to_precision(ccxt_symbol, 0.001) if not args.dry_run else "0.001"
                    entry_price = bar_close
                position = {
                    "side": signal, "entry_price": entry_price,
                    "qty": qty, "entry_time": latest_ts,
                }

            log_line = (
                f"{current_time},{signal},{y_proba:.6f},{action},{bar_close:.2f},"
                f"{position['qty'] if position else 0},{pnl:.2f},"
                f"{position['side'] if position else 'NONE'},{cycle}\n"
            )
            with open(log_path, "a") as log_f:
                log_f.write(log_line)

            logger.info(f"[{current_time}] y_proba={y_proba:.4f} signal={signal} action={action} "
                        f"price={bar_close:.2f} pos={'NONE' if position is None else position['side']}")

            last_bar_close = latest_ts

            if cycle % 30 == 0:
                logger.info(f"[heartbeat] cycle={cycle} position={'NONE' if position is None else position['side']} "
                            f"trades={trade_count} last_bar={last_bar_close}")

            time.sleep(args.interval)

        except KeyboardInterrupt:
            logger.info("Shutdown requested.")
            break
        except Exception as e:
            logger.error(f"Cycle {cycle} error: {e}")
            logger.error(traceback.format_exc())
            time.sleep(args.interval * 2)

    logger.info(f"\nForward test terminated after {cycle} cycles, {trade_count} trades.")
    if position is not None and not args.dry_run:
        logger.info("Closing final position...")
        try:
            exchange.create_order(
                ccxt_symbol, "MARKET", "sell" if position["side"] == "BUY" else "buy",
                position["qty"],
            )
        except Exception as e:
            logger.error(f"Final close failed: {e}")


if __name__ == "__main__":
    main()
