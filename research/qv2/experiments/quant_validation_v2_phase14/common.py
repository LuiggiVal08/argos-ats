"""Phase 14 — Lock Test (strict chronological walk-forward).

Frozen LR + RobustScaler trained on pre-cutoff data.
Features pre-computed once (bfill only affects warmup rows ≪ cutoff).
Post-cutoff: feature lookup by position → scale → predict → compare.
No O(n²) recomputation needed — TA rolling ops are backward-safe at any index.
"""

from __future__ import annotations

import json
import logging
import time
import traceback
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import RobustScaler

from experiments.quant_validation_v2.common import (
    build_feature_matrix, compute_base_ta, compute_mtf_features,
    compute_funding_features, label_binary,
)
from experiments.quant_validation_v2_phase38.common import load_ohlcv, load_funding

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

logger = logging.getLogger("phase14")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

REPORT_DIR = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v2_phase14"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = ["BTC", "ETH", "SOL"]
EXCHANGE = "binance"
CUTOFF_DATE = "2025-06-01"
LOOKAHEAD = 5
STRIDE = 5
BUY_THRESHOLD = 0.60
SELL_THRESHOLD = 0.40
COST_PER_SIDE = 0.00155

LR_PARAMS = {
    "C": 0.1,
    "class_weight": "balanced",
    "max_iter": 5000,
    "random_state": 42,
    "solver": "liblinear",
}


def train_frozen_model(
    ohlcv: pd.DataFrame,
    funding: pd.DataFrame | None,
) -> tuple[LogisticRegression, RobustScaler, np.ndarray, list[str]]:
    cutoff = pd.Timestamp(CUTOFF_DATE)
    train_ohlcv = ohlcv[ohlcv["timestamp"] < cutoff]

    logger.info(f"[train] {len(train_ohlcv)} bars before {CUTOFF_DATE}")

    X_vals, X_index, feature_names, X_df = build_feature_matrix(train_ohlcv, funding)
    y_labels, y_valid = label_binary(train_ohlcv, lookahead=LOOKAHEAD, threshold=0.5)

    # Align lengths (features may be shorter due to warmup)
    min_len = min(len(X_vals), len(y_labels))
    X_vals = X_vals[:min_len]
    y_labels = y_labels[:min_len]

    valid = y_valid[:min_len]
    X_vals = X_vals[valid]
    y_labels = y_labels[valid]

    # Drop any rows with NaN
    nan_mask = np.isnan(X_vals).any(axis=1)
    X_vals = X_vals[~nan_mask]
    y_labels = y_labels[~nan_mask]

    logger.info(f"[train] X: {X_vals.shape}, y: {y_labels.sum()} pos / "
                f"{(1 - y_labels).sum()} neg")

    scaler = RobustScaler()
    X_scaled = scaler.fit_transform(X_vals)

    lr = LogisticRegression(**LR_PARAMS)
    lr.fit(X_scaled, y_labels)
    train_acc = lr.score(X_scaled, y_labels)
    logger.info(f"[train] accuracy={train_acc:.4f}  features={X_vals.shape[1]}")

    return lr, scaler, X_vals, feature_names


def compute_features_for_symbol(
    ohlcv: pd.DataFrame,
    funding: pd.DataFrame | None,
    cutoff: pd.Timestamp,
) -> tuple[pd.DataFrame, np.ndarray, pd.DataFrame]:
    """Compute full feature matrix for a symbol, split into train/test by cutoff."""
    # Build features — bfill/ffill only affects warmup rows ≪ cutoff
    X_vals, X_index, feature_names, X_df = build_feature_matrix(ohlcv, funding)
    y_labels, y_valid = label_binary(ohlcv, lookahead=LOOKAHEAD, threshold=0.5)

    # Align
    min_len = min(len(X_df), len(y_labels))
    X_df = X_df.iloc[:min_len]
    y_labels = y_labels[:min_len]
    y_valid = y_valid[:min_len]

    timestamps = ohlcv["timestamp"].values[:min_len]
    train_mask = pd.to_datetime(timestamps) < cutoff

    # For test, only include rows where both features and labels are valid
    test_ohlcv = ohlcv.iloc[:min_len]
    test_df = X_df.copy()
    test_df["timestamp"] = timestamps
    test_df["y_label"] = y_labels
    test_df["y_valid"] = y_valid

    train_df = test_df[train_mask].copy()
    test_df = test_df[~train_mask].copy()

    logger.info(f"[{ohlcv.iloc[0].get('symbol', '?')}] train: {len(train_df)} rows, "
                f"test: {len(test_df)} rows")
    logger.info(f"  feature_names: {feature_names}")

    return train_df, y_labels[train_mask], test_df, feature_names


def walk_forward_single(
    lr: LogisticRegression,
    scaler: RobustScaler,
    test_df: pd.DataFrame,
    feature_names: list[str],
    symbol: str,
) -> list[dict]:
    """Predict bar by bar on test set using frozen model."""
    results: list[dict] = []
    feature_cols = [c for c in feature_names if c in test_df.columns]

    for idx, row in test_df.iterrows():
        ts = row["timestamp"]
        y_actual = int(row["y_label"])

        X_dict = {col: row[col] for col in feature_cols}
        X = pd.DataFrame([X_dict])

        if X.isna().any(axis=1).any():
            continue

        X_scaled = scaler.transform(X)
        y_proba = lr.predict_proba(X_scaled)[0, 1]
        y_pred = lr.predict(X_scaled)[0]

        results.append({
            "timestamp": str(ts),
            "symbol": symbol,
            "y_proba": round(float(y_proba), 6),
            "y_pred": int(y_pred),
            "y_actual": int(y_actual),
            "correct": bool(y_pred == y_actual),
        })

    return results


def compute_metrics(results: list[dict]) -> dict:
    if len(results) == 0:
        return {"status": "no_predictions"}

    df = pd.DataFrame(results)
    n = len(df)

    # Filter to non-HOLD only (y_actual ∈ {0, 1})
    directional = df[df["y_actual"].isin([0, 1])].copy()
    n_directional = len(directional)

    correct = directional["correct"].sum()
    nolabel_correct = df[~df["y_actual"].isin([0, 1])]["correct"].sum() if n > n_directional else 0

    tp = int(((directional["y_pred"] == 1) & (directional["y_actual"] == 1)).sum())
    fp = int(((directional["y_pred"] == 1) & (directional["y_actual"] == 0)).sum())
    tn = int(((directional["y_pred"] == 0) & (directional["y_actual"] == 0)).sum())
    fn = int(((directional["y_pred"] == 0) & (directional["y_actual"] == 1)).sum())

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    specificity = tn / (tn + fp) if (tn + fp) > 0 else 0.0
    directional_acc = correct / n_directional if n_directional > 0 else 0.0
    overall_acc = (correct + nolabel_correct) / n if n > 0 else 0.0

    long_mask = directional["y_pred"] == 1
    short_mask = directional["y_pred"] == 0

    return {
        "n_predictions": n,
        "n_directional": n_directional,
        "n_hold": n - n_directional if n > n_directional else 0,
        "directional_accuracy": round(float(directional_acc), 6),
        "overall_accuracy": round(float(overall_acc), 6),
        "precision": round(float(precision), 6),
        "recall": round(float(recall), 6),
        "f1": round(float(f1), 6),
        "specificity": round(float(specificity), 6),
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
    }


def compute_backtest_metrics(results: list[dict], ohlcv: pd.DataFrame) -> dict:
    """Compute simple backtest metrics over the test period."""
    if len(results) == 0:
        return {}
    df = pd.DataFrame(results)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp")

    ohlcv_ts = ohlcv.set_index("timestamp")

    trade_pnls = []
    balance = 100_000.0
    for _, row in df.iterrows():
        ts = row["timestamp"]
        if ts not in ohlcv_ts.index:
            continue
        entry_close = ohlcv_ts.loc[ts, "close"]

        # Find close after LOOKAHEAD bars
        ts_idx = ohlcv_ts.index.get_loc(ts)
        exit_idx = min(ts_idx + LOOKAHEAD, len(ohlcv_ts) - 1)
        exit_ts = ohlcv_ts.index[exit_idx]
        exit_close = ohlcv_ts.loc[exit_ts, "close"]

        gross_return = exit_close / entry_close - 1.0
        side = 1 if row["y_pred"] == 1 else -1
        gross_pnl = balance * 0.33 * gross_return * side
        entry_cost = balance * 0.33 * COST_PER_SIDE
        exit_cost = balance * 0.33 * COST_PER_SIDE
        net_pnl = gross_pnl - entry_cost - exit_cost

        trade_pnls.append({
            "entry_ts": str(ts), "exit_ts": str(exit_ts),
            "side": "LONG" if side == 1 else "SHORT",
            "gross_return": round(float(gross_return), 6),
            "net_pnl": round(float(net_pnl), 2),
            "correct": bool(row["correct"]),
        })
        balance += net_pnl

    df_trades = pd.DataFrame(trade_pnls)
    wins = df_trades[df_trades["net_pnl"] > 0]
    losses = df_trades[df_trades["net_pnl"] <= 0]
    win_rate = len(wins) / len(df_trades) if len(df_trades) > 0 else 0.0
    profit_factor = abs(wins["net_pnl"].sum()) / abs(losses["net_pnl"].sum() + 1e-10)
    total_return = balance / 100_000.0 - 1.0

    return {
        "backtest_final_balance": round(float(balance), 2),
        "backtest_return": round(float(total_return), 6),
        "backtest_win_rate": round(float(win_rate), 4),
        "backtest_profit_factor": round(float(profit_factor), 4),
        "backtest_n_trades": len(trade_pnls),
        "backtest_n_wins": int(len(wins)),
    }


def process_symbol(
    symbol: str,
    lr: LogisticRegression,
    scaler: RobustScaler,
    base_feature_names: list[str],
) -> dict:
    logger.info(f"\n{'─' * 50}")
    logger.info(f"  Processing: {symbol}")

    ohlcv = load_ohlcv(symbol, EXCHANGE)
    ohlcv["timestamp"] = pd.to_datetime(ohlcv["timestamp"])
    ohlcv = ohlcv.sort_values("timestamp").reset_index(drop=True)

    funding = load_funding(symbol, EXCHANGE)
    if funding is not None:
        funding["timestamp"] = pd.to_datetime(funding["timestamp"])
    else:
        # Use BTC funding as proxy for non-BTC symbols
        try:
            btc_funding = load_funding("BTC", EXCHANGE)
            btc_funding["timestamp"] = pd.to_datetime(btc_funding["timestamp"])
            funding = btc_funding
        except Exception:
            funding = pd.DataFrame(columns=["timestamp", "fundingRate"])

    cutoff = pd.Timestamp(CUTOFF_DATE)

    # Build features
    t1 = time.time()
    X_vals, X_index, feature_names, X_df = build_feature_matrix(ohlcv, funding)
    logger.info(f"  Features: {X_df.shape} in {time.time()-t1:.1f}s")

    # Align labels
    y_labels, y_valid = label_binary(ohlcv, lookahead=LOOKAHEAD, threshold=0.5)
    min_len = min(len(X_df), len(y_labels))
    X_df = X_df.iloc[:min_len]
    y_labels = y_labels[:min_len]

    timestamps = ohlcv["timestamp"].values[:min_len]
    train_mask = pd.to_datetime(timestamps) < cutoff

    test_mask = ~train_mask
    test_df = X_df[test_mask].copy()
    test_timestamps = timestamps[test_mask]
    test_y = y_labels[test_mask]

    logger.info(f"  Test rows: {len(test_df)}")

    # Predict bar by bar
    feature_cols = [c for c in base_feature_names if c in test_df.columns]
    results: list[dict] = []

    for i in range(len(test_df)):
        row = test_df.iloc[i]
        y_actual = int(test_y[i])

        X_dict = {col: row[col] for col in feature_cols}
        X = pd.DataFrame([X_dict])

        if X.isna().any(axis=1).any():
            continue

        X_scaled = scaler.transform(X)
        y_proba = lr.predict_proba(X_scaled)[0, 1]
        y_pred = lr.predict(X_scaled)[0]

        results.append({
            "timestamp": str(test_timestamps[i]),
            "symbol": symbol,
            "y_proba": round(float(y_proba), 6),
            "y_pred": int(y_pred),
            "y_actual": int(y_actual),
            "correct": bool(y_pred == y_actual),
        })

    metrics = compute_metrics(results)
    bt_metrics = compute_backtest_metrics(results, ohlcv)
    metrics.update(bt_metrics)

    # Compare with Phase 5 predictions for the same period
    phase5_path = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v2_phase5"
    p5_path = phase5_path / f"{symbol.lower()}_predictions.parquet"
    if p5_path.exists():
        p5_df = pd.read_parquet(p5_path)
        p5_cutoff = p5_df[pd.to_datetime(p5_df["timestamp"]) >= cutoff]
        if len(p5_cutoff) > 0 and "y_true" in p5_cutoff.columns:
            tp_p5 = ((p5_cutoff["y_pred"] == 1) & (p5_cutoff["y_true"] == 1)).sum()
            fp_p5 = ((p5_cutoff["y_pred"] == 1) & (p5_cutoff["y_true"] == 0)).sum()
            fn_p5 = ((p5_cutoff["y_pred"] == 0) & (p5_cutoff["y_true"] == 1)).sum()
            p5_prec = tp_p5 / (tp_p5 + fp_p5) if (tp_p5 + fp_p5) > 0 else 0.0
            p5_rec = tp_p5 / (tp_p5 + fn_p5) if (tp_p5 + fn_p5) > 0 else 0.0
            p5_f1 = 2 * p5_prec * p5_rec / (p5_prec + p5_rec) if (p5_prec + p5_rec) > 0 else 0.0
            p5_acc = (p5_cutoff["y_pred"] == p5_cutoff["y_true"]).mean()
            metrics["phase5_comparison"] = {
                "accuracy": round(float(p5_acc), 6),
                "f1": round(float(p5_f1), 6),
                "n_predictions": int(len(p5_cutoff)),
            }

    logger.info(f"  Directional Acc: {metrics['directional_accuracy']:.4%}  "
                f"Overall Acc: {metrics['overall_accuracy']:.4%}  "
                f"F1: {metrics['f1']:.4f}")
    logger.info(f"  N={metrics['n_predictions']}  "
                f"directional={metrics['n_directional']}  "
                f"hold={metrics['n_hold']}")
    if "backtest_return" in metrics:
        logger.info(f"  Backtest: ${metrics.get('backtest_final_balance', 0):,.0f}  "
                    f"Return: {metrics['backtest_return']:.2%}")
    if "phase5_comparison" in metrics:
        p5 = metrics["phase5_comparison"]
        logger.info(f"  Phase 5 on same period: acc={p5['accuracy']:.4%}  F1={p5['f1']:.4f}")

    return {"symbol": symbol, "metrics": metrics, "results": results}


def main():
    logger.info("╔══════════════════════════════════════╗")
    logger.info("║  Phase 14 — Lock Test                ║")
    logger.info(f"║  Cutoff: {CUTOFF_DATE}  Symbols: {len(SYMBOLS)}        ║")
    logger.info("╚══════════════════════════════════════╝")
    t0 = time.time()

    # ── Train frozen model on BTC ────────────────────────────
    logger.info("\n[training] BTC (frozen model)")
    btc_ohlcv = load_ohlcv("BTC", EXCHANGE)
    btc_ohlcv["timestamp"] = pd.to_datetime(btc_ohlcv["timestamp"])
    btc_ohlcv = btc_ohlcv.sort_values("timestamp").reset_index(drop=True)

    # Use BTC funding for training
    btc_funding = load_funding("BTC", EXCHANGE)
    if btc_funding is not None:
        btc_funding["timestamp"] = pd.to_datetime(btc_funding["timestamp"])

    lr, scaler, X_train_vals, feature_names = train_frozen_model(btc_ohlcv, btc_funding)

    # ── Walk forward per symbol ──────────────────────────────
    all_symbol_results = []
    all_predictions = []

    for sym in SYMBOLS:
        result = process_symbol(sym, lr, scaler, feature_names)
        all_symbol_results.append({
            "symbol": sym,
            "metrics": result["metrics"],
        })
        all_predictions.extend(result["results"])

    # ── Global assessment ────────────────────────────────────
    accuracy_mean = np.mean([r["metrics"]["directional_accuracy"] for r in all_symbol_results])
    f1_mean = np.mean([r["metrics"]["f1"] for r in all_symbol_results])
    accuracy_std = np.std([r["metrics"]["directional_accuracy"] for r in all_symbol_results])

    # Compare with global Phase 5 benchmark
    avg_p5_acc = np.mean([
        r["metrics"].get("phase5_comparison", {}).get("accuracy", 0)
        for r in all_symbol_results
    ]) if all_symbol_results else 0
    avg_p5_f1 = np.mean([
        r["metrics"].get("phase5_comparison", {}).get("f1", 0)
        for r in all_symbol_results
    ]) if all_symbol_results else 0

    degradation = accuracy_mean - avg_p5_acc

    elapsed = time.time() - t0

    report = {
        "cutoff_date": CUTOFF_DATE,
        "model_params": LR_PARAMS,
        "feature_count": len(feature_names),
        "per_symbol": all_symbol_results,
        "global": {
            "mean_directional_accuracy": round(float(accuracy_mean), 6),
            "mean_f1": round(float(f1_mean), 6),
            "directional_accuracy_std": round(float(accuracy_std), 6),
            "vs_phase5_mean_accuracy": round(float(avg_p5_acc), 6),
            "accuracy_degradation_vs_phase5": round(float(degradation), 6),
        },
        "elapsed_seconds": round(elapsed, 1),
    }

    out_path = REPORT_DIR / "lock_final.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info(f"[report] saved {out_path}")

    pd.DataFrame(all_predictions).to_parquet(
        REPORT_DIR / "lock_predictions.parquet", index=False)

    # ── Lock test verdict ────────────────────────────────────
    logger.info(f"\n{'=' * 50}")
    logger.info(f"  LOCK TEST RESULTS")
    logger.info(f"{'=' * 50}")
    logger.info(f"  Mean Directional Accuracy: {accuracy_mean:.4%} ± {accuracy_std:.4%}")
    logger.info(f"  Mean F1:                   {f1_mean:.4f}")
    logger.info(f"  Phase 5 Acc:               {avg_p5_acc:.4%}")
    logger.info(f"  Degradation:               {degradation:+.4%}")
    logger.info(f"")
    if degradation >= -0.05:
        logger.info(f"  VERDICT: PASS — Lock test passed (degradation ≤ 5pp)")
    elif degradation >= -0.10:
        logger.info(f"  VERDICT: WARNING — Moderate degradation (5-10pp)")
    else:
        logger.info(f"  VERDICT: HARD FAIL — Degradation exceeds 10pp")
    logger.info(f"  Elapsed: {elapsed:.0f}s")
    logger.info("Phase 14 complete")


if __name__ == "__main__":
    main()
