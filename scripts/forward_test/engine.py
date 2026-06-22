"""STEP 2 — Forward Test Stabilization Layer.

Core loop with clean buffer management, closed-candle gating,
implicit-future-free feature computation, and position management.
"""

from __future__ import annotations

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

_project_root = Path(__file__).parent.parent.parent.resolve()
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from experiments.quant_validation_v2.common import (
    compute_base_ta,
    compute_mtf_features,
    compute_funding_features,
)

from scripts.forward_test.config import (
    BUFFER_HOURS,
    FUNDING_BUFFER,
    TRADING_FEE,
    RT_COST,
)
from scripts.forward_test.policy import PolicyV1
from scripts.forward_test.regime import classify as classify_regime
from scripts.forward_test.metrics import MetricsTracker
from scripts.forward_test.watchdog import Watchdog

logger = logging.getLogger("forward_test.engine")


def _closest_closed_hour() -> datetime:
    """Last fully closed 1h UTC bar (tz-naive)."""
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return (now - timedelta(hours=1)).replace(tzinfo=None)


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


def load_model_package(symbol: str):
    model_dir = _project_root / "models" / symbol.lower()
    with open(model_dir / "model.pkl", "rb") as f:
        model = pickle.load(f)
    with open(model_dir / "scaler.pkl", "rb") as f:
        scaler = pickle.load(f)
    with open(model_dir / "metadata.json") as f:
        meta = json.load(f)
    n_features = meta.get("features", 53)
    logger.info(f"[model] {model.__class__.__name__}  {n_features} features  accuracy={meta.get('training_accuracy', '?')}")
    return model, scaler, n_features


# ── Data helpers ──────────────────────────────────────────────────


def bootstrap_ohlcv(exchange, ccxt_symbol, limit=1000):
    ohlcv = exchange.fetch_ohlcv(ccxt_symbol, "1h", limit=limit)
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    logger.info(f"[bootstrap] OHLCV: {len(df)} bars  {df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}")
    return df


def bootstrap_funding(exchange, ccxt_symbol, limit=500):
    funding = exchange.fetch_funding_rate_history(ccxt_symbol, limit=limit)
    rows = [{
        "timestamp": pd.to_datetime(f["timestamp"], unit="ms"),
        "fundingRate": f["fundingRate"],
    } for f in funding]
    df = pd.DataFrame(rows).sort_values("timestamp").drop_duplicates(subset="timestamp")
    logger.info(f"[bootstrap] Funding: {len(df)} rates  {df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}")
    return df


def fetch_new_bars(exchange, ccxt_symbol, since_ms, limit=10) -> pd.DataFrame:
    ohlcv = exchange.fetch_ohlcv(ccxt_symbol, "1h", since=since_ms, limit=limit)
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


def fetch_new_funding(exchange, ccxt_symbol, since_ms) -> pd.DataFrame | None:
    try:
        funding = exchange.fetch_funding_rate_history(ccxt_symbol, since=since_ms, limit=50)
        if not funding:
            return None
        rows = [{
            "timestamp": pd.to_datetime(f["timestamp"], unit="ms"),
            "fundingRate": f["fundingRate"],
        } for f in funding]
        return pd.DataFrame(rows)
    except Exception as e:
        logger.warning(f"[funding] fetch failed: {e}")
        return None


def compute_features(ohlcv: pd.DataFrame, funding: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    base = compute_base_ta(ohlcv)
    mtf = compute_mtf_features(ohlcv, ("4h", "1d"))
    fund = compute_funding_features(ohlcv, funding)
    combined = pd.concat([base, mtf, fund], axis=1)
    combined = combined.bfill().ffill().fillna(0.0).astype(np.float64)
    return combined, combined.columns.tolist()


# ── Position management ──────────────────────────────────────────


def build_position(side: str, entry_price: float, qty: float, timestamp: datetime) -> dict:
    return {"side": side, "entry_price": entry_price, "qty": qty, "entry_time": timestamp}


def bars_held(position: dict, current_time: datetime) -> float:
    return (current_time - position["entry_time"]) / timedelta(hours=1)


# ── Main engine ───────────────────────────────────────────────────


class ForwardTestEngine:
    """Institutional-grade forward test engine.

    Owns buffers, model, exchange connection, metrics, and watchdog.
    Single responsibility: execute the policy faithfully.
    """

    def __init__(
        self,
        symbol: str,
        policy: PolicyV1,
        dry_run: bool = True,
        poll_interval: int = 60,
        log_dir: Path | None = None,
    ):
        self.symbol = symbol.upper()
        self.ccxt_symbol = f"{self.symbol}/USDT"
        self.policy = policy
        self.dry_run = dry_run
        self.poll_interval = poll_interval
        self.log_dir = log_dir or (_project_root / "logs" / "forward_test")
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Components
        self.exchange = None
        self.model = None
        self.scaler = None
        self.metrics = MetricsTracker(dry_run=dry_run)
        self.watchdog = Watchdog(dry_run=dry_run)

        # Buffers
        self.ohlcv_buffer: pd.DataFrame | None = None
        self.funding_buffer: pd.DataFrame | None = None
        self.last_bar_close: datetime | None = None
        self.last_bar_features: dict | None = None

        # Position state
        self.position: dict | None = None
        self.trade_count = 0
        self.cycle = 0

        # CSV log
        self.csv_path = self.log_dir / f"live_{self.symbol.lower()}.csv"

    # ── Initialization ──────────────────────────────────────────────

    def init(self) -> None:
        model_dir = _project_root / "models" / self.symbol.lower()
        if not (model_dir / "model.pkl").exists():
            raise FileNotFoundError(f"No model found at {model_dir}. Run train_production_models.py first.")

        self.model, self.scaler, _ = load_model_package(self.symbol)
        self.exchange = create_exchange()
        self.exchange.load_markets()

        if self.ccxt_symbol not in self.exchange.markets:
            for s in self.exchange.markets:
                if self.symbol in s and "USDT" in s:
                    self.ccxt_symbol = s
                    break
        logger.info(f"[exchange] {self.exchange.id} → {self.ccxt_symbol}")

        # Bootstrap buffers
        max_init = _closest_closed_hour()
        self.ohlcv_buffer = bootstrap_ohlcv(self.exchange, self.ccxt_symbol)
        self.ohlcv_buffer = self.ohlcv_buffer[self.ohlcv_buffer["timestamp"] <= max_init].reset_index(drop=True)
        self.funding_buffer = bootstrap_funding(self.exchange, self.ccxt_symbol)
        self.last_bar_close = self.ohlcv_buffer["timestamp"].iloc[-1]

        # Write CSV header
        with open(self.csv_path, "w") as f:
            f.write("timestamp,signal,y_proba,action,price,qty,pnl,position_side,cycle,latency_ms,regime\n")

        logger.info(f"[init] buffers ready  last_bar={self.last_bar_close}")

    # ── Per-cycle execution ─────────────────────────────────────────

    def _execute_cycle(self) -> None:
        t_start = time.perf_counter()

        # ── 1. Fetch data ───────────────────────────────────────────
        max_bar_ts = _closest_closed_hour()
        since_ms = int(self.last_bar_close.timestamp() * 1000) - 3600 * 1000
        new_bars = fetch_new_bars(self.exchange, self.ccxt_symbol, since_ms)
        feed_ok = len(new_bars) >= 2
        self.watchdog.check_data_feed(feed_ok)
        if not feed_ok:
            time.sleep(self.poll_interval)
            return
        if self.watchdog.frozen:
            return

        # Time-gate: only closed candles
        valid = new_bars[new_bars["timestamp"] <= max_bar_ts]
        if len(valid) == 0:
            self.metrics.record_latency(self.cycle, "poll", (time.perf_counter() - t_start) * 1000)
            time.sleep(self.poll_interval)
            return

        new_bar = valid.iloc[-1]
        latest_ts = new_bar["timestamp"]
        if latest_ts <= self.last_bar_close:
            self.metrics.record_latency(self.cycle, "poll", (time.perf_counter() - t_start) * 1000)
            time.sleep(self.poll_interval)
            return

        t1 = time.perf_counter()

        # ── 2. Update buffers ───────────────────────────────────────
        new_rows = new_bars[new_bars["timestamp"] > self.last_bar_close]
        self.ohlcv_buffer = pd.concat([self.ohlcv_buffer, new_rows], ignore_index=True)
        if len(self.ohlcv_buffer) > BUFFER_HOURS + 100:
            self.ohlcv_buffer = self.ohlcv_buffer.iloc[-(BUFFER_HOURS + 100):].reset_index(drop=True)

        latest_funding = fetch_new_funding(self.exchange, self.ccxt_symbol,
                                           since_ms=int(self.last_bar_close.timestamp() * 1000))
        if latest_funding is not None and len(latest_funding) > 0:
            self.funding_buffer = pd.concat(
                [self.funding_buffer, latest_funding], ignore_index=True
            ).drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
        if len(self.funding_buffer) > FUNDING_BUFFER + 50:
            self.funding_buffer = self.funding_buffer.iloc[-(FUNDING_BUFFER + 50):].reset_index(drop=True)

        t2 = time.perf_counter()

        # ── 3. Compute features ────────────────────────────────────
        combined, feature_names = compute_features(self.ohlcv_buffer, self.funding_buffer)
        if len(combined) == 0 or combined.isna().all(axis=None):
            logger.warning("[features] all NaN — skipping cycle")
            self.last_bar_close = latest_ts
            self.watchdog.check_data_feed(False)
            time.sleep(self.poll_interval)
            return

        X_row = combined.iloc[-1:]
        self.watchdog.check_features(X_row.values)
        if self.watchdog.frozen:
            return

        # Extract feature dict for regime + metrics
        feature_dict = dict(zip(feature_names, X_row.iloc[0].values))
        regime = classify_regime(feature_dict)
        self.last_bar_features = feature_dict

        t3 = time.perf_counter()

        # ── 4. Predict ──────────────────────────────────────────────
        X_scaled = self.scaler.transform(X_row.values.reshape(1, -1))
        y_proba = float(self.model.predict_proba(X_scaled)[0, 1])
        self.watchdog.check_prediction(y_proba)
        if self.watchdog.frozen:
            return
        signal = self.policy.get_signal(y_proba)

        t4 = time.perf_counter()

        # ── 5. Execute — position management ────────────────────────
        bar_close = float(new_bar["close"])
        current_time_str = latest_ts.strftime("%Y-%m-%d %H:%M:%S")
        pnl = 0.0
        action = "NONE"
        qty = 0.0
        pos_side = "NONE"

        # Close / flip existing
        if self.position is not None:
            old_side = self.position["side"]
            b_held = bars_held(self.position, latest_ts)
            should_close = b_held >= self.policy.max_hold_bars
            should_flip = signal != "HOLD" and signal != old_side

            if should_close or should_flip:
                action = "CLOSE(time)" if should_close else "FLIP"
                close_side = "sell" if old_side == "BUY" else "buy"

                if not self.dry_run:
                    close_order = self.exchange.create_order(
                        self.ccxt_symbol, "MARKET", close_side, self.position["qty"],
                    )
                    exit_price = float(close_order.get("price", bar_close))
                else:
                    exit_price = bar_close

                gross_pnl = (exit_price - self.position["entry_price"]) * self.position["qty"] * \
                    (1 if old_side == "BUY" else -1)
                pnl = gross_pnl - abs(self.position["qty"] * exit_price) * TRADING_FEE

                self.metrics.record_trade(
                    entry_time=self.position["entry_time"].strftime("%Y-%m-%d %H:%M:%S"),
                    exit_time=current_time_str,
                    side=old_side,
                    entry_price=self.position["entry_price"],
                    exit_price=exit_price,
                    qty=self.position["qty"],
                    gross_pnl=gross_pnl,
                    net_pnl=pnl,
                    bars_held=int(b_held),
                    exit_reason="time" if should_close else "flip",
                    regime=regime,
                )

                logger.info(
                    f"  {action} [{old_side}] entry={self.position['entry_price']:.2f} "
                    f"exit={exit_price:.2f} bars={b_held:.0f} gross={gross_pnl:.2f} net={pnl:.2f}"
                )
                self.trade_count += 1
                self.position = None

        # Open new
        if self.position is None and signal != "HOLD":
            action = "OPEN"
            if not self.dry_run:
                balance = self.exchange.fetch_balance()
                usdt_bal = float(balance.get("USDT", {}).get("free", 0))
                qty_f = max(0.001, usdt_bal * 0.95 / bar_close)
                qty = self.exchange.amount_to_precision(self.ccxt_symbol, qty_f)
                order = self.exchange.create_order(
                    self.ccxt_symbol, "MARKET", "buy" if signal == "BUY" else "sell", qty,
                )
                entry_price = float(order.get("price", bar_close))
                logger.info(f"  OPEN {signal} qty={qty} price={entry_price:.2f}")
            else:
                qty = 0.001
                entry_price = bar_close
            self.position = build_position(signal, entry_price, qty, latest_ts)

        pos_side = self.position["side"] if self.position else "NONE"
        qty = float(self.position["qty"]) if self.position else 0.0

        t5 = time.perf_counter()

        # ── 6. Record & log ─────────────────────────────────────────
        self.watchdog.update_balance(0.0)  # Only in non-dry-run
        self.metrics.record_prediction(current_time_str, y_proba, signal, feature_dict, regime)
        self.metrics.record_latency(self.cycle, "fetch", (t1 - t_start) * 1000)
        self.metrics.record_latency(self.cycle, "buffer", (t2 - t1) * 1000)
        self.metrics.record_latency(self.cycle, "features", (t3 - t2) * 1000)
        self.metrics.record_latency(self.cycle, "predict", (t4 - t3) * 1000)
        self.metrics.record_latency(self.cycle, "execute", (t5 - t4) * 1000)
        total_ms = (t5 - t_start) * 1000

        log_line = (
            f"{current_time_str},{signal},{y_proba:.6f},{action},{bar_close:.2f},{qty:.6f},{pnl:.2f},"
            f"{pos_side},{self.cycle},{total_ms:.1f},{regime}\n"
        )
        with open(self.csv_path, "a") as f:
            f.write(log_line)

        logger.info(
            f"[{current_time_str}] y_proba={y_proba:.4f} signal={signal} action={action} "
            f"price={bar_close:.2f} pos={pos_side} cycle={self.cycle} ({total_ms:.0f}ms) regime={regime}"
        )

        self.last_bar_close = latest_ts

        if self.cycle % 30 == 0:
            logger.info(
                f"[heartbeat] cycle={self.cycle} trades={self.trade_count} "
                f"pos={pos_side} watchdog={self.watchdog.status()['drawdown']:.4f}"
            )

    # ── Run loop ────────────────────────────────────────────────────

    def run(self, max_cycles: int = 0) -> None:
        logger.info("=" * 60)
        logger.info(f"Forward Test  {self.symbol}  dry_run={self.dry_run}")
        logger.info(f"Policy: v{self.policy.version}  BUY>{self.policy.entry_threshold} SELL<{self.policy.exit_threshold}")
        logger.info(f"Costs: fee={TRADING_FEE}  RT={RT_COST:.4f}")
        logger.info(f"Log: {self.csv_path}")
        logger.info("=" * 60)

        while True:
            try:
                self.cycle += 1
                if max_cycles and self.cycle > max_cycles:
                    break
                self._execute_cycle()
                if self.watchdog.frozen:
                    logger.warning(f"Engine frozen: {self.watchdog.freeze_reason}")
                    break
                time.sleep(self.poll_interval)
            except KeyboardInterrupt:
                logger.info("Shutdown requested.")
                break
            except Exception as e:
                logger.error(f"Cycle {self.cycle} error: {e}")
                logger.error(traceback.format_exc())
                if self.watchdog.consecutive_failures >= self.watchdog.max_failures:
                    logger.critical("Max consecutive failures reached — halting engine.")
                    break
                time.sleep(self.poll_interval * 2)

        # Final cleanup
        logger.info(f"\n{'=' * 60}")
        logger.info(f"HALT  cycles={self.cycle} trades={self.trade_count} frozen={self.watchdog.frozen}")
        if self.position is not None and not self.dry_run:
            logger.info("Closing final position...")
            try:
                self.exchange.create_order(
                    self.ccxt_symbol, "MARKET",
                    "sell" if self.position["side"] == "BUY" else "buy",
                    self.position["qty"],
                )
            except Exception as e:
                logger.error(f"Final close failed: {e}")
                self.watchdog._freeze(f"Final close error: {e}")

        # Generate final report
        report = self.metrics.generate_daily_report(
            watchdog_status=self.watchdog.status(),
        )
        logger.info(f"Final report generated.")

    @property
    def summary(self) -> dict:
        return {
            "symbol": self.symbol,
            "cycles": self.cycle,
            "trades": self.trade_count,
            "frozen": self.watchdog.frozen,
            "freeze_reason": self.watchdog.freeze_reason,
        }
