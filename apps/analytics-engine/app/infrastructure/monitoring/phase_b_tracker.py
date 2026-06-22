"""Phase B tracker — continuous metric tracking, kill-switch monitors,
daily reports, regime analysis, and market baseline comparison.

All tracking is in-memory with periodic structured log emission.
No external storage, no architecture changes, no model modifications.
Single truth stream: ticks -> candles -> inference -> signals -> execution -> monitor -> logs.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from math import sqrt
from statistics import mean, stdev
from typing import Any

import structlog

log = structlog.get_logger()

# ── Constants ──────────────────────────────────────────────────────────────

_WINDOW_7D = 7
_WINDOW_30D = 30
_TRADING_DAYS_PER_YEAR = 252
_TF_BASE_MS = 3_600_000  # 1h (buffer timeframe)
_KILL_SWITCH_CONSECUTIVE_LOSSES = 5
_KILL_SWITCH_DRAWDOWN_PCT = 0.05
_KILL_SWITCH_LOW_CONFIDENCE = 0.3
_KILL_SWITCH_MIN_TRADES = 3
_MIN_CANDLES = 5


# ── Trade entry ───────────────────────────────────────────────────────────


@dataclass
class PhaseBTradeEntry:
    signal_id: str
    symbol: str
    side: str
    entry_price: Decimal
    exit_price: Decimal
    units: Decimal
    realized_pnl: Decimal
    fees: Decimal
    confidence: float
    regime: str
    drawdown_at_entry: float
    opened_at: datetime
    closed_at: datetime
    model_version: str
    duration_seconds: float
    episode_id: str = ""
    feature_hash: str = ""


@dataclass
class DailySummary:
    date: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    realized_pnl: Decimal
    fees_total: Decimal
    buy_hold_pnl: Decimal
    ema_cross_pnl: Decimal
    regime: str
    avg_confidence: float
    peak_drawdown_pct: float
    signal_count: int
    acceptance_rate: float


@dataclass
class RollingMetrics:
    window_days: int
    total_trades: int
    win_rate: float
    expectancy: float
    profit_factor: float
    sharpe: float
    max_drawdown: float
    avg_confidence: float
    total_pnl: Decimal


@dataclass
class KillSwitchDiagnostic:
    triggered: bool
    criteria: list[str]
    details: dict


# ── Market Baseline ───────────────────────────────────────────────────────


class _MarketBaseline:
    """In-process Buy & Hold and EMA Cross baseline simulators.

    Consumes candle close prices and tracks simulated capital
    for both strategies. All values in quote currency.
    """

    def __init__(self, initial_capital: Decimal = Decimal("1000")) -> None:
        self._capital = float(initial_capital)

        self._bnh_entry_price: float | None = None
        self._last_close: float = 0.0

        self._ema12: float | None = None
        self._ema26: float | None = None
        self._ema_position: str = "OUT"
        self._ema_entry_price: float | None = None
        self._ema_capital: float = float(initial_capital)

    def on_5m_candle(self, candle: dict) -> None:
        close = float(candle.get("close", 0))
        if close <= 0:
            return

        self._last_close = close

        if self._bnh_entry_price is None:
            self._bnh_entry_price = close

        self._update_ema(close)

    def _update_ema(self, close: float) -> None:
        k12 = 2.0 / 13.0
        k26 = 2.0 / 27.0

        if self._ema12 is None:
            self._ema12 = close
            self._ema26 = close
            return

        prev12 = self._ema12
        prev26 = self._ema26
        self._ema12 = close * k12 + prev12 * (1.0 - k12)
        self._ema26 = close * k26 + prev26 * (1.0 - k26)

        if self._ema12 > self._ema26 and self._ema_position == "OUT":
            self._ema_position = "IN"
            self._ema_entry_price = close
        elif self._ema12 <= self._ema26 and self._ema_position == "IN":
            if self._ema_entry_price and self._ema_entry_price > 0:
                ret = (close - self._ema_entry_price) / self._ema_entry_price
                self._ema_capital *= (1.0 + ret)
            self._ema_position = "OUT"
            self._ema_entry_price = None

    def bnh_unrealized_pnl(self, current_price: Decimal) -> Decimal:
        if self._bnh_entry_price is None or self._bnh_entry_price <= 0:
            return Decimal("0")
        entry = Decimal(str(self._bnh_entry_price))
        current = Decimal(str(current_price))
        return (current - entry) / entry * Decimal(str(self._capital))

    @property
    def ema_cross_pnl(self) -> Decimal:
        total = Decimal(str(self._ema_capital)) - Decimal(str(self._capital))
        if self._ema_position == "IN" and self._ema_entry_price and self._ema_entry_price > 0 and self._last_close > 0:
            ret = (self._last_close - self._ema_entry_price) / self._ema_entry_price
            total += Decimal(str(self._ema_capital * ret))
        return total

    @property
    def ema_cross_position(self) -> str:
        return self._ema_position


# ── Kill Switch Monitor ───────────────────────────────────────────────────


class _KillSwitchMonitor:
    """Passive diagnostic kill-switch monitor.

    Checks configurable thresholds and logs when breached.
    NEVER halts execution.
    """

    def __init__(
        self,
        max_consecutive_losses: int = _KILL_SWITCH_CONSECUTIVE_LOSSES,
        max_drawdown_pct: float = _KILL_SWITCH_DRAWDOWN_PCT,
        min_avg_confidence: float = _KILL_SWITCH_LOW_CONFIDENCE,
        min_trades_for_check: int = _KILL_SWITCH_MIN_TRADES,
    ) -> None:
        self._max_consecutive_losses = max_consecutive_losses
        self._max_drawdown_pct = max_drawdown_pct
        self._min_avg_confidence = min_avg_confidence
        self._min_trades_for_check = min_trades_for_check

    def check(
        self,
        consecutive_losses: int,
        drawdown_pct: float,
        avg_confidence: float,
        total_trades: int,
    ) -> KillSwitchDiagnostic:
        triggered = False
        criteria: list[str] = []
        details: dict[str, Any] = {}

        if total_trades >= self._min_trades_for_check:
            if consecutive_losses >= self._max_consecutive_losses:
                triggered = True
                criteria.append("consecutive_losses")
                details["consecutive_losses"] = consecutive_losses
                details["consecutive_losses_threshold"] = self._max_consecutive_losses

            if drawdown_pct >= self._max_drawdown_pct:
                triggered = True
                criteria.append("drawdown_exceeded")
                details["drawdown_pct"] = round(drawdown_pct, 4)
                details["drawdown_threshold"] = self._max_drawdown_pct

            if avg_confidence < self._min_avg_confidence:
                triggered = True
                criteria.append("low_confidence")
                details["avg_confidence"] = round(avg_confidence, 4)
                details["confidence_threshold"] = self._min_avg_confidence

        return KillSwitchDiagnostic(
            triggered=triggered, criteria=criteria, details=details
        )


# ── Metrics Engine ────────────────────────────────────────────────────────


class _MetricsEngine:
    """Computes aggregate performance metrics from a trade log."""

    @staticmethod
    def rolling_metrics(
        trades: list[PhaseBTradeEntry],
        daily_returns: dict[str, Decimal],
        max_drawdown_pct: float,
        window_days: int,
    ) -> RollingMetrics:
        if not trades:
            return RollingMetrics(
                window_days=window_days,
                total_trades=0,
                win_rate=0.0,
                expectancy=0.0,
                profit_factor=0.0,
                sharpe=0.0,
                max_drawdown=0.0,
                avg_confidence=0.0,
                total_pnl=Decimal("0"),
            )

        total = len(trades)
        wins = sum(1 for t in trades if t.realized_pnl > 0)
        win_rate = wins / total

        pnls = [t.realized_pnl for t in trades]
        total_pnl = sum(pnls, Decimal("0"))
        avg_pnl = total_pnl / Decimal(str(total))
        expectancy = float(avg_pnl)

        gross_wins = sum(
            (t.realized_pnl for t in trades if t.realized_pnl > 0),
            Decimal("0"),
        )
        gross_losses = sum(
            (abs(t.realized_pnl) for t in trades if t.realized_pnl < 0),
            Decimal("0"),
        )
        profit_factor = (
            float(gross_wins / gross_losses) if gross_losses > 0 else float("inf")
        )

        sharpe = _MetricsEngine._compute_sharpe(daily_returns)

        avg_conf = mean(t.confidence for t in trades)

        return RollingMetrics(
            window_days=window_days,
            total_trades=total,
            win_rate=round(win_rate, 4),
            expectancy=round(expectancy, 4),
            profit_factor=round(profit_factor, 4) if profit_factor != float("inf") else -1.0,
            sharpe=round(sharpe, 4),
            max_drawdown=round(max_drawdown_pct, 4),
            avg_confidence=round(avg_conf, 4),
            total_pnl=total_pnl,
        )

    @staticmethod
    def _compute_sharpe(daily_returns: dict[str, Decimal]) -> float:
        if not daily_returns:
            return 0.0
        returns = [float(r) for r in daily_returns.values()]
        if len(returns) < 5:
            return 0.0
        s = stdev(returns)
        if s == 0:
            return 0.0
        return mean(returns) / s * sqrt(_TRADING_DAYS_PER_YEAR)


# ── Regime Helper ─────────────────────────────────────────────────────────


def _classify_regime(candles_5m: list[dict]) -> str:
    """Simple ADX-like regime classification from 5m candles.

    Uses ATR/price ratio as a proxy:
      - > 1.5% -> TRENDING (high volatility / directional)
      - <= 1.5% -> RANGING (low volatility / mean-reverting)
    Returns UNKNOWN with insufficient data.
    """
    if len(candles_5m) < 14:
        return "UNKNOWN"

    highs = [float(c["high"]) for c in candles_5m]
    lows = [float(c["low"]) for c in candles_5m]
    closes = [float(c["close"]) for c in candles_5m]

    tr_values: list[float] = []
    for i in range(1, len(candles_5m)):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr_values.append(max(hl, hc, lc))

    if len(tr_values) < 14:
        return "UNKNOWN"

    atr_14 = sum(tr_values[-14:]) / 14.0
    current_price = closes[-1]
    atr_pct = atr_14 / current_price if current_price > 0 else 0

    if atr_pct > 0.015:
        return "TRENDING"
    return "RANGING"


# ── Phase B Tracker ──────────────────────────────────────────────────────


class PhaseBTracker:
    """Central Phase B coordinator.

    Wires together trade tracking, signal quality, market baselines,
    kill-switch monitoring, and reporting.
    All tracking is in-memory; results are emitted as structured logs.

    Usage:
        tracker = PhaseBTracker(candle_buffer, position_repo)

        # In decision loop:
        tracker.record_signal(side, confidence, regime, model_version, accepted, reason)
        tracker.record_execution(signal_id, position_id, side, confidence, regime, model_version, entry_price)

        # In Phase B loop (every 60s):
        await tracker.tick()
    """

    def __init__(
        self,
        candle_buffer: Any,
        position_repo: Any,
        symbol: str = "BTC/USDT",
        initial_capital: Decimal = Decimal("1000"),
    ) -> None:
        self._candle_buffer = candle_buffer
        self._position_repo = position_repo
        self._symbol = symbol
        self._initial_capital = initial_capital

        self._trade_log: list[PhaseBTradeEntry] = []
        self._seen_closed: set[str] = set()
        self._position_metadata: dict[str, dict[str, Any]] = {}
        self._daily_returns: dict[str, Decimal] = defaultdict(Decimal)
        self._peak_capital = initial_capital
        self._max_drawdown_pct: float = 0.0
        self._consecutive_losses: int = 0

        self._signal_records: deque[dict[str, Any]] = deque(maxlen=1000)
        self._signal_counts: dict[str, int] = defaultdict(int)
        self._accepted_count: int = 0
        self._rejected_count: int = 0
        self._rejection_reasons: dict[str, int] = defaultdict(int)

        self._market_baseline = _MarketBaseline(initial_capital=initial_capital)
        self._kill_switch = _KillSwitchMonitor()

        self._last_baseline_ts: int = 0
        self._last_daily_report_date: str = ""
        self._start_time = datetime.now(timezone.utc)

    # ── Signal recording (called from decision loop) ─────────────────

    def record_signal(
        self,
        side: str,
        confidence: float,
        regime: str,
        model_version: str,
        accepted: bool,
        reason: str = "",
    ) -> None:
        now = datetime.now(timezone.utc)
        self._signal_records.append({
            "side": side,
            "confidence": confidence,
            "regime": regime,
            "model_version": model_version,
            "accepted": accepted,
            "reason": reason,
            "date": now.strftime("%Y-%m-%d"),
        })
        self._signal_counts[side] += 1

        if accepted:
            self._accepted_count += 1
        else:
            self._rejected_count += 1
            if reason:
                self._rejection_reasons[reason] += 1

    def record_execution(
        self,
        signal_id: str,
        position_id: str | None,
        side: str,
        confidence: float,
        regime: str,
        model_version: str,
        entry_price: Decimal | None,
        source: str = "STREAMING",
        episode_id: str = "",
        feature_hash: str = "",
    ) -> None:
        if position_id is None:
            return
        self._position_metadata[position_id] = {
            "signal_id": signal_id,
            "side": side,
            "confidence": confidence,
            "regime": regime,
            "model_version": model_version,
            "entry_price": entry_price,
            "opened_at": datetime.now(timezone.utc),
            "drawdown_at_entry": self._max_drawdown_pct,
            "source": source,
            "episode_id": episode_id,
            "feature_hash": feature_hash,
        }

    # ── Main tick ───────────────────────────────────────────────────

    async def tick(self) -> None:
        now = datetime.now(timezone.utc)

        await self._update_market_baseline()
        await self._poll_closed_positions()
        await self._check_kill_switch()

        metrics_7d = self._compute_rolling_metrics(_WINDOW_7D)
        metrics_30d = self._compute_rolling_metrics(_WINDOW_30D)
        self._emit_periodic_metrics(metrics_7d, metrics_30d, now)

        await self._maybe_emit_daily_report(now)

    # ── Market baseline ─────────────────────────────────────────────

    async def _update_market_baseline(self) -> None:
        try:
            candles = self._get_5m_candles()
            if not candles:
                return
            last_ts = int(candles[-1].get("timestamp", 0))
            if last_ts <= self._last_baseline_ts:
                return
            for c in candles:
                ts = int(c.get("timestamp", 0))
                if ts <= self._last_baseline_ts:
                    continue
                self._market_baseline.on_5m_candle(c)
            self._last_baseline_ts = last_ts
        except Exception as e:
            log.warning("phase_b_market_baseline_error", error=str(e))

    def _get_5m_candles(self) -> list[dict]:
        """Return candles from buffer for baseline computation.

        Buffer now holds 1h candles directly (CandleBuilder with
        timeframe_seconds=3600). No aggregation needed — return as-is.
        """
        try:
            buf = self._candle_buffer
            candles = buf.to_ohlcv_dicts() if hasattr(buf, "to_ohlcv_dicts") else []
            if len(candles) < _MIN_CANDLES:
                return []
            return candles
        except Exception:
            return []

    # ── Closed position polling ─────────────────────────────────────

    async def _poll_closed_positions(self) -> None:
        try:
            all_positions = await self._position_repo.list_all()
        except Exception as e:
            log.warning("phase_b_poll_positions_error", error=str(e))
            return

        for p in all_positions:
            if p.position_id in self._seen_closed:
                continue
            if p.status not in ("CLOSED", "SL_HIT", "TP_HIT"):
                continue
            if p.closed_at is None or p.realized_pnl is None:
                continue

            self._seen_closed.add(p.position_id)
            meta = self._position_metadata.pop(p.position_id, {})
            duration = (p.closed_at - p.opened_at).total_seconds()
            p_side = str(p.side.value) if hasattr(p.side, "value") else str(p.side)

            entry = PhaseBTradeEntry(
                signal_id=meta.get("signal_id", ""),
                symbol=self._symbol,
                side=meta.get("side", p_side),
                entry_price=meta.get("entry_price", p.entry_price),
                exit_price=p.current_price,
                units=p.units,
                realized_pnl=p.realized_pnl,
                fees=Decimal("0"),
                confidence=meta.get("confidence", 0.0),
                regime=meta.get("regime", "UNKNOWN"),
                drawdown_at_entry=meta.get("drawdown_at_entry", 0.0),
                opened_at=p.opened_at,
                closed_at=p.closed_at,
                model_version=meta.get("model_version", ""),
                duration_seconds=duration,
                episode_id=meta.get("episode_id", ""),
                feature_hash=meta.get("feature_hash", ""),
            )
            self._trade_log.append(entry)

            day_key = p.closed_at.strftime("%Y-%m-%d")
            self._daily_returns[day_key] += p.realized_pnl

            cum_pnl = sum((t.realized_pnl for t in self._trade_log), Decimal("0"))
            current_capital = self._initial_capital + cum_pnl
            if current_capital > self._peak_capital:
                self._peak_capital = current_capital
            if self._peak_capital > 0:
                dd_pct = float((self._peak_capital - current_capital) / self._peak_capital)
                if dd_pct > self._max_drawdown_pct:
                    self._max_drawdown_pct = dd_pct

            if p.realized_pnl < 0:
                self._consecutive_losses += 1
            else:
                self._consecutive_losses = 0

            log.info(
                "phase_b_trade_closed",
                signal_id=entry.signal_id,
                side=entry.side,
                pnl=str(entry.realized_pnl),
                confidence=entry.confidence,
                regime=entry.regime,
                duration_s=round(entry.duration_seconds),
                drawdown_at_entry=round(entry.drawdown_at_entry, 4),
            )

    # ── Kill switch ─────────────────────────────────────────────────

    async def _check_kill_switch(self) -> None:
        avg_conf = self._compute_avg_confidence()
        n_trades = len(self._trade_log)

        diag = self._kill_switch.check(
            consecutive_losses=self._consecutive_losses,
            drawdown_pct=self._max_drawdown_pct,
            avg_confidence=avg_conf,
            total_trades=n_trades,
        )

        if diag.triggered:
            log.warning(
                "phase_b_kill_switch_triggered",
                criteria=diag.criteria,
                details=diag.details,
                total_trades=n_trades,
                total_signals=self._accepted_count + self._rejected_count,
                uptime_s=int((datetime.now(timezone.utc) - self._start_time).total_seconds()),
            )
        else:
            log.info(
                "phase_b_kill_switch_check",
                consecutive_losses=self._consecutive_losses,
                drawdown_pct=round(self._max_drawdown_pct, 4),
                avg_confidence=round(avg_conf, 4),
                total_trades=n_trades,
            )

    # ── Rolling metrics ─────────────────────────────────────────────

    def _compute_rolling_metrics(self, window_days: int) -> RollingMetrics:
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
        window_trades = [t for t in self._trade_log if t.closed_at >= cutoff]

        window_returns: dict[str, Decimal] = defaultdict(Decimal)
        for t in window_trades:
            day_key = t.closed_at.strftime("%Y-%m-%d")
            window_returns[day_key] += t.realized_pnl

        running_peak = self._initial_capital
        running_capital = self._initial_capital
        window_max_dd = 0.0
        for t in sorted(window_trades, key=lambda x: x.closed_at):
            running_capital += t.realized_pnl
            if running_capital > running_peak:
                running_peak = running_capital
            if running_peak > 0:
                dd = float((running_peak - running_capital) / running_peak)
                if dd > window_max_dd:
                    window_max_dd = dd

        return _MetricsEngine.rolling_metrics(
            trades=window_trades,
            daily_returns=dict(window_returns),
            max_drawdown_pct=window_max_dd,
            window_days=window_days,
        )

    def _compute_avg_confidence(self) -> float:
        accepted_trades = [t for t in self._trade_log if t.realized_pnl is not None]
        if not accepted_trades:
            accepted_signals = [r for r in self._signal_records if r.get("accepted")]
            if not accepted_signals:
                return 0.0
            return mean(r["confidence"] for r in accepted_signals)
        return mean(t.confidence for t in accepted_trades)

    # ── Metric emission ─────────────────────────────────────────────

    def _emit_periodic_metrics(
        self, metrics_7d: RollingMetrics, metrics_30d: RollingMetrics, now: datetime
    ) -> None:
        total_signals = self._accepted_count + self._rejected_count
        acceptance_rate = self._accepted_count / total_signals if total_signals > 0 else 0.0

        log.info(
            "phase_b_periodic_metrics",
            uptime_s=int((now - self._start_time).total_seconds()),
            total_trades=len(self._trade_log),
            total_signals=total_signals,
            acceptance_rate=round(acceptance_rate, 4),
            regime=self._get_current_regime(),
            rolling_7d={
                "trades": metrics_7d.total_trades,
                "win_rate": metrics_7d.win_rate,
                "expectancy": metrics_7d.expectancy,
                "profit_factor": metrics_7d.profit_factor,
                "sharpe": metrics_7d.sharpe,
                "max_drawdown": metrics_7d.max_drawdown,
                "avg_confidence": metrics_7d.avg_confidence,
                "total_pnl": str(metrics_7d.total_pnl),
            },
            rolling_30d={
                "trades": metrics_30d.total_trades,
                "win_rate": metrics_30d.win_rate,
                "expectancy": metrics_30d.expectancy,
                "profit_factor": metrics_30d.profit_factor,
                "sharpe": metrics_30d.sharpe,
                "max_drawdown": metrics_30d.max_drawdown,
                "avg_confidence": metrics_30d.avg_confidence,
                "total_pnl": str(metrics_30d.total_pnl),
            },
            signal_distribution=dict(self._signal_counts),
            rejection_reasons=dict(self._rejection_reasons),
            buy_hold_pnl=str(self._compute_bnh_pnl()),
            ema_cross_pnl=str(self._market_baseline.ema_cross_pnl),
        )

    def _get_current_regime(self) -> str:
        try:
            candles_5m = self._get_5m_candles()
            return _classify_regime(candles_5m)
        except Exception:
            return "UNKNOWN"

    def _compute_bnh_pnl(self) -> Decimal:
        candles_5m = self._get_5m_candles()
        if not candles_5m:
            return Decimal("0")
        current_close = Decimal(str(candles_5m[-1].get("close", 0)))
        return self._market_baseline.bnh_unrealized_pnl(current_close)

    # ── Daily report ────────────────────────────────────────────────

    async def _maybe_emit_daily_report(self, now: datetime) -> None:
        today = now.strftime("%Y-%m-%d")
        if today == self._last_daily_report_date:
            return
        if now.hour != 0 or now.minute < 5:
            return

        self._last_daily_report_date = today
        yesterday = now - timedelta(days=1)
        yesterday_key = yesterday.strftime("%Y-%m-%d")

        daily_trades = [t for t in self._trade_log if t.closed_at.strftime("%Y-%m-%d") == yesterday_key]
        total = len(daily_trades)
        wins = sum(1 for t in daily_trades if t.realized_pnl > 0)
        total_pnl = sum((t.realized_pnl for t in daily_trades), Decimal("0"))
        avg_conf = mean(t.confidence for t in daily_trades) if daily_trades else 0.0

        yesterday_signals = [r for r in self._signal_records if r.get("date") == yesterday_key]
        signal_count = len(yesterday_signals)
        sig_accepted = sum(1 for r in yesterday_signals if r.get("accepted"))
        sig_total = len(yesterday_signals)
        acceptance_rate = sig_accepted / sig_total if sig_total > 0 else 0.0

        bnh_pnl = self._compute_bnh_pnl()

        log.info(
            "phase_b_daily_report",
            date=yesterday_key,
            total_trades=total,
            winning_trades=wins,
            losing_trades=total - wins,
            realized_pnl=str(total_pnl),
            fees_total="0",
            buy_hold_baseline=str(bnh_pnl),
            ema_cross_baseline=str(self._market_baseline.ema_cross_pnl),
            regime=self._get_current_regime(),
            avg_confidence=round(avg_conf, 4),
            peak_drawdown_pct=round(self._max_drawdown_pct, 4),
            signal_count=signal_count,
            acceptance_rate=round(acceptance_rate, 4),
        )

    # ── Properties ──────────────────────────────────────────────────

    @property
    def trade_count(self) -> int:
        return len(self._trade_log)

    def all_closed_since(self, index: int) -> list[PhaseBTradeEntry]:
        """Retorna los trades cerrados desde el índice dado (0-based)
        en adelante. Útil para settle incremental de TradeEpisodes.
        """
        if index >= len(self._trade_log):
            return []
        return list(self._trade_log[index:])

    @property
    def total_pnl(self) -> Decimal:
        return sum((t.realized_pnl for t in self._trade_log), Decimal("0"))

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_losses

    @property
    def max_drawdown_pct(self) -> float:
        return self._max_drawdown_pct

    @property
    def daily_returns(self) -> dict[str, Decimal]:
        return dict(self._daily_returns)

    @property
    def bnh_pnl(self) -> Decimal:
        return self._compute_bnh_pnl()

    @property
    def ema_cross_pnl(self) -> Decimal:
        return self._market_baseline.ema_cross_pnl
