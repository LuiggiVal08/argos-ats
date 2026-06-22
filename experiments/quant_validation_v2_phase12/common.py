"""Phase 12 — Position Sizing Real (ATR20-based).

Three risk variants: 0.5%, 1%, 2% per trade.
stop_distance = ATR20 × 2.
No abs(forward_return) anywhere (leakage check).
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from experiments.quant_validation_v2_phase38.common import load_ohlcv

logger = logging.getLogger("phase12")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

PHASE5_REPORT_DIR = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v2_phase5"
REPORT_DIR = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v2_phase12"
REPORT_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR = Path(__file__).parent.parent.parent / "apps" / "analytics-engine" / "data"

SYMBOLS = ["BTC", "ETH", "SOL"]
EXCHANGE = "binance"
INITIAL_CAPITAL = 100_000.0
BUY_THRESHOLD = 0.60
SELL_THRESHOLD = 0.40
COST_PER_SIDE = 0.00155
LOOKAHEAD = 5
STRIDE = 5
STOP_DIST_MULT = 2.0


@dataclass
class Position:
    symbol: str
    side: int
    entry_bar: int
    entry_price: float
    size: float
    entry_cost: float


def compute_atr20(ohlcv: pd.DataFrame) -> np.ndarray:
    close = ohlcv["close"].values.astype(float)
    high = ohlcv["high"].values.astype(float)
    low = ohlcv["low"].values.astype(float)
    tr = np.zeros(len(close))
    tr[0] = high[0] - low[0]
    for i in range(1, len(close)):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    atr = np.zeros(len(close))
    for i in range(20, len(close)):
        atr[i] = tr[i - 19:i + 1].mean()
    atr[:20] = atr[20] if len(close) > 20 else 0.0
    return atr


def run_backtest(risk_pct: float, label: str) -> dict:
    logger.info(f"\n{'=' * 50}")
    logger.info(f"  Variant: risk={risk_pct:.3f} ({label})  "
                f"stop_dist=ATR20×{STOP_DIST_MULT}")
    t0 = time.time()

    ohlcv_data: dict[str, pd.DataFrame] = {}
    close_data: dict[str, np.ndarray] = {}
    atr_data: dict[str, np.ndarray] = {}
    ts_data: dict[str, np.ndarray] = {}

    for sym in SYMBOLS:
        ohlcv = load_ohlcv(sym, EXCHANGE)
        ohlcv["timestamp"] = pd.to_datetime(ohlcv["timestamp"])
        ohlcv = ohlcv.sort_values("timestamp").reset_index(drop=True)
        ohlcv_data[sym] = ohlcv
        close_data[sym] = ohlcv["close"].values.astype(float)
        atr_data[sym] = compute_atr20(ohlcv)
        ts_data[sym] = ohlcv["timestamp"].values

    # Signals
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

    # Chronological bar index
    all_ts = pd.DatetimeIndex([])
    for sym in SYMBOLS:
        all_ts = all_ts.union(ohlcv_data[sym]["timestamp"])
    all_ts = all_ts.sort_values()
    logger.info(f"  {len(all_ts)} bars")

    # State
    positions: dict[str, Position | None] = {sym: None for sym in SYMBOLS}
    free_balance = INITIAL_CAPITAL
    trades: list[dict] = []
    equity_log: list[dict] = []

    # ── Bar loop ──
    for bar_idx, bar_ts in enumerate(all_ts):
        if bar_idx > 0 and bar_idx % 10000 == 0:
            logger.info(f"  [{bar_idx // len(all_ts) * 100:.0f}%] bar={bar_idx} "
                        f"trades={len(trades)} balance=${free_balance:,.0f}")

        for sym in SYMBOLS:
            ohlcv = ohlcv_data[sym]
            ohlcv_mask = ohlcv["timestamp"] == bar_ts
            if not ohlcv_mask.any():
                continue
            ohlcv_idx = ohlcv_mask.idxmax()
            current_close = close_data[sym][ohlcv_idx]
            current_atr = atr_data[sym][ohlcv_idx]

            has_signal = bar_ts in sig_lookup[sym]
            signal = sig_lookup[sym].get(bar_ts, 0)
            pos = positions[sym]

            # Flip
            if pos is not None and has_signal and signal != 0 and signal != pos.side:
                gross_return = current_close / pos.entry_price - 1.0
                gross_pnl = pos.size * (gross_return if pos.side == 1 else -gross_return)
                exit_cost = pos.size * COST_PER_SIDE
                net_pnl = gross_pnl - exit_cost
                free_balance += pos.size + pos.entry_cost + net_pnl
                trades.append({
                    "entry_ts": str(ts_data[sym][pos.entry_bar]),
                    "exit_ts": str(bar_ts), "symbol": sym,
                    "side": "LONG" if pos.side == 1 else "SHORT",
                    "size": round(pos.size, 6),
                    "entry_price": round(pos.entry_price, 2),
                    "exit_price": round(current_close, 2),
                    "gross_pnl": round(gross_pnl, 2),
                    "entry_cost": round(pos.entry_cost, 4),
                    "exit_cost": round(exit_cost, 4),
                    "net_pnl": round(net_pnl, 2),
                    "duration_bars": bar_idx - pos.entry_bar,
                    "exit_reason": "signal_flip",
                    "atr_entry": round(current_atr, 2),
                    "risk_pct": risk_pct,
                })
                positions[sym] = None

            # Open new
            if has_signal and signal != 0 and positions[sym] is None:
                entry_price = current_close
                stop_distance = max(current_atr * STOP_DIST_MULT, 0.01)
                size = min(free_balance * risk_pct / stop_distance,
                           free_balance * 0.5)  # cap at 50% of balance
                entry_cost = size * COST_PER_SIDE
                if size > 1.0 and free_balance > size + entry_cost:
                    positions[sym] = Position(
                        symbol=sym, side=signal,
                        entry_bar=bar_idx, entry_price=entry_price,
                        size=size, entry_cost=entry_cost,
                    )
                    free_balance -= (size + entry_cost)

            # Close after 5 bars
            elif pos is not None and (bar_idx - pos.entry_bar) >= LOOKAHEAD:
                if not has_signal or signal == 0:
                    gross_return = current_close / pos.entry_price - 1.0
                    gross_pnl = pos.size * (gross_return if pos.side == 1 else -gross_return)
                    exit_cost = pos.size * COST_PER_SIDE
                    net_pnl = gross_pnl - exit_cost
                    free_balance += pos.size + pos.entry_cost + net_pnl
                    trades.append({
                        "entry_ts": str(ts_data[sym][pos.entry_bar]),
                        "exit_ts": str(bar_ts), "symbol": sym,
                        "side": "LONG" if pos.side == 1 else "SHORT",
                        "size": round(pos.size, 6),
                        "entry_price": round(pos.entry_price, 2),
                        "exit_price": round(current_close, 2),
                        "gross_pnl": round(gross_pnl, 2),
                        "entry_cost": round(pos.entry_cost, 4),
                        "exit_cost": round(exit_cost, 4),
                        "net_pnl": round(net_pnl, 2),
                        "duration_bars": bar_idx - pos.entry_bar,
                        "exit_reason": "signal",
                        "atr_entry": round(current_atr, 2),
                        "risk_pct": risk_pct,
                    })
                    positions[sym] = None

        # MTM
        unrealized = 0.0
        n_open = 0
        for sym in SYMBOLS:
            pos = positions[sym]
            if pos is None:
                continue
            n_open += 1
            ohlcv_mask = ohlcv_data[sym]["timestamp"] == bar_ts
            if not ohlcv_mask.any():
                continue
            ohlcv_idx = ohlcv_mask.idxmax()
            mtm_return = close_data[sym][ohlcv_idx] / pos.entry_price - 1.0
            mtm_pnl = pos.size * (mtm_return if pos.side == 1 else -mtm_return)
            unrealized += mtm_pnl

        total_equity = free_balance + unrealized
        equity_log.append({
            "timestamp": str(bar_ts), "bar": int(bar_idx),
            "equity": round(total_equity, 2),
            "free_balance": round(free_balance, 2),
            "unrealized_pnl": round(unrealized, 2),
            "open_positions": n_open,
        })

    elapsed = time.time() - t0
    logger.info(f"  Done: {elapsed:.0f}s  trades={len(trades)}  "
                f"final=${free_balance:,.0f}")

    # ── Metrics ──
    if len(trades) == 0:
        return {"status": "no_trades", "label": label, "risk_pct": risk_pct}

    df_trades = pd.DataFrame(trades)

    eq_df = pd.DataFrame(equity_log)
    eq_df["timestamp"] = pd.to_datetime(eq_df["timestamp"])
    eq_df = eq_df.set_index("timestamp").sort_index()
    daily_eq = eq_df["equity"].resample("D").last().dropna()
    daily_ret = daily_eq.pct_change().dropna()

    mean_d = daily_ret.mean()
    std_d = daily_ret.std()
    sharpe = mean_d / std_d * np.sqrt(365) if std_d > 1e-10 else 0.0
    downside = daily_ret[daily_ret < 0]
    sortino = mean_d / (downside.std() + 1e-10) * np.sqrt(365)

    equity = daily_eq.values
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    max_dd = float(dd.min())

    n_days = len(daily_eq)
    years = n_days / 365.0
    total_return = float(equity[-1] / equity[0] - 1.0)
    cagr = (equity[-1] / equity[0]) ** (1.0 / years) - 1.0 if years > 0 and equity[0] > 0 else 0.0
    calmar = cagr / abs(max_dd) if max_dd < 0 else 0.0

    wins = df_trades[df_trades["net_pnl"] > 0]
    losses = df_trades[df_trades["net_pnl"] <= 0]
    win_rate = len(wins) / len(df_trades)
    profit_factor = abs(wins["net_pnl"].sum()) / abs(losses["net_pnl"].sum() + 1e-10)

    dd_squared = dd ** 2
    ulcer = np.sqrt(dd_squared.mean())

    total_gain = float(equity[-1] - equity[0])
    max_dd_abs = float(np.max(np.abs(dd)) * equity[0])
    recovery_factor = total_gain / max_dd_abs if max_dd_abs > 1e-10 else 0.0

    underwater = equity < peak
    tuw_pct = underwater.mean() * 100

    total_costs = df_trades["entry_cost"].sum() + df_trades["exit_cost"].sum()

    metrics = {
        "variant": label,
        "risk_pct": risk_pct,
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
        "final_equity": round(float(equity[-1]), 2),
        "peak_equity": round(float(peak[-1]), 2),
        "total_costs": round(float(total_costs), 2),
    }

    logger.info(f"  Sharpe={metrics['sharpe']:.2f}  CAGR={metrics['cagr']:.2%}  "
                f"MaxDD={metrics['max_drawdown']:.2%}  "
                f"Return={metrics['total_return']:.2f}x")
    logger.info(f"  Trades={metrics['n_trades']}  WR={metrics['win_rate']:.1%}  "
                f"PF={metrics['profit_factor']:.2f}")

    return metrics


def main():
    variants = [
        (0.005, "risk_0_5"),
        (0.01, "risk_1"),
        (0.02, "risk_2"),
    ]

    all_results = []
    for risk_pct, label in variants:
        try:
            result = run_backtest(risk_pct, label)
            all_results.append(result)
        except Exception as e:
            logger.error(f"Variant {label} FAILED: {e}")
            logger.error(traceback.format_exc())
            all_results.append({"variant": label, "risk_pct": risk_pct, "error": str(e)})

    # Summary
    summary_path = REPORT_DIR / "phase12_summary.json"
    with open(summary_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    logger.info(f"\n[summary] saved {summary_path}")

    best_sharpe = max(all_results, key=lambda r: r.get("sharpe", 0))
    best_calmar = max(all_results, key=lambda r: r.get("calmar", 0))
    logger.info(f"\nBest Sharpe: {best_sharpe['variant']} = {best_sharpe['sharpe']:.2f}")
    logger.info(f"Best Calmar: {best_calmar['variant']} = {best_calmar['calmar']:.2f}")
    logger.info("Phase 12 complete")


if __name__ == "__main__":
    main()
