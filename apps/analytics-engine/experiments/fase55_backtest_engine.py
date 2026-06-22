"""BacktestEngine — Reusable backtest engine for FASE 5.5.

Features SL/TP dinámico por ATR, position sizing por % riesgo,
fees, slippage, spread, latencia de 1 vela, y chequeo de stops
en cada vela antes de evaluar nueva señal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class TradeRecord:
    entry_idx: int
    entry_time: Any
    entry_price: float
    signal_prob: float
    size: float
    sl_price: float
    tp_price: float
    exit_idx: int | None = None
    exit_time: Any = None
    exit_price: float | None = None
    exit_reason: str = ""  # SL | TP | SIGNAL | END
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    bars_held: int = 0


@dataclass
class BacktestResult:
    trades: list[TradeRecord] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    win_rate: float = 0.0
    total_return_pct: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    calmar: float = 0.0
    max_dd_pct: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    n_trades: int = 0
    avg_bars_held: float = 0.0
    max_consecutive_losses: int = 0
    max_consecutive_wins: int = 0
    pnl_histogram: list[float] = field(default_factory=list)
    exposure_pct: float = 0.0
    time_in_market_pct: float = 0.0
    turnover_per_day: float = 0.0
    cagr: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0


class BacktestEngine:
    """Simulate trading with SL/TP, position sizing, fees, slippage."""

    def __init__(
        self,
        sl_mult: float = 1.5,
        tp_mult: float = 3.0,
        risk_pct: float = 0.01,
        fee: float = 0.001,
        slippage: float = 0.0005,
        spread: float = 0.0001,
        min_prob: float = 0.425,
        adx_threshold: float = 0.0,
        max_trades_per_day: float = float("inf"),
        htf_alignment: str = "none",
        vol_regime_filter: str = "all",
        vol_regime_series: np.ndarray | None = None,
        htf_trend_series: np.ndarray | None = None,
        atr_series: np.ndarray | None = None,
    ):
        self.sl_mult = sl_mult
        self.tp_mult = tp_mult
        self.risk_pct = risk_pct
        self.fee = fee
        self.slippage = slippage
        self.spread = spread
        self.min_prob = min_prob
        self.adx_threshold = adx_threshold
        self.max_trades_per_day = max_trades_per_day
        self.htf_alignment = htf_alignment
        self.vol_regime_filter = vol_regime_filter
        self.vol_regime_series = vol_regime_series
        self.htf_trend_series = htf_trend_series
        self.atr_series = atr_series

    def run(
        self,
        close: np.ndarray,
        high: np.ndarray,
        low: np.ndarray,
        timestamps: np.ndarray,
        proba: np.ndarray,
        adx: np.ndarray | None = None,
        initial_balance: float = 10000.0,
        atr_values: np.ndarray | None = None,
    ) -> BacktestResult:
        n = len(close)
        balance = initial_balance
        equity = [balance]
        trades: list[TradeRecord] = []
        position: TradeRecord | None = None
        day_trades: dict[str, int] = {}
        max_dd = 0.0
        peak = balance
        trade_pnls: list[float] = []
        consec_losses = 0
        consec_wins = 0
        max_cl = 0
        max_cw = 0
        bars_in_market = 0

        for i in range(1, n):
            ts_raw = timestamps[i]
            ts = pd.Timestamp(ts_raw) if not isinstance(ts_raw, pd.Timestamp) else ts_raw
            day_key = str(ts.date())

            # ── Check SL/TP for open position ──
            if position is not None:
                low_i = low[i]
                high_i = high[i]
                exit_reason = None

                # Check stop loss hit
                if low_i <= position.sl_price:
                    exit_price = position.sl_price * (1 - self.slippage)
                    exit_reason = "SL"
                # Check take profit hit
                elif high_i >= position.tp_price:
                    exit_price = position.tp_price * (1 - self.slippage)
                    exit_reason = "TP"

                if exit_reason is not None:
                    gross_pnl = position.size * (exit_price - position.entry_price)
                    fee_cost = (abs(position.size * position.entry_price) + abs(position.size * exit_price)) * self.fee
                    net_pnl = gross_pnl - fee_cost
                    balance += net_pnl
                    pnl_pct = net_pnl / (position.size * position.entry_price) if position.size * position.entry_price != 0 else 0
                    position.exit_idx = i
                    position.exit_time = ts
                    position.exit_price = exit_price
                    position.exit_reason = exit_reason
                    position.pnl_usd = net_pnl
                    position.pnl_pct = pnl_pct
                    position.bars_held = i - position.entry_idx
                    trades.append(position)
                    trade_pnls.append(net_pnl)

                    if net_pnl > 0:
                        consec_wins += 1
                        consec_losses = 0
                    else:
                        consec_losses += 1
                        consec_wins = 0
                    max_cl = max(max_cl, consec_losses)
                    max_cw = max(max_cw, consec_wins)

                    position = None
                    equity.append(balance)
                    peak = max(peak, balance)
                    dd = (peak - balance) / peak * 100
                    max_dd = max(max_dd, dd)
                    continue

            # ── Check trade filters ──
            has_position = position is not None

            # ADX filter
            if self.adx_threshold > 0 and adx is not None:
                if adx[i] < self.adx_threshold:
                    if not has_position:
                        equity.append(balance)
                    continue

            # Vol regime filter
            if self.vol_regime_filter != "all" and self.vol_regime_series is not None:
                regime = self.vol_regime_series[i]
                if self.vol_regime_filter == "low_only" and regime != -1:
                    if not has_position:
                        equity.append(balance)
                    continue
                if self.vol_regime_filter == "high_only" and regime != 1:
                    if not has_position:
                        equity.append(balance)
                    continue

            # HTF alignment filter
            if self.htf_alignment != "none" and self.htf_trend_series is not None:
                trend = self.htf_trend_series[i]
                if self.htf_alignment == "4h_trend":
                    htf_trend = trend  # 1=bullish, -1=bearish, 0=neutral
                    if htf_trend == 0:
                        if not has_position:
                            equity.append(balance)
                        continue
                elif self.htf_alignment == "1d_trend":
                    htf_trend = trend
                    if htf_trend == 0:
                        if not has_position:
                            equity.append(balance)
                        continue

            # Max trades per day
            if self.max_trades_per_day < float("inf"):
                day_count = day_trades.get(day_key, 0)
                if day_count >= self.max_trades_per_day and not has_position:
                    equity.append(balance)
                    continue

            # ── Generate signal ──
            signal = proba[i] >= self.min_prob
            if signal and not has_position:
                current_atr = atr_values[i] if atr_values is not None else (high[i] - low[i])
                if current_atr <= 0:
                    current_atr = close[i] * 0.01

                sl_distance = current_atr * self.sl_mult
                tp_distance = current_atr * self.tp_mult

                pos_size = (balance * self.risk_pct) / sl_distance
                if pos_size <= 0:
                    if not has_position:
                        equity.append(balance)
                    continue

                entry_price = close[i] * (1 + self.slippage + self.spread)
                sl_price = entry_price - sl_distance
                tp_price = entry_price + tp_distance

                position = TradeRecord(
                    entry_idx=i,
                    entry_time=ts,
                    entry_price=entry_price,
                    signal_prob=proba[i],
                    size=pos_size,
                    sl_price=sl_price,
                    tp_price=tp_price,
                )
                day_trades[day_key] = day_trades.get(day_key, 0) + 1
                bars_in_market += 1

            elif not signal and has_position:
                # Exit on opposite signal
                exit_price = close[i] * (1 - self.slippage)
                gross_pnl = position.size * (exit_price - position.entry_price)
                fee_cost = (abs(position.size * position.entry_price) + abs(position.size * exit_price)) * self.fee
                net_pnl = gross_pnl - fee_cost
                balance += net_pnl
                pnl_pct = net_pnl / (position.size * position.entry_price) if position.size * position.entry_price != 0 else 0
                position.exit_idx = i
                position.exit_time = ts
                position.exit_price = exit_price
                position.exit_reason = "SIGNAL"
                position.pnl_usd = net_pnl
                position.pnl_pct = pnl_pct
                position.bars_held = i - position.entry_idx
                trades.append(position)
                trade_pnls.append(net_pnl)

                if net_pnl > 0:
                    consec_wins += 1
                    consec_losses = 0
                else:
                    consec_losses += 1
                    consec_wins = 0
                max_cl = max(max_cl, consec_losses)
                max_cw = max(max_cw, consec_wins)

                position = None
                equity.append(balance)
                peak = max(peak, balance)
                dd = (peak - balance) / peak * 100
                max_dd = max(max_dd, dd)

            if has_position:
                bars_in_market += 1

            equity.append(balance)
            peak = max(peak, balance)
            dd = (peak - balance) / peak * 100
            max_dd = max(max_dd, dd)

        # Close any remaining position at end
        if position is not None:
            exit_price = close[-1] * (1 - self.slippage)
            gross_pnl = position.size * (exit_price - position.entry_price)
            fee_cost = (abs(position.size * position.entry_price) + abs(position.size * exit_price)) * self.fee
            net_pnl = gross_pnl - fee_cost
            balance += net_pnl
            position.exit_idx = n - 1
            position.exit_time = timestamps[-1]
            position.exit_price = exit_price
            position.exit_reason = "END"
            position.pnl_usd = net_pnl
            position.pnl_pct = net_pnl / (position.size * position.entry_price)
            position.bars_held = n - 1 - position.entry_idx
            trades.append(position)
            trade_pnls.append(net_pnl)
            equity.append(balance)

        # ── Compute metrics ──
        n_trades = len([t for t in trades if t.exit_reason != "END"])
        if n_trades == 0:
            return BacktestResult(trades=list(trades), equity_curve=equity)

        pnls = np.array([t.pnl_usd for t in trades if t.exit_reason != "END"])
        wins = pnls[pnls > 0]
        losses = pnls[pnls < 0]

        win_rate = float(len(wins) / len(pnls)) if len(pnls) > 0 else 0.0
        total_return = float((balance / initial_balance - 1.0) * 100)

        # Sharpe (daily returns)
        eq = np.array(equity)
        daily_returns = np.diff(eq) / eq[:-1]
        sharpe = float(np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(365 * 24)) if np.std(daily_returns) > 0 else 0.0

        # Sortino
        neg_rets = daily_returns[daily_returns < 0]
        downside = np.std(neg_rets) if len(neg_rets) > 0 else 1e-10
        sortino = float(np.mean(daily_returns) / downside * np.sqrt(365 * 24))

        # CAGR
        years = n / (365 * 24)
        cagr = float((balance / initial_balance) ** (1 / years) - 1) * 100 if years > 0 else 0.0

        # Calmar
        calmar = float(cagr / max_dd) if max_dd > 0 else 0.0

        # Profit factor
        profit_factor = float(wins.sum() / abs(losses.sum())) if len(losses) > 0 and losses.sum() != 0 else float("inf")

        # Expectancy
        expectancy = float(np.mean(pnls))

        # Avg win/loss
        avg_win = float(np.mean(wins)) if len(wins) > 0 else 0.0
        avg_loss = float(np.mean(losses)) if len(losses) > 0 else 0.0

        # Bars held
        avg_bars = float(np.mean([t.bars_held for t in trades if t.exit_reason != "END"]))

        # Exposure / time in market
        time_in_market = bars_in_market / n * 100
        exposure = float(np.mean([abs(t.size * t.entry_price) / balance for t in trades])) if len(trades) > 0 else 0.0

        # Turnover per day
        total_days = (timestamps[-1] - timestamps[0]) / (1e9 * 86400) if hasattr(timestamps[-1], 'timestamp') else n / 24
        total_days = float(total_days) if total_days > 0 else 1.0
        turnover = n_trades / total_days

        # PnL histogram (20 buckets)
        if len(pnls) > 0:
            hist, _ = np.histogram(pnls, bins=20)
            pnl_hist = hist.tolist()
        else:
            pnl_hist = []

        return BacktestResult(
            trades=list(trades),
            equity_curve=[float(x) for x in equity],
            win_rate=win_rate,
            total_return_pct=total_return,
            sharpe=sharpe,
            sortino=sortino,
            calmar=calmar,
            max_dd_pct=float(max_dd),
            profit_factor=profit_factor,
            expectancy=expectancy,
            n_trades=n_trades,
            avg_bars_held=avg_bars,
            max_consecutive_losses=max_cl,
            max_consecutive_wins=max_cw,
            pnl_histogram=pnl_hist,
            exposure_pct=exposure,
            time_in_market_pct=time_in_market,
            turnover_per_day=turnover,
            cagr=cagr,
            avg_win=avg_win,
            avg_loss=avg_loss,
        )
