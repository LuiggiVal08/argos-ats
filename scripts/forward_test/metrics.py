"""STEP 3 — Fase B Evaluation Harness.

Tracks trading metrics, model calibration, and system health
in real-time. Generates daily reports.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.forward_test.regime import classify as classify_regime

logger = logging.getLogger("forward_test.metrics")


@dataclass
class TradeRecord:
    entry_time: str
    exit_time: str
    side: str
    entry_price: float
    exit_price: float
    qty: float
    gross_pnl: float
    net_pnl: float
    bars_held: int
    exit_reason: str  # "time" | "flip" | "kill_switch"
    regime: str = "undefined"


@dataclass
class PredictionRecord:
    timestamp: str
    y_proba: float
    signal: str
    features_sample: dict | None = None
    regime: str = "undefined"


@dataclass
class LatencyRecord:
    cycle: int
    step: str
    duration_ms: float


class MetricsTracker:
    """Real-time metrics accumulator.

    Data accumulates across cycles; reports are generated daily.
    """

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self.trades: list[TradeRecord] = []
        self.predictions: list[PredictionRecord] = []
        self.latencies: list[LatencyRecord] = []
        self.balance_snapshots: list[dict] = []
        self._current_day: date | None = None
        self._daily_accum: dict[str, list] = defaultdict(list)

    # ── Recording ───────────────────────────────────────────────────

    def record_trade(
        self,
        entry_time: str,
        exit_time: str,
        side: str,
        entry_price: float,
        exit_price: float,
        qty: float,
        gross_pnl: float,
        net_pnl: float,
        bars_held: int,
        exit_reason: str,
        regime: str = "undefined",
    ) -> None:
        t = TradeRecord(
            entry_time=entry_time,
            exit_time=exit_time,
            side=side,
            entry_price=entry_price,
            exit_price=exit_price,
            qty=qty,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            bars_held=bars_held,
            exit_reason=exit_reason,
            regime=regime,
        )
        self.trades.append(t)
        self._daily_accum["trades"].append(t)

    def record_prediction(
        self, timestamp: str, y_proba: float, signal: str,
        features_sample: dict | None = None, regime: str = "undefined",
    ) -> None:
        p = PredictionRecord(
            timestamp=timestamp,
            y_proba=y_proba,
            signal=signal,
            features_sample=features_sample,
            regime=regime,
        )
        self.predictions.append(p)
        self._daily_accum["predictions"].append(p)

    def record_latency(self, cycle: int, step: str, duration_ms: float) -> None:
        self.latencies.append(LatencyRecord(cycle=cycle, step=step, duration_ms=duration_ms))
        self._daily_accum["latencies"].append(LatencyRecord(cycle=cycle, step=step, duration_ms=duration_ms))

    def record_balance(self, balance: float, timestamp: str) -> None:
        self.balance_snapshots.append({"timestamp": timestamp, "balance": balance})

    # ── Trading Metrics ─────────────────────────────────────────────

    def compute_trading_metrics(self, trades: list[TradeRecord] | None = None) -> dict:
        ts = trades or self.trades
        if not ts:
            return {}

        gross_pnls = np.array([t.gross_pnl for t in ts])
        net_pnls = np.array([t.net_pnl for t in ts])
        wins = net_pnls > 0
        losses = net_pnls <= 0

        n = len(ts)
        n_wins = int(wins.sum())
        n_losses = int(losses.sum())
        win_rate = n_wins / n if n > 0 else 0.0

        avg_win = float(gross_pnls[wins].mean()) if n_wins > 0 else 0.0
        avg_loss = float(abs(gross_pnls[losses].mean())) if n_losses > 0 else 0.0
        profit_factor = avg_win / avg_loss if avg_loss > 0 else float("inf")

        total_gross = float(gross_pnls.sum())
        total_net = float(net_pnls.sum())
        expectancy = total_net / n if n > 0 else 0.0

        # Turnover
        gross_exposure = sum(abs(t.entry_price * t.qty) for t in ts)
        turnover_rate = gross_exposure / n if n > 0 else 0.0

        # Cost ratio
        total_costs = total_gross - total_net
        cost_ratio = abs(total_costs / total_net) if total_net != 0 else float("inf")

        # Max adverse excursion (worst gross loss)
        max_adverse = float(gross_pnls.min())

        return {
            "n_trades": n,
            "n_wins": n_wins,
            "n_losses": n_losses,
            "win_rate": round(win_rate, 4),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "profit_factor": round(profit_factor, 4),
            "total_gross_pnl": round(total_gross, 4),
            "total_net_pnl": round(total_net, 4),
            "expectancy": round(expectancy, 4),
            "turnover_rate": round(turnover_rate, 4),
            "cost_ratio": round(cost_ratio, 4),
            "max_adverse_excursion": round(max_adverse, 4),
        }

    # ── Model Metrics ───────────────────────────────────────────────

    def compute_calibration(self, predictions: list[PredictionRecord] | None = None, n_bins: int = 10) -> dict:
        ps = predictions or self.predictions
        if not ps:
            return {}

        probas = np.array([p.y_proba for p in ps])
        signals = np.array([p.signal for p in ps])

        # For calibration, we need actual outcomes. In forward test we don't
        # have the forward return at prediction time. We use the *next* prediction's
        # signal direction as a proxy (not ideal but what we have in real-time).
        # Better approach: store the forward return when we close the position.
        actual_direction = np.full(len(probas), np.nan)
        for i in range(len(probas) - 1):
            s = signals[i]
            if s == "BUY":
                actual_direction[i] = 1.0
            elif s == "SELL":
                actual_direction[i] = 0.0
            # HOLD: can't evaluate

        # Bin by probability
        bins = np.linspace(0, 1, n_bins + 1)
        bin_indices = np.digitize(probas, bins) - 1
        bin_indices = np.clip(bin_indices, 0, n_bins - 1)

        calibration = []
        for b in range(n_bins):
            mask = bin_indices == b
            if not mask.any():
                continue
            bin_probas = probas[mask]
            bin_actuals = actual_direction[mask]
            valid = ~np.isnan(bin_actuals)
            if not valid.any():
                continue
            mean_pred = float(bin_probas[valid].mean())
            mean_actual = float(bin_actuals[valid].mean())
            calibration.append({
                "bin": b,
                "bin_range": [round(bins[b], 3), round(bins[b + 1], 3)],
                "n_samples": int(valid.sum()),
                "mean_predicted": round(mean_pred, 4),
                "mean_actual": round(mean_actual, 4),
                "error": round(mean_actual - mean_pred, 4),
            })

        return {
            "calibration": calibration,
            "n_total_predictions": len(ps),
            "n_evaluable": int((~np.isnan(actual_direction)).sum()),
        }

    def compute_signal_distribution(self, predictions: list[PredictionRecord] | None = None) -> dict:
        ps = predictions or self.predictions
        if not ps:
            return {}
        signals = [p.signal for p in ps]
        total = len(signals)
        counts = defaultdict(int)
        for s in signals:
            counts[s] += 1
        return {
            "total": total,
            "distribution": {
                k: {"count": v, "pct": round(v / total * 100, 2)}
                for k, v in sorted(counts.items())
            },
        }

    def compute_y_proba_stability(self, predictions: list[PredictionRecord] | None = None, window: int = 100) -> dict:
        ps = predictions or self.predictions
        if len(ps) < 2:
            return {}
        probas = np.array([p.y_proba for p in ps])
        return {
            "mean": round(float(probas.mean()), 4),
            "std": round(float(probas.std()), 4),
            "min": round(float(probas.min()), 4),
            "max": round(float(probas.max()), 4),
            "recent_mean": round(float(probas[-window:].mean()), 4) if len(probas) >= window else None,
            "recent_std": round(float(probas[-window:].std()), 4) if len(probas) >= window else None,
        }

    # ── System Metrics ──────────────────────────────────────────────

    def compute_system_metrics(self, latencies: list[LatencyRecord] | None = None) -> dict:
        ls = latencies or self.latencies
        if not ls:
            return {}
        durations = np.array([l.duration_ms for l in ls])

        # Breakdown by step
        steps = defaultdict(list)
        for l in ls:
            steps[l.step].append(l.duration_ms)

        step_stats = {}
        for step, durs in steps.items():
            arr = np.array(durs)
            step_stats[step] = {
                "mean_ms": round(float(arr.mean()), 2),
                "p50_ms": round(float(np.median(arr)), 2),
                "p99_ms": round(float(np.percentile(arr, 99)), 2),
                "count": len(arr),
            }

        return {
            "total_cycles": len(ls),
            "mean_latency_ms": round(float(durations.mean()), 2),
            "p50_latency_ms": round(float(np.median(durations)), 2),
            "p99_latency_ms": round(float(np.percentile(durations, 99)), 2),
            "step_breakdown": step_stats,
        }

    def compute_exposure_metrics(self) -> dict:
        if not self.predictions:
            return {}
        signals = [p.signal for p in self.predictions]
        total = len(signals)
        in_position = sum(1 for s in signals if s != "HOLD")
        return {
            "total_bars": total,
            "bars_in_position": in_position,
            "exposure_pct": round(in_position / total * 100, 2) if total > 0 else 0.0,
            "idle_pct": round((total - in_position) / total * 100, 2) if total > 0 else 0.0,
        }

    def compute_drawdown_curve(self) -> pd.Series | None:
        if not self.balance_snapshots:
            return None
        df = pd.DataFrame(self.balance_snapshots)
        df["peak"] = df["balance"].cummax()
        df["drawdown"] = (df["peak"] - df["balance"]) / df["peak"]
        return df

    def compute_regime_breakdown(self) -> dict:
        if not self.trades:
            return {}
        regime_trades = defaultdict(list)
        for t in self.trades:
            regime_trades[t.regime].append(t)

        breakdown = {}
        for regime, ts in regime_trades.items():
            metrics = self.compute_trading_metrics(ts)
            breakdown[regime] = metrics

        # Also breakdown predictions by regime
        regime_preds = defaultdict(list)
        for p in self.predictions:
            regime_preds[p.regime].append(p)

        for regime, ps in regime_preds.items():
            if regime not in breakdown:
                breakdown[regime] = {}
            breakdown[regime]["n_predictions"] = len(ps)
            breakdown[regime]["signal_distribution"] = self.compute_signal_distribution(ps)

        return dict(breakdown)

    # ── Daily Report ────────────────────────────────────────────────

    def generate_daily_report(self, report_date: str | None = None, watchdog_status: dict | None = None) -> dict:
        rd = report_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        trades = [t for t in self.trades if t.exit_time.startswith(rd)]
        preds = [p for p in self.predictions if p.timestamp.startswith(rd)]
        lats = [l for l in self.latencies if hasattr(l, "cycle")]

        report: dict = {
            "date": rd,
            "dry_run": self.dry_run,
            "trading_metrics": self.compute_trading_metrics(trades) if trades else {},
            "model_metrics": {
                "calibration": self.compute_calibration(preds) if preds else {},
                "signal_distribution": self.compute_signal_distribution(preds) if preds else {},
                "y_proba_stability": self.compute_y_proba_stability(preds) if preds else {},
            },
            "system_metrics": {
                **self.compute_system_metrics(lats),
                "exposure": self.compute_exposure_metrics(),
            },
            "regime_breakdown": self.compute_regime_breakdown(),
            "watchdog": watchdog_status or {},
        }

        report_dir = Path(__file__).parent.parent.parent / "reports" / "forward_test" / rd
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "daily_report.json"

        # Convert non-serializable
        def _serialize(obj):
            if isinstance(obj, (np.integer,)): return int(obj)
            if isinstance(obj, (np.floating,)): return float(obj)
            if isinstance(obj, np.ndarray): return obj.tolist()
            return str(obj)

        with open(report_path, "w") as f:
            json.dump(report, f, indent=2, default=_serialize)

        # Also save trades log
        trades_df = pd.DataFrame([asdict(t) for t in trades]) if trades else pd.DataFrame()
        trades_path = report_dir / "trades.csv"
        trades_df.to_csv(trades_path, index=False)

        logger.info(f"Daily report saved → {report_path}  ({len(trades)} trades, {len(preds)} predictions)")
        return report
