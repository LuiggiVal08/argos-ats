"""Phase 11 — Event-Driven Backtester.

Bar-by-bar simulation over ALL 1h bars. Positions held 5 bars, MTM bar by bar,
flip on contrary signal, PnL with real prices (no forward_return shortcut).
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from experiments.quant_validation_v2_phase38.common import (
    load_ohlcv, load_funding,
)

logger = logging.getLogger("phase11")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

PHASE5_REPORT_DIR = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v2_phase5"
REPORT_DIR = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v2_phase11"
REPORT_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR = Path(__file__).parent.parent.parent / "apps" / "analytics-engine" / "data"

SYMBOLS = ["BTC", "ETH", "SOL"]
EXCHANGE = "binance"
INITIAL_CAPITAL = 100_000.0
BUY_THRESHOLD = 0.60
SELL_THRESHOLD = 0.40
COST_PER_SIDE = 0.00155  # fee 0.0010 + slippage 0.0005 + spread/2 0.00005
COST_ROUND_TRIP = COST_PER_SIDE * 2
LOOKAHEAD = 5
STRIDE = 5


def save_report(data: dict, filename: str) -> Path:
    path = REPORT_DIR / filename
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    logger.info(f"[report] saved {path}")
    return path


# ── Dataclasses ────────────────────────────────────────────────────


@dataclass
class TradeRecord:
    entry_bar: int
    entry_ts: str
    exit_bar: int | None
    exit_ts: str | None
    symbol: str
    side: int  # 1=LONG, -1=SHORT
    entry_price: float
    exit_price: float | None
    size: float
    gross_pnl: float
    entry_cost: float
    exit_cost: float
    net_pnl: float
    duration_bars: int | None
    exit_reason: str  # "signal" | "signal_flip"


@dataclass
class Position:
    symbol: str
    side: int
    entry_bar: int
    entry_price: float
    size: float
    entry_cost: float


# ── Signal loader ──────────────────────────────────────────────────


def load_signals() -> dict[str, dict[int, int]]:
    """Load predictions from Phase 5 and return {symbol: {bar_index: pred}}."""
    signals: dict[str, dict[int, int]] = {}
    for sym in SYMBOLS:
        path = PHASE5_REPORT_DIR / f"{sym.lower()}_predictions.parquet"
        df = pd.read_parquet(path)
        df["bar_idx"] = range(len(df))  # placeholder — actual bar index from data
        signals[sym] = {}
        for _, row in df.iterrows():
            ts = pd.Timestamp(row["timestamp"])
            if row["y_proba"] > BUY_THRESHOLD:
                signals[sym][ts] = 1
            elif row["y_proba"] < SELL_THRESHOLD:
                signals[sym][ts] = -1
            # else: HOLD = no signal
        logger.info(f"[signals] {sym}: {len(signals[sym])} non-HOLD predictions")
    return signals


# ── ATR calculator (lookback only) ─────────────────────────────────


def compute_atr20(ohlcv: pd.DataFrame) -> np.ndarray:
    """Compute ATR20 from OHLCV (uses only past data at each bar)."""
    high = ohlcv["high"].values.astype(float)
    low = ohlcv["low"].values.astype(float)
    close = ohlcv["close"].values.astype(float)
    tr = np.zeros(len(close))
    tr[0] = high[0] - low[0]
    for i in range(1, len(close)):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    atr = np.zeros(len(close))
    for i in range(20, len(close)):
        atr[i] = tr[i - 19:i + 1].mean()
    atr[:20] = atr[20] if len(close) > 20 else 0.0
    return atr


# ── Position size (simple fraction for Phase 11) ──────────────────


def calc_size(free_balance: float) -> float:
    """Simple equal-weight allocation: 33% of free balance."""
    return min(free_balance * 0.33, free_balance)


# ── Bar-by-bar simulation ─────────────────────────────────────────


def run_backtest() -> dict:
    logger.info("╔══════════════════════════════════════╗")
    logger.info("║  Phase 11 — Event-Driven Backtester   ║")
    logger.info(f"║  Capital: ${INITIAL_CAPITAL:,.0f}                  ║")
    logger.info("╚══════════════════════════════════════╝")
    t0 = time.time()

    # ── Load data ──────────────────────────────────────────────
    ohlcv_data: dict[str, pd.DataFrame] = {}
    close_data: dict[str, np.ndarray] = {}
    atr_data: dict[str, np.ndarray] = {}

    for sym in SYMBOLS:
        ohlcv = load_ohlcv(sym, EXCHANGE)
        ohlcv["timestamp"] = pd.to_datetime(ohlcv["timestamp"])
        ohlcv = ohlcv.sort_values("timestamp").reset_index(drop=True)
        ohlcv_data[sym] = ohlcv
        close_data[sym] = ohlcv["close"].values.astype(float)
        atr_data[sym] = compute_atr20(ohlcv)

    # ── Load signals ───────────────────────────────────────────
    raw_signals = load_signals()

    # Build per-symbol signal lookup: {timestamp: side}
    sig_lookup: dict[str, dict[pd.Timestamp, int]] = {}
    for sym in SYMBOLS:
        sig_lookup[sym] = {}
        path = PHASE5_REPORT_DIR / f"{sym.lower()}_predictions.parquet"
        df = pd.read_parquet(path)
        for _, row in df.iterrows():
            ts = pd.Timestamp(row["timestamp"])
            if row["y_proba"] > BUY_THRESHOLD:
                sig_lookup[sym][ts] = 1
            elif row["y_proba"] < SELL_THRESHOLD:
                sig_lookup[sym][ts] = -1

    # ── Build chronological bar index (union of all timestamps) ─
    all_ts = pd.DatetimeIndex([])
    for sym in SYMBOLS:
        all_ts = all_ts.union(ohlcv_data[sym]["timestamp"])
    all_ts = all_ts.sort_values()
    ts_to_idx: dict[pd.Timestamp, int] = {ts: i for i, ts in enumerate(all_ts)}
    logger.info(f"[bars] {len(all_ts)} unique 1h timestamps")

    # ── State ──────────────────────────────────────────────────
    positions: dict[str, Position | None] = {sym: None for sym in SYMBOLS}
    free_balance = INITIAL_CAPITAL
    trades: list[TradeRecord] = []
    equity_log: list[dict] = []
    last_heartbeat = time.time()

    # ── Bar loop ───────────────────────────────────────────────
    for bar_idx, bar_ts in enumerate(all_ts):
        if bar_idx > 0 and bar_idx % 5000 == 0:
            elapsed = time.time() - t0
            pct = bar_idx / len(all_ts) * 100
            logger.info(f"  [{pct:.0f}%] bar={bar_idx}/{len(all_ts)}  "
                        f"trades={len(trades)}  "
                        f"balance=${free_balance:,.0f}  "
                        f"elapsed={elapsed:.0f}s")
            last_heartbeat = time.time()

        # ── Check each symbol ──────────────────────────────────
        for sym in SYMBOLS:
            ohlcv = ohlcv_data[sym]
            close = close_data[sym]

            # Find OHLCV row for this timestamp
            ohlcv_mask = ohlcv["timestamp"] == bar_ts
            if not ohlcv_mask.any():
                continue  # no bar for this symbol at this timestamp
            ohlcv_idx = ohlcv_mask.idxmax()
            current_close = close[ohlcv_idx]

            has_signal = bar_ts in sig_lookup[sym]
            signal = sig_lookup[sym].get(bar_ts, 0)
            pos = positions[sym]

            # ── Flip: if position open AND opposite signal ──────
            if pos is not None and has_signal and signal != 0 and signal != pos.side:
                gross_return = current_close / pos.entry_price - 1.0
                gross_pnl = pos.size * (gross_return if pos.side == 1 else -gross_return)
                exit_cost = pos.size * COST_PER_SIDE
                net_pnl = gross_pnl - exit_cost
                free_balance += pos.size + pos.entry_cost + net_pnl  # return capital + PnL

                trades.append(TradeRecord(
                    entry_bar=pos.entry_bar,
                    entry_ts=str(ohlcv_data[sym].iloc[pos.entry_bar]["timestamp"]),
                    exit_bar=bar_idx,
                    exit_ts=str(bar_ts),
                    symbol=sym,
                    side=pos.side,
                    entry_price=pos.entry_price,
                    exit_price=current_close,
                    size=pos.size,
                    gross_pnl=round(gross_pnl, 2),
                    entry_cost=pos.entry_cost,
                    exit_cost=round(exit_cost, 4),
                    net_pnl=round(net_pnl, 2),
                    duration_bars=bar_idx - pos.entry_bar,
                    exit_reason="signal_flip",
                ))
                positions[sym] = None

            # ── Open new position ──────────────────────────────
            if has_signal and signal != 0 and positions[sym] is None:
                entry_price = current_close
                size = calc_size(free_balance)
                entry_cost = size * COST_PER_SIDE
                if size > 1.0:
                    positions[sym] = Position(
                        symbol=sym, side=signal,
                        entry_bar=bar_idx, entry_price=entry_price,
                        size=size, entry_cost=entry_cost,
                    )
                    free_balance -= (size + entry_cost)

            # ── Close if held 5 bars ───────────────────────────
            elif pos is not None and (bar_idx - pos.entry_bar) >= LOOKAHEAD:
                if not has_signal or signal == 0:
                    gross_return = current_close / pos.entry_price - 1.0
                    gross_pnl = pos.size * (gross_return if pos.side == 1 else -gross_return)
                    exit_cost = pos.size * COST_PER_SIDE
                    net_pnl = gross_pnl - exit_cost
                    free_balance += pos.size + pos.entry_cost + net_pnl

                    trades.append(TradeRecord(
                        entry_bar=pos.entry_bar,
                        entry_ts=str(ohlcv_data[sym].iloc[pos.entry_bar]["timestamp"]),
                        exit_bar=bar_idx,
                        exit_ts=str(bar_ts),
                        symbol=sym,
                        side=pos.side,
                        entry_price=pos.entry_price,
                        exit_price=current_close,
                        size=pos.size,
                        gross_pnl=round(gross_pnl, 2),
                        entry_cost=pos.entry_cost,
                        exit_cost=round(exit_cost, 4),
                        net_pnl=round(net_pnl, 2),
                        duration_bars=bar_idx - pos.entry_bar,
                        exit_reason="signal",
                    ))
                    positions[sym] = None

        # ── MTM Equity ─────────────────────────────────────────
        unrealized = 0.0
        n_open = 0
        for sym in SYMBOLS:
            pos = positions[sym]
            if pos is None:
                continue
            n_open += 1
            ohlcv = ohlcv_data[sym]
            ohlcv_mask = ohlcv["timestamp"] == bar_ts
            if not ohlcv_mask.any():
                continue
            ohlcv_idx = ohlcv_mask.idxmax()
            current_close = close_data[sym][ohlcv_idx]
            mtm_return = current_close / pos.entry_price - 1.0
            mtm_pnl = pos.size * (mtm_return if pos.side == 1 else -mtm_return)
            unrealized += mtm_pnl

        total_equity = free_balance + unrealized
        equity_log.append({
            "timestamp": str(bar_ts),
            "bar": int(bar_idx),
            "equity": round(total_equity, 2),
            "free_balance": round(free_balance, 2),
            "unrealized_pnl": round(unrealized, 2),
            "open_positions": n_open,
        })

    elapsed = time.time() - t0
    logger.info(f"[backtest] complete: {elapsed:.0f}s  trades={len(trades)}  "
                f"final_equity=${free_balance:,.0f}")

    # ── Compute metrics ────────────────────────────────────────
    if len(trades) == 0:
        return {"status": "no_trades"}

    df_trades = pd.DataFrame([{
        "entry_ts": t.entry_ts, "exit_ts": t.exit_ts,
        "symbol": t.symbol, "side": "LONG" if t.side == 1 else "SHORT",
        "size": t.size, "gross_pnl": t.gross_pnl,
        "entry_cost": t.entry_cost, "exit_cost": t.exit_cost,
        "net_pnl": t.net_pnl, "duration_bars": t.duration_bars,
        "exit_reason": t.exit_reason,
    } for t in trades])

    # Daily returns from equity curve
    eq_df = pd.DataFrame(equity_log)
    eq_df["timestamp"] = pd.to_datetime(eq_df["timestamp"])
    eq_df = eq_df.set_index("timestamp").sort_index()
    daily_eq = eq_df["equity"].resample("D").last().dropna()
    daily_ret = daily_eq.pct_change().dropna()

    if len(daily_ret) > 5:
        mean_d = daily_ret.mean()
        std_d = daily_ret.std()
        sharpe = mean_d / std_d * np.sqrt(365) if std_d > 1e-10 else 0.0
        downside = daily_ret[daily_ret < 0]
        downside_std = downside.std() if len(downside) > 1 else 0.0
        sortino = mean_d / downside_std * np.sqrt(365) if downside_std > 1e-10 else 0.0
    else:
        sharpe = 0.0
        sortino = 0.0

    equity = daily_eq.values
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    max_dd = float(dd.min())

    n_days = len(daily_eq)
    years = n_days / 365.0
    total_return = float(equity[-1] / equity[0] - 1.0) if equity[0] > 0 else 0.0
    cagr = (equity[-1] / equity[0]) ** (1.0 / years) - 1.0 if years > 0 and equity[0] > 0 else 0.0
    calmar = cagr / abs(max_dd) if max_dd < 0 else 0.0

    wins = df_trades[df_trades["net_pnl"] > 0]
    losses = df_trades[df_trades["net_pnl"] <= 0]
    win_rate = len(wins) / len(df_trades) if len(df_trades) > 0 else 0.0
    profit_factor = abs(wins["net_pnl"].sum()) / abs(losses["net_pnl"].sum() + 1e-10)
    avg_dur = df_trades["duration_bars"].mean()

    total_costs = df_trades["entry_cost"].sum() + df_trades["exit_cost"].sum()

    # Exposure
    n_bars_with_positions = sum(1 for e in equity_log if e["open_positions"] > 0)
    exposure_pct = n_bars_with_positions / len(equity_log) * 100

    # Ulcer Index
    dd_squared = (dd ** 2).mean()
    ulcer = np.sqrt(dd_squared) if dd_squared > 0 else 0.0

    # Recovery Factor
    total_gain = float(equity[-1] - equity[0])
    max_dd_abs = float(np.max(np.abs(dd)) * equity[0]) if len(equity) > 0 else 1.0
    recovery_factor = total_gain / max_dd_abs if max_dd_abs > 1e-10 else 0.0

    # Time Under Water
    underwater = equity < peak
    tuw_pct = underwater.mean() * 100

    metrics = {
        "n_trades": len(trades),
        "n_wins": int(len(wins)),
        "n_losses": int(len(losses)),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4),
        "total_return": round(total_return, 6),
        "cagr": round(cagr, 4),
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4),
        "calmar": round(calmar, 4),
        "max_drawdown": round(max_dd, 6),
        "ulcer_index": round(float(ulcer), 6),
        "recovery_factor": round(recovery_factor, 4),
        "time_under_water_pct": round(float(tuw_pct), 2),
        "avg_duration_bars": round(float(avg_dur), 1),
        "exposure_pct": round(float(exposure_pct), 2),
        "total_costs": round(float(total_costs), 2),
        "final_equity": round(float(equity[-1]) if len(equity) > 0 else free_balance, 2),
        "peak_equity": round(float(peak[-1]) if len(peak) > 0 else free_balance, 2),
    }

    # ── Save outputs ───────────────────────────────────────────
    df_trades.to_parquet(REPORT_DIR / "trade_log.parquet", index=False)
    pd.DataFrame(equity_log).to_csv(REPORT_DIR / "equity_curve.csv", index=False)
    save_report(metrics, "phase11_results.json")

    logger.info(f"\n  Trades: {metrics['n_trades']}  |  WinRate: {metrics['win_rate']:.1%}")
    logger.info(f"  Sharpe: {metrics['sharpe']:.2f}  |  CAGR: {metrics['cagr']:.2%}")
    logger.info(f"  MaxDD: {metrics['max_drawdown']:.2%}  |  PF: {metrics['profit_factor']:.2f}")
    logger.info(f"  Exposure: {metrics['exposure_pct']:.1f}%  |  AvgDur: {metrics['avg_duration_bars']:.0f}b")
    logger.info(f"  Final: ${metrics['final_equity']:,.0f}")

    return metrics


def main():
    try:
        result = run_backtest()
        logger.info("\nPhase 11 complete")
    except Exception as e:
        logger.error(f"Phase 11 FAILED: {e}")
        logger.error(traceback.format_exc())
        save_report({"error": str(e), "traceback": traceback.format_exc()}, "partial_results.json")


if __name__ == "__main__":
    main()
