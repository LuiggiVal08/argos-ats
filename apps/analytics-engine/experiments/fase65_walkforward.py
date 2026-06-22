"""FASE 6.5.1 — Walk-Forward Validation.

4 folds expanding window, config congelada de FASE 5.5:
  threshold=0.6, ADX=0, SL=2 ATR, TP=4 ATR, risk_pct=1%

Protección:
  - Features pre-computadas (backward-looking, seguras)
  - Últimas 5 barras del train excluidas (leakage de labels)
  - Scaler fit en train, transform en test
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR / "experiments"))
from fase55_backtest_engine import BacktestEngine
from fase55_feature_selection import load_data, ALL_NAMES

REPORT_DIR = BASE_DIR / "reports" / "walkforward"
REPORT_DIR.mkdir(parents=True, exist_ok=True)
RETAINED_FILE = BASE_DIR / "data" / "retained_features.txt"

# NOTE: data starts at 2022-01-01 (not 2019), so folds are adapted
# to the available range while preserving expanding-window structure
FOLDS = [
    {"train_start": "2022-01-01", "train_end": "2023-06-30", "test_start": "2023-07-01", "test_end": "2023-12-31"},
    {"train_start": "2022-01-01", "train_end": "2023-12-31", "test_start": "2024-01-01", "test_end": "2024-06-30"},
    {"train_start": "2022-01-01", "train_end": "2024-06-30", "test_start": "2024-07-01", "test_end": "2024-12-31"},
    {"train_start": "2022-01-01", "train_end": "2024-12-31", "test_start": "2025-01-01", "test_end": "2025-12-31"},
]

LABEL_LOOKAHEAD = 5  # shift(-5) in feature_selection.py
WARM_START_BARS = 100  # bars before test_start to include for rolling indicators


def get_year_index_mask(timestamps: pd.Series, year: int) -> np.ndarray:
    """Return boolean mask for bars in a given calendar year."""
    if isinstance(timestamps.iloc[0], (int, float, np.integer, np.floating)):
        ts_dt = pd.to_datetime(timestamps, unit="ns")
    else:
        ts_dt = pd.to_datetime(timestamps)
    return ts_dt.dt.year == year


def get_date_range_mask(timestamps: pd.Series, start: str, end: str) -> np.ndarray:
    """Return boolean mask for bars between two ISO dates (inclusive)."""
    if isinstance(timestamps.iloc[0], (int, float, np.integer, np.floating)):
        ts_dt = pd.to_datetime(timestamps, unit="ns")
    else:
        ts_dt = pd.to_datetime(timestamps)
    return (ts_dt >= start) & (ts_dt <= end)


def load_retained_features() -> list[str]:
    with open(RETAINED_FILE) as f:
        return [line.strip() for line in f if line.strip()]


def compute_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, window: int = 14) -> np.ndarray:
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - close[:-1]),
                               np.abs(low[1:] - close[:-1])))
    tr_full = np.concatenate([[tr[0]], tr])
    atr = pd.Series(tr_full).rolling(window).mean().values
    return np.nan_to_num(atr, nan=0.0)


def run_fold(fold_id: int, fold: dict,
             X: np.ndarray, y: np.ndarray,
             close: np.ndarray, high: np.ndarray, low: np.ndarray,
             timestamps: pd.Series, full_labels: np.ndarray,
             retained_idx: list[int]) -> dict:
    """Run a single walk-forward fold."""
    train_mask = get_date_range_mask(timestamps, fold["train_start"], fold["train_end"])
    test_mask = get_date_range_mask(timestamps, fold["test_start"], fold["test_end"])

    train_idx_full = np.where(train_mask)[0]
    test_idx = np.where(test_mask)[0]

    if len(train_idx_full) < 100 or len(test_idx) < 100:
        return {"fold": fold_id, "error": f"Insufficient data: train={len(train_idx_full)}, test={len(test_idx)}"}

    # Exclude last LABEL_LOOKAHEAD bars from train (label leakage protection)
    train_idx = train_idx_full[:-LABEL_LOOKAHEAD] if len(train_idx_full) > LABEL_LOOKAHEAD else train_idx_full

    # Build full proba array: predictions for test, 0 elsewhere
    proba_full = np.zeros(len(close))

    # Filter to retained features
    X_r = X[:, retained_idx]

    # Train
    scaler = StandardScaler()
    X_train = scaler.fit_transform(X_r[train_idx])
    y_train = full_labels[train_idx]

    # Filter -1 labels (HOLD/no-trade)
    train_valid = y_train != -1
    if train_valid.sum() < 50:
        return {"fold": fold_id, "error": f"Insufficient valid training labels: {train_valid.sum()}"}

    rf = RandomForestClassifier(max_depth=7, n_estimators=100, class_weight="balanced", random_state=42, n_jobs=-1)
    rf.fit(X_train[train_valid], y_train[train_valid])

    # Predict on test
    X_test = scaler.transform(X_r[test_idx])
    test_valid_mask = full_labels[test_idx] != -1
    if test_valid_mask.sum() > 0:
        proba_full[test_idx[test_valid_mask]] = rf.predict_proba(X_test[test_valid_mask])[:, 1]

    # ATR (computed on full data, safe — backward-looking)
    atr_full = compute_atr(high, low, close)

    # Run backtest on test period
    engine = BacktestEngine(
        sl_mult=2.0, tp_mult=4.0, risk_pct=0.01,
        min_prob=0.6, adx_threshold=0,
        fee=0.001, slippage=0.0005, spread=0.0001,
    )

    # Engine needs test data + proba for test only
    test_slice_start = test_idx[0]
    test_slice_end = test_idx[-1] + 1
    result = engine.run(
        close[test_slice_start:test_slice_end],
        high[test_slice_start:test_slice_end],
        low[test_slice_start:test_slice_end],
        timestamps.iloc[test_slice_start:test_slice_end].values,
        proba_full[test_slice_start:test_slice_end],
        atr_values=atr_full[test_slice_start:test_slice_end],
    )

    # Extract trade data
    trade_pnls = [t.pnl_usd for t in result.trades if t.exit_reason != "END"]
    trade_bars = [t.bars_held for t in result.trades if t.exit_reason != "END"]

    fold_result = {
        "fold": fold_id,
        "train_start": fold["train_start"],
        "train_end": fold["train_end"],
        "test_start": fold["test_start"],
        "test_end": fold["test_end"],
        "n_train_bars": len(train_idx),
        "n_train_valid": int(train_valid.sum()),
        "n_test_bars": len(test_idx),
        "n_trades": result.n_trades,
        "win_rate": result.win_rate,
        "total_return_pct": result.total_return_pct,
        "sharpe": result.sharpe,
        "sortino": result.sortino,
        "calmar": result.calmar,
        "max_dd_pct": result.max_dd_pct,
        "profit_factor": result.profit_factor,
        "expectancy": result.expectancy,
        "cagr": result.cagr,
        "avg_bars_held": result.avg_bars_held,
        "max_consecutive_losses": result.max_consecutive_losses,
        "max_consecutive_wins": result.max_consecutive_wins,
        "turnover_per_day": result.turnover_per_day,
        "exposure_pct": result.exposure_pct,
        "time_in_market_pct": result.time_in_market_pct,
        "avg_win": result.avg_win,
        "avg_loss": result.avg_loss,
        "trade_pnls": trade_pnls,
        "trade_bars": trade_bars,
    }

    return fold_result


def main():
    t0 = time.time()
    print("=" * 60)
    print("FASE 6.5.1 — Walk-Forward Validation")
    print("=" * 60)
    print("Config: threshold=0.6, ADX=0, SL=2 ATR, TP=4 ATR, risk=1%")

    # Load retained features
    retained = load_retained_features()
    retained_set = set(retained)
    names = list(ALL_NAMES)
    retained_idx = [i for i, name in enumerate(names) if name in retained_set]
    print(f"Retained features: {len(retained)}/{len(names)}")

    # Load data
    print("\nLoading data...")
    df, X, y, close, high, low, volume = load_data()
    timestamps = df["timestamp"]
    print(f"Data shape: {X.shape}, range: {timestamps.iloc[0]} to {timestamps.iloc[-1]}")

    # y already contains labels (-1, 0, 1)
    full_labels = y.astype(np.int32)

    results = []
    for fold_id, fold in enumerate(FOLDS):
        print(f"\n{'=' * 50}")
        print(f"Fold {fold_id}: Train {fold['train_start']}→{fold['train_end']}, Test {fold['test_start']}→{fold['test_end']}")
        print(f"{'=' * 50}")

        f_result = run_fold(fold_id, fold, X, y, close, high, low, timestamps, full_labels, retained_idx)
        results.append(f_result)

        if "error" in f_result:
            print(f"  ERROR: {f_result['error']}")
            continue

        print(f"  Trades: {f_result['n_trades']}")
        print(f"  Win rate: {f_result['win_rate']:.2%}")
        print(f"  Return: {f_result['total_return_pct']:.2f}%")
        print(f"  CAGR: {f_result['cagr']:.2f}%")
        print(f"  Sharpe: {f_result['sharpe']:.2f}")
        print(f"  Sortino: {f_result['sortino']:.2f}")
        print(f"  Calmar: {f_result['calmar']:.2f}")
        print(f"  Max DD: {f_result['max_dd_pct']:.2f}%")
        print(f"  Profit Factor: {f_result['profit_factor']:.2f}")
        print(f"  Expectancy: ${f_result['expectancy']:.2f}")
        print(f"  Turnover/day: {f_result['turnover_per_day']:.2f}")

        # Save per-fold report (without trade lists to keep it manageable)
        fold_report = {k: v for k, v in f_result.items() if k not in ("trade_pnls", "trade_bars")}
        fold_report_path = REPORT_DIR / f"fold_{fold_id}.json"
        with open(fold_report_path, "w") as f:
            json.dump(fold_report, f, indent=2, default=str)
        print(f"  Saved: {fold_report_path}")

        # Save trade data separately (for Monte Carlo)
        trades_path = REPORT_DIR / f"fold_{fold_id}_trades.json"
        with open(trades_path, "w") as f:
            json.dump({"trade_pnls": f_result.get("trade_pnls", []),
                       "trade_bars": f_result.get("trade_bars", []),
                       "n_test_bars": f_result.get("n_test_bars", 0),
                       "total_return_pct": f_result.get("total_return_pct", 0),
                       "n_trades": f_result.get("n_trades", 0)}, f, indent=2)
        print(f"  Trades saved: {trades_path}")

    # Summary
    print(f"\n{'=' * 60}")
    print("WALK-FORWARD SUMMARY")
    print(f"{'=' * 60}")
    metrics = ["n_trades", "win_rate", "total_return_pct", "cagr", "sharpe", "sortino",
               "calmar", "max_dd_pct", "profit_factor", "expectancy", "turnover_per_day"]
    header = f"{'Fold':<6} " + " ".join(f"{m:<16}" for m in metrics)
    print(header)
    print("-" * len(header))
    for r in results:
        if "error" in r:
            print(f"{r['fold']:<6} ERROR")
            continue
        vals = " ".join(f"{r.get(m, 0):<16.4f}" if isinstance(r.get(m), (int, float)) and m != "n_trades" else f"{r.get(m, 0):<16}" for m in metrics)
        print(f"{r['fold']:<6} {vals}")

    # Aggregate
    successful = [r for r in results if "error" not in r]
    if successful:
        summary = {
            "n_folds": len(FOLDS),
            "n_successful": len(successful),
            "results": results,
            "mean_metrics": {m: float(np.mean([r[m] for r in successful if m in r])) for m in metrics},
            "std_metrics": {m: float(np.std([r[m] for r in successful if m in r])) for m in metrics},
            "elapsed_seconds": time.time() - t0,
            "config": {
                "threshold": 0.6, "adx_threshold": 0,
                "sl_mult": 2.0, "tp_mult": 4.0, "risk_pct": 0.01,
                "fee": 0.001, "slippage": 0.0005, "spread": 0.0001,
                "n_features_retained": len(retained),
            },
        }
        summary_path = REPORT_DIR / "summary.json"
        with open(summary_path, "w") as f:
            json.dump(summary, f, indent=2, default=str)
        print(f"\nSummary: {summary_path}")
        print(f"Elapsed: {time.time() - t0:.1f}s")

        # Quick pass/fail assessment
        n_pass = sum(1 for r in successful if r.get("calmar", 0) > 1.0 and r.get("sharpe", 0) > 1.0)
        print(f"\nQuick check: {n_pass}/{len(successful)} folds pass Calmar>1 + Sharpe>1")

    return results


if __name__ == "__main__":
    main()
