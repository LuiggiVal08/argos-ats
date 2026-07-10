#!/usr/bin/env python3
"""Daily Snapshot Generator for Forward Test Experiment 001.

Generates a JSON snapshot of the experiment state at the end of each day.
Reads from existing forward_test data files — does NOT modify trading behavior.

Usage:
    python scripts/generate_daily_snapshot.py [--date YYYY-MM-DD] [--experiment-id FT-001]

Output:
    reports/forward-test/YYYY-MM-DD.json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────

EXPERIMENT_ID = "FT-001"
GIT_TAG = "v0.9.1-forward-test"
MODEL_ID = "qv2_target_spec_v1_reduced_33_primary"
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
FORWARD_TEST_DIR = PROJECT_ROOT / "forward_test"
REPORTS_DIR = PROJECT_ROOT / "reports" / "forward-test"
MODEL_DIR = PROJECT_ROOT / "models" / "production" / "btc"


def _get_git_commit() -> str:
    """Get current git commit SHA."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, cwd=PROJECT_ROOT,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def _compute_model_checksum(filepath: Path) -> str:
    """Compute MD5 checksum of a file."""
    if not filepath.exists():
        return "missing"
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_signals_csv(date_str: str) -> list[dict]:
    """Read signals from forward_test/signals_btc.csv for a given date."""
    signals_path = FORWARD_TEST_DIR / "signals_btc.csv"
    if not signals_path.exists():
        return []

    signals = []
    with open(signals_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = row.get("timestamp", "")
            if ts.startswith(date_str):
                signals.append(row)
    return signals


def _read_trades_csv(date_str: str) -> list[dict]:
    """Read trades from forward_test/trades_btc.csv for a given date."""
    trades_path = FORWARD_TEST_DIR / "trades_btc.csv"
    if not trades_path.exists():
        return []

    trades = []
    with open(trades_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            exit_time = row.get("exit_time", row.get("timestamp", ""))
            if exit_time.startswith(date_str):
                trades.append(row)
    return trades


def _read_equity_csv() -> list[dict]:
    """Read equity curve from forward_test/equity_btc.csv."""
    equity_path = FORWARD_TEST_DIR / "equity_btc.csv"
    if not equity_path.exists():
        return []

    equity = []
    with open(equity_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            equity.append(row)
    return equity


def _read_state() -> dict:
    """Read current state from forward_test/state_btc.json."""
    state_path = FORWARD_TEST_DIR / "state_btc.json"
    if not state_path.exists():
        return {}
    with open(state_path, "r") as f:
        return json.load(f)


def _compute_signal_distribution(signals: list[dict]) -> dict:
    """Compute BUY/SELL/HOLD distribution from signals."""
    counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
    for s in signals:
        signal = s.get("signal", "HOLD").upper()
        if signal in counts:
            counts[signal] += 1
    total = len(signals)
    return {
        "buy": counts["BUY"],
        "sell": counts["SELL"],
        "hold": counts["HOLD"],
        "total": total,
        "buy_pct": round(counts["BUY"] / total * 100, 2) if total > 0 else 0,
        "sell_pct": round(counts["SELL"] / total * 100, 2) if total > 0 else 0,
        "hold_pct": round(counts["HOLD"] / total * 100, 2) if total > 0 else 0,
    }


def _compute_confidence_stats(signals: list[dict]) -> dict:
    """Compute confidence statistics from signals."""
    probas = []
    for s in signals:
        y_proba = s.get("y_proba")
        if y_proba is not None:
            try:
                probas.append(float(y_proba))
            except (ValueError, TypeError):
                pass

    if not probas:
        return {"mean": 0, "std": 0, "min": 0, "max": 0, "count": 0}

    import statistics
    return {
        "mean": round(statistics.mean(probas), 4),
        "std": round(statistics.stdev(probas), 4) if len(probas) > 1 else 0,
        "min": round(min(probas), 4),
        "max": round(max(probas), 4),
        "count": len(probas),
    }


def _compute_regime_distribution(signals: list[dict]) -> dict:
    """Compute regime distribution from signals."""
    counts = {}
    for s in signals:
        regime = s.get("regime", "unknown")
        counts[regime] = counts.get(regime, 0) + 1
    total = len(signals)
    return {
        regime: {
            "count": count,
            "pct": round(count / total * 100, 2) if total > 0 else 0,
        }
        for regime, count in counts.items()
    }


def _compute_trading_metrics(trades: list[dict]) -> dict:
    """Compute trading metrics from trades."""
    if not trades:
        return {
            "executed": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0,
            "total_pnl": 0,
            "avg_pnl": 0,
            "max_win": 0,
            "max_loss": 0,
        }

    pnls = []
    for t in trades:
        pnl = t.get("pnl", t.get("net_pnl", 0))
        try:
            pnls.append(float(pnl))
        except (ValueError, TypeError):
            pnls.append(0)

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    return {
        "executed": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(pnls) * 100, 2) if pnls else 0,
        "total_pnl": round(sum(pnls), 2),
        "avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else 0,
        "max_win": round(max(pnls), 2) if pnls else 0,
        "max_loss": round(min(pnls), 2) if pnls else 0,
    }


def _compute_equity_metrics(equity: list[dict], date_str: str) -> dict:
    """Compute equity and drawdown metrics."""
    if not equity:
        return {"equity": 0, "pnl": 0, "drawdown": 0, "peak_equity": 0}

    # Get latest equity value
    latest = equity[-1]
    current_equity = float(latest.get("equity", latest.get("balance", 0)))

    # Find peak
    equities = [float(e.get("equity", e.get("balance", 0))) for e in equity]
    peak = max(equities) if equities else 0

    # Compute drawdown
    drawdown = ((peak - current_equity) / peak * 100) if peak > 0 else 0

    return {
        "equity": round(current_equity, 2),
        "pnl": round(current_equity - 100000, 2),  # Assuming 100k initial
        "drawdown": round(drawdown, 2),
        "peak_equity": round(peak, 2),
    }


def _compute_runtime_hours(date_str: str, start_date: str = "2026-07-10") -> float:
    """Compute runtime hours since experiment start."""
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        current = datetime.strptime(date_str, "%Y-%m-%d")
        delta = current - start
        return round(delta.total_seconds() / 3600, 1)
    except Exception:
        return 0


def generate_snapshot(date_str: str | None = None, experiment_id: str = EXPERIMENT_ID) -> dict:
    """Generate a daily snapshot of the experiment."""
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Read data
    signals = _read_signals_csv(date_str)
    trades = _read_trades_csv(date_str)
    equity = _read_equity_csv()
    state = _read_state()

    # Compute metrics
    signal_dist = _compute_signal_distribution(signals)
    confidence = _compute_confidence_stats(signals)
    regime_dist = _compute_regime_distribution(signals)
    trading = _compute_trading_metrics(trades)
    equity_metrics = _compute_equity_metrics(equity, date_str)

    # Get git info
    git_commit = _get_git_commit()

    # Model checksums
    model_checksum = _compute_model_checksum(MODEL_DIR / "model.pkl")

    # Build snapshot
    snapshot = {
        "experiment_id": experiment_id,
        "date": date_str,
        "git_tag": GIT_TAG,
        "git_commit": git_commit,
        "model_id": MODEL_ID,
        "model_checksum": model_checksum,
        "inference_count": signal_dist["total"],
        "buy_predictions": signal_dist["buy"],
        "sell_predictions": signal_dist["sell"],
        "hold_predictions": signal_dist["hold"],
        "executed_trades": trading["executed"],
        "blocked_threshold": 0,  # Derivable from ExecutionGuard logs
        "blocked_portfolio_context": 0,  # Derivable from Portfolio Context logs
        "blocked_risk": 0,  # Derivable from Risk Engine logs
        "blocked_execution_guard": 0,  # Derivable from ExecutionGuard logs
        "confidence_mean": confidence["mean"],
        "confidence_std": confidence["std"],
        "equity": equity_metrics["equity"],
        "pnl": equity_metrics["pnl"],
        "drawdown": equity_metrics["drawdown"],
        "open_positions": 1 if state.get("in_position") else 0,
        "closed_positions": trading["executed"],
        "trending_count": regime_dist.get("TRENDING", {}).get("count", 0),
        "ranging_count": regime_dist.get("RANGING", {}).get("count", 0),
        "feature_drift_score": None,  # Not available yet
        "runtime_hours": _compute_runtime_hours(date_str),
        "trading_metrics": trading,
        "regime_distribution": regime_dist,
        "state": {
            "last_bar": state.get("last_timestamp"),
            "total_trades": state.get("total_trades", 0),
            "free_balance": state.get("free_balance", 0),
            "peak_equity": state.get("peak_equity", 0),
        },
    }

    return snapshot


def save_snapshot(snapshot: dict, date_str: str | None = None) -> Path:
    """Save snapshot to reports/forward-test/YYYY-MM-DD.json."""
    if date_str is None:
        date_str = snapshot.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    filepath = REPORTS_DIR / f"{date_str}.json"

    with open(filepath, "w") as f:
        json.dump(snapshot, f, indent=2, default=str)

    return filepath


def generate_report(snapshot: dict) -> str:
    """Generate a text report from a snapshot."""
    pnl_str = f"+{snapshot['pnl']}" if snapshot['pnl'] >= 0 else str(snapshot['pnl'])
    drawdown_str = f"{snapshot['drawdown']}%"

    # Determine dominant regime
    trending = snapshot.get("trending_count", 0)
    ranging = snapshot.get("ranging_count", 0)
    total_regime = trending + ranging
    if total_regime > 0:
        dominant = "TRENDING" if trending > ranging else "RANGING"
        dominant_pct = round(max(trending, ranging) / total_regime * 100, 1)
    else:
        dominant = "unknown"
        dominant_pct = 0

    # Health status
    health = "OK"
    if snapshot.get("drawdown", 0) > 5:
        health = "WARNING (drawdown > 5%)"
    if snapshot.get("executed_trades", 0) == 0 and snapshot.get("inference_count", 0) > 10:
        health = "WARNING (no trades despite inferences)"

    report = f"""================================================
Forward Test Daily Report
================================================
Versión:     {snapshot.get('git_tag', 'unknown')}
Commit:      {snapshot.get('git_commit', 'unknown')}
Tag:         {snapshot.get('git_tag', 'unknown')}
Modelo:      {snapshot.get('model_id', 'unknown')}
Tiempo:      {snapshot.get('runtime_hours', 0)}h
------------------------------------------------
Inferencias: {snapshot.get('inference_count', 0)}
Trades:      {snapshot.get('executed_trades', 0)} ejecutados / {snapshot.get('blocked_threshold', 0) + snapshot.get('blocked_portfolio_context', 0) + snapshot.get('blocked_risk', 0) + snapshot.get('blocked_execution_guard', 0)} bloqueados
BUY: {snapshot.get('buy_predictions', 0)}  SELL: {snapshot.get('sell_predictions', 0)}  HOLD: {snapshot.get('hold_predictions', 0)}
Confianza:   {snapshot.get('confidence_mean', 0):.4f} ± {snapshot.get('confidence_std', 0):.4f}
------------------------------------------------
PnL:         {pnl_str}
Drawdown:    {drawdown_str}
Equity:      {snapshot.get('equity', 0):.2f}
Régimen:     {dominant} ({dominant_pct}%)
------------------------------------------------
Observaciones: Ninguna
Anomalías:   Ninguna
Health:      {health}
================================================"""

    return report


def main():
    parser = argparse.ArgumentParser(description="Generate daily snapshot for Forward Test Experiment 001")
    parser.add_argument("--date", help="Date to generate snapshot for (YYYY-MM-DD)", default=None)
    parser.add_argument("--experiment-id", default=EXPERIMENT_ID, help="Experiment ID")
    parser.add_argument("--report", action="store_true", help="Print text report to stdout")
    args = parser.parse_args()

    snapshot = generate_snapshot(date_str=args.date, experiment_id=args.experiment_id)
    filepath = save_snapshot(snapshot, date_str=args.date)

    if args.report:
        print(generate_report(snapshot))
    else:
        print(f"Snapshot saved → {filepath}")
        print(f"  Experiment: {snapshot['experiment_id']}")
        print(f"  Date: {snapshot['date']}")
        print(f"  Inferences: {snapshot['inference_count']}")
        print(f"  Trades: {snapshot['executed_trades']}")
        print(f"  PnL: {snapshot['pnl']}")
        print(f"  Drawdown: {snapshot['drawdown']}%")


if __name__ == "__main__":
    main()
