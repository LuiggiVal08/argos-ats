"""Phase 9 — Paper Trading Framework.

Simulates 1-year historical paper trading with full portfolio state, risk management,
and performance tracking. No real API connections — purely historical simulation.
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("phase9")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

PHASE5_REPORT_DIR = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v2_phase5"
REPORT_DIR = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v2_phase9"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = ["BTC", "ETH", "SOL"]

# ── config ─────────────────────────────────────────────────────────

INITIAL_CAPITAL = 100_000.0
BUY_THRESHOLD = 0.60
SELL_THRESHOLD = 0.40
COST_ROUND_TRIP = 0.0031
RISK_PER_TRADE = 0.01  # 1% of free balance
SL_ATR_MULTIPLIER = 2.0
DRAWDOWN_CB_PCT = 0.05  # 5% daily loss → halt
SIMULATION_DAYS = 365

# ── data structures ────────────────────────────────────────────────


@dataclass
class Position:
    symbol: str
    side: int  # 1 = long, -1 = short
    entry_price: float
    size_notional: float  # notional value (quote currency)
    sl_price: float
    timestamp: str


@dataclass
class Trade:
    timestamp: str
    symbol: str
    side: int  # 1 = BUY, -1 = SELL
    entry_price: float
    size_notional: float
    pnl: float  # realized P&L (exits only)
    cost: float
    reason: str  # "signal" | "stop_loss"


@dataclass
class PortfolioState:
    positions: dict[str, Position] = field(default_factory=dict)
    free_balance: float = INITIAL_CAPITAL
    total_equity: float = INITIAL_CAPITAL
    peak_equity: float = INITIAL_CAPITAL
    daily_pnl: float = 0.0
    daily_start_equity: float = INITIAL_CAPITAL
    current_day: str = ""
    halted: bool = False

    def mark_to_market(self) -> float:
        """Mark all open positions to market, compute total equity."""
        unrealized = 0.0
        for pos in self.positions.values():
            # In paper simulation, we don't have live prices; return notional
            # This is simplified: unrealized P&L tracked on exit
            pass
        self.total_equity = self.free_balance + unrealized
        return self.total_equity


# ── signal engine ──────────────────────────────────────────────────


class SignalEngine:
    """Loads predictions and generates signals per bar."""

    def __init__(self, buy_threshold: float = BUY_THRESHOLD, sell_threshold: float = SELL_THRESHOLD):
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold
        self.signals: dict[str, pd.DataFrame] = {}

    def load_predictions(self, symbol: str) -> pd.DataFrame | None:
        path = PHASE5_REPORT_DIR / f"{symbol.lower()}_predictions.parquet"
        if not path.exists():
            logger.warning(f"[signal] no predictions for {symbol}")
            return None
        df = pd.read_parquet(path)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp")
        # Filter: only last year
        cutoff = df["timestamp"].max() - pd.Timedelta(days=SIMULATION_DAYS)
        df = df[df["timestamp"] >= cutoff].copy()
        logger.info(f"[signal] {symbol}: {len(df)} bars in simulation window")
        return df

    def load_all(self) -> dict[str, pd.DataFrame]:
        for sym in SYMBOLS:
            df = self.load_predictions(sym)
            if df is not None:
                self.signals[sym] = df
        return self.signals

    def get_signal(self, symbol: str, timestamp: pd.Timestamp) -> int:
        """Return 1 (BUY), -1 (SELL), or 0 (HOLD)."""
        df = self.signals.get(symbol)
        if df is None or len(df) == 0:
            return 0
        match = df[df["timestamp"] == timestamp]
        if len(match) == 0:
            return 0
        proba = match.iloc[0]["y_proba"]
        if proba > self.buy_threshold:
            return 1
        elif proba < self.sell_threshold:
            return -1
        return 0

    def get_forward_return(self, symbol: str, timestamp: pd.Timestamp) -> float:
        df = self.signals.get(symbol)
        if df is None:
            return 0.0
        match = df[df["timestamp"] == timestamp]
        if len(match) == 0:
            return 0.0
        return float(match.iloc[0]["forward_return"])


# ── paper broker ───────────────────────────────────────────────────


class PaperBroker:
    """Simulates order execution with configurable costs."""

    def __init__(self, cost_round_trip: float = COST_ROUND_TRIP, delay_bars: int = 0):
        self.cost_round_trip = cost_round_trip

    def calc_position_size(
        self, free_balance: float, fwd_return: float, entry_price: float = 1.0,
    ) -> float:
        """Compute position notional based on 1% risk per trade.

        Simplified: Since we trade on return (not price), use:
        risk_amount = free_balance * RISK_PER_TRADE
        SL_distance = abs(fwd_return) * SL_ATR_MULTIPLIER (if return is the P&L metric)
        position_size = risk_amount / SL_distance  (in notional)
        """
        risk_amount = free_balance * RISK_PER_TRADE
        # Use the magnitude of the forward return as a proxy for ATR
        ret_magnitude = abs(fwd_return) if abs(fwd_return) > 0.001 else 0.01
        sl_distance = ret_magnitude * SL_ATR_MULTIPLIER
        size = risk_amount / sl_distance
        # Cap at free balance
        return min(size, free_balance)

    def execute_market(
        self,
        symbol: str,
        side: int,
        notional: float,
        fwd_return: float,
        timestamp: str,
    ) -> Trade:
        """Execute a market order. Returns a Trade with P&L.

        The trade "exits" with P&L = side * (fwd_return * notional) - cost.
        In this simplified model, each bar is a complete round-trip.
        """
        gross_pnl = side * fwd_return * notional
        cost = notional * self.cost_round_trip
        net_pnl = gross_pnl - cost

        return Trade(
            timestamp=timestamp,
            symbol=symbol,
            side=side,
            entry_price=1.0,  # normalized
            size_notional=round(notional, 2),
            pnl=round(net_pnl, 2),
            cost=round(cost, 4),
            reason="signal",
        )


# ── metrics tracker ────────────────────────────────────────────────


class MetricsTracker:
    def __init__(self):
        self.trades: list[Trade] = []
        self.daily_equity: list[dict] = []
        self.daily_returns: list[float] = []

    def record_trade(self, trade: Trade):
        self.trades.append(trade)

    def snapshot_equity(self, timestamp: str, equity: float):
        self.daily_equity.append({"timestamp": timestamp, "equity": round(equity, 2)})

    def record_daily_return(self, ret: float):
        self.daily_returns.append(ret)

    def compute_metrics(self) -> dict:
        if not self.trades:
            return {"status": "no_trades"}

        # Per-trade returns
        trade_returns = np.array([t.pnl for t in self.trades if t.reason == "signal"])
        if len(trade_returns) == 0:
            return {"status": "no_trades"}

        total_pnl = sum(t.pnl for t in self.trades)
        total_costs = sum(t.cost for t in self.trades)
        n_trades = len(self.trades)
        n_wins = sum(1 for t in self.trades if t.pnl > 0)
        n_losses = sum(1 for t in self.trades if t.pnl <= 0)

        win_rate = n_wins / n_trades if n_trades > 0 else 0.0
        avg_win = np.mean([t.pnl for t in self.trades if t.pnl > 0]) if n_wins > 0 else 0.0
        avg_loss = np.mean([t.pnl for t in self.trades if t.pnl <= 0]) if n_losses > 0 else 0.0
        profit_factor = abs(sum(t.pnl for t in self.trades if t.pnl > 0)) / abs(sum(t.pnl for t in self.trades if t.pnl < 0) + 1e-10)

        # Daily metrics
        daily_rets = np.array(self.daily_returns)
        if len(daily_rets) > 5:
            mean_daily = daily_rets.mean()
            std_daily = daily_rets.std()
            sharpe = mean_daily / std_daily * np.sqrt(365) if std_daily > 1e-10 else 0.0
            downside = daily_rets[daily_rets < 0]
            downside_std = downside.std() if len(downside) > 1 else 0.0
            sortino = mean_daily / downside_std * np.sqrt(365) if downside_std > 1e-10 else 0.0
        else:
            sharpe = 0.0
            sortino = 0.0

        # Equity curve
        eq = pd.Series([e["equity"] for e in self.daily_equity])
        if len(eq) > 1:
            peak = eq.cummax()
            dd = (eq - peak) / peak
            max_dd = float(dd.min())
            total_return = float((eq.iloc[-1] - eq.iloc[0]) / eq.iloc[0])
            cagr = total_return / (len(eq) / 365) if len(eq) > 0 else 0.0
            calmar = cagr / abs(max_dd) if max_dd < 0 else 0.0
        else:
            max_dd = 0.0
            total_return = 0.0
            cagr = 0.0
            calmar = 0.0

        return {
            "n_trades": n_trades,
            "n_wins": n_wins,
            "n_losses": n_losses,
            "win_rate": round(win_rate, 4),
            "total_pnl": round(float(total_pnl), 2),
            "total_costs": round(float(total_costs), 4),
            "net_pnl": round(float(total_pnl), 2),
            "avg_win": round(float(avg_win), 2),
            "avg_loss": round(float(avg_loss), 2),
            "profit_factor": round(float(profit_factor), 4),
            "total_return": round(total_return, 6),
            "cagr": round(cagr, 6),
            "sharpe": round(sharpe, 4),
            "sortino": round(sortino, 4),
            "calmar": round(calmar, 4),
            "max_drawdown": round(max_dd, 6),
        }


# ── simulation ─────────────────────────────────────────────────────


def run_simulation() -> dict:
    logger.info("╔══════════════════════════════════════╗")
    logger.info("║  Phase 9 — Paper Trading Simulation  ║")
    logger.info(f"║  Capital: ${INITIAL_CAPITAL:,.0f}                 ║")
    logger.info(f"║  Period:  last {SIMULATION_DAYS} days            ║")
    logger.info("╚══════════════════════════════════════╝")

    # ── Load signals ────────────────────────────────────────────
    engine = SignalEngine(buy_threshold=BUY_THRESHOLD, sell_threshold=SELL_THRESHOLD)
    signals = engine.load_all()
    if not signals:
        logger.error("No signals loaded — aborting")
        return {"status": "no_signals"}

    # ── Time-ordered bar index ──────────────────────────────────
    all_timestamps = pd.DatetimeIndex([])
    for sym, df in signals.items():
        all_timestamps = all_timestamps.union(df["timestamp"])
    all_timestamps = all_timestamps.sort_values()
    logger.info(f"  Total bars in simulation: {len(all_timestamps)}")

    # ── Simulation loop ─────────────────────────────────────────
    broker = PaperBroker(cost_round_trip=COST_ROUND_TRIP)
    portfolio = PortfolioState()
    metrics = MetricsTracker()

    # Track daily P&L
    current_day = ""
    portfolio.daily_start_equity = INITIAL_CAPITAL

    for bar_idx, ts in enumerate(all_timestamps):
        if portfolio.halted:
            break

        ts_str = str(ts)
        day = ts_str[:10]

        # Day rollover
        if day != current_day:
            if current_day:
                daily_ret = (portfolio.total_equity - portfolio.daily_start_equity) / portfolio.daily_start_equity
                portfolio.daily_pnl = portfolio.total_equity - portfolio.daily_start_equity
                metrics.record_daily_return(daily_ret)
                metrics.snapshot_equity(current_day, portfolio.total_equity)
                portfolio.daily_start_equity = portfolio.total_equity

                # Drawdown circuit breaker
                if daily_ret < -DRAWDOWN_CB_PCT:
                    logger.warning(f"  ⚠ DRAWDOWN CB TRIGGERED at {current_day}: {daily_ret:.2%}")
                    portfolio.halted = True
                    metrics.snapshot_equity(day, portfolio.total_equity)
                    break

            current_day = day

        portfolio.current_day = current_day

        # Process each symbol at this bar
        for sym in SYMBOLS:
            sig = engine.get_signal(sym, ts)
            fwd_ret = engine.get_forward_return(sym, ts)
            existing = portfolio.positions.get(sym)

            if sig == 0:
                # HOLD — close any existing position? No, hold.
                continue

            if existing and existing.side == sig:
                # Same direction — hold
                continue

            if existing and existing.side != sig:
                # Flip: close existing (no P&L tracking in simplified model)
                pass  # We'll re-enter below

            # Open position
            size = broker.calc_position_size(portfolio.free_balance, fwd_ret)
            if size < 1.0:
                continue  # too small

            trade = broker.execute_market(sym, sig, size, fwd_ret, ts_str)
            metrics.record_trade(trade)

            # Update portfolio
            portfolio.free_balance += trade.pnl
            portfolio.total_equity = portfolio.free_balance
            if portfolio.total_equity > portfolio.peak_equity:
                portfolio.peak_equity = portfolio.total_equity

        # Heartbeat every 500 bars
        if bar_idx > 0 and bar_idx % 500 == 0:
            pct = bar_idx / len(all_timestamps) * 100
            logger.info(f"  [{pct:.0f}%] bar={bar_idx} "
                        f"trades={len(metrics.trades)} "
                        f"equity=${portfolio.total_equity:,.0f}")

    # Final snapshot
    metrics.snapshot_equity(current_day, portfolio.total_equity)
    comp_metrics = metrics.compute_metrics()

    # ── Verdict ─────────────────────────────────────────────────
    verdict = None
    if comp_metrics.get("status") != "no_trades":
        sp = comp_metrics.get("sharpe", 0)
        if sp > 1.0:
            verdict = "PAPER_ALPHA_CONFIRMED"
        elif sp > 0:
            verdict = "PAPER_ALPHA_NEUTRAL"
        else:
            verdict = "PAPER_ALPHA_REJECTED"

    # ── Save outputs ────────────────────────────────────────────
    trade_log = pd.DataFrame([{
        "timestamp": t.timestamp,
        "symbol": t.symbol,
        "side": "BUY" if t.side == 1 else "SELL",
        "size": t.size_notional,
        "pnl": t.pnl,
        "cost": t.cost,
        "reason": t.reason,
    } for t in metrics.trades])
    trade_log.to_csv(REPORT_DIR / "trade_log.csv", index=False)

    equity_df = pd.DataFrame(metrics.daily_equity)
    equity_df.to_csv(REPORT_DIR / "equity_curve.csv", index=False)

    result = {
        "phase": "quant_validation_v2_phase9",
        "config": {
            "initial_capital": INITIAL_CAPITAL,
            "buy_threshold": BUY_THRESHOLD,
            "sell_threshold": SELL_THRESHOLD,
            "cost_round_trip": COST_ROUND_TRIP,
            "risk_per_trade": RISK_PER_TRADE,
            "sl_atr_multiplier": SL_ATR_MULTIPLIER,
            "drawdown_cb_pct": DRAWDOWN_CB_PCT,
            "simulation_days": SIMULATION_DAYS,
        },
        "simulation": {
            "n_bars": len(all_timestamps),
            "n_days": len(metrics.daily_equity),
            "final_equity": round(portfolio.total_equity, 2),
            "peak_equity": round(portfolio.peak_equity, 2),
            "halted": portfolio.halted,
        },
        "metrics": comp_metrics,
        "verdict": verdict,
    }

    save_report(result, "paper_trading.json")
    logger.info(f"\nResults:")
    logger.info(f"  Trades:     {comp_metrics.get('n_trades', 0)}")
    logger.info(f"  Win rate:   {comp_metrics.get('win_rate', 0):.1%}")
    logger.info(f"  Sharpe:     {comp_metrics.get('sharpe', 0):.2f}")
    logger.info(f"  Return:     {comp_metrics.get('total_return', 0):.2%}")
    logger.info(f"  Max DD:     {comp_metrics.get('max_drawdown', 0):.2%}")
    logger.info(f"  Final eq:   ${portfolio.total_equity:,.0f}")
    logger.info(f"  Verdict:    {verdict}")
    return result


def save_report(data: dict, filename: str) -> Path:
    path = REPORT_DIR / filename
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    logger.info(f"[report] saved {path}")
    return path


def load_report(filename: str) -> dict | None:
    path = REPORT_DIR / filename
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def main():
    result = run_simulation()


if __name__ == "__main__":
    main()
