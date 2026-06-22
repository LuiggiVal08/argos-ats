"""Phase 5 — Portfolio Validation common utilities.

Custom walk-forward runner that preserves per-fold predictions (y_true, y_pred, y_proba,
forward_return, timestamps) in addition to aggregated metrics.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import (
    accuracy_score, f1_score, roc_auc_score,
    cohen_kappa_score, matthews_corrcoef,
    precision_score, recall_score,
    confusion_matrix,
)

from experiments.quant_validation_v2_phase35.common import (
    N_FOLDS,
    RANDOM_STATE,
    build_feature_matrix as _bfm,
    subsample_indices,
    walk_forward_splits_phase35,
)

# ── paths ──────────────────────────────────────────────────────────

REPORT_DIR = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v2_phase5"
REPORT_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR = Path(__file__).parent.parent.parent / "apps" / "analytics-engine" / "data"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("phase5")


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


# ── model factories ────────────────────────────────────────────────


def make_lr():
    return LogisticRegression(max_iter=1000, random_state=RANDOM_STATE, class_weight="balanced", n_jobs=1)


# ── FoldPrediction dataclass ──────────────────────────────────────


@dataclass
class FoldPrediction:
    fold: int
    test_start: int
    test_end: int
    timestamps: list[str] = field(default_factory=list)
    y_true: list[int] = field(default_factory=list)
    y_pred: list[int] = field(default_factory=list)
    y_proba: list[float] = field(default_factory=list)
    forward_return: list[float] = field(default_factory=list)
    indices: list[int] = field(default_factory=list)


@dataclass
class ModelResult:
    model_name: str = ""
    folds: list[dict] = field(default_factory=list)
    avg_accuracy: float = 0.0
    avg_kappa: float = 0.0
    avg_mcc: float = 0.0
    avg_f1: float = 0.0
    avg_auc: float | None = None


# ── custom runner with probabilities ──────────────────────────────


def run_classification_with_probas(
    X: np.ndarray,
    y: np.ndarray,
    idx_orig: np.ndarray,
    ohlcv: pd.DataFrame,
    splits: list[dict],
    model_fn,
    labels: list[int] | None = None,
) -> tuple[ModelResult, list[FoldPrediction]]:
    """Walk-forward classification returning both aggregated metrics and per-fold predictions.

    Mirrors QV1's run_walk_forward_classification but additionally captures:
      - y_true, y_pred, y_proba (probability of positive class)
      - forward_return (raw 5-bar return)
      - timestamps from original OHLCV
    """
    if labels is None:
        labels = [0, 1]
    labels_for_cm = labels

    folds_metrics: list[dict] = []
    fold_predictions: list[FoldPrediction] = []

    for i, sp in enumerate(splits):
        sp["fold"] = i
        tr_s, tr_e = sp["train_start"], sp["train_end"]
        te_s, te_e = sp["test_start"], sp["test_end"]

        X_tr, y_tr = X[tr_s:tr_e], y[tr_s:tr_e]
        X_te, y_te = X[te_s:te_e], y[te_s:te_e]

        scaler = StandardScaler()
        X_tr_s = scaler.fit_transform(X_tr)
        X_te_s = scaler.transform(X_te)

        model = model_fn()
        model.fit(X_tr_s, y_tr)
        preds = model.predict(X_te_s)

        # Probabilities
        proba_pos: np.ndarray | None = None
        if hasattr(model, "predict_proba"):
            try:
                proba = model.predict_proba(X_te_s)
                if proba.shape[1] == 2:
                    pos_idx = list(model.classes_).index(max(model.classes_))
                    proba_pos = proba[:, pos_idx]
                else:
                    proba_pos = proba[:, 1] if proba.shape[1] > 1 else proba[:, 0]
            except Exception:
                proba_pos = None

        # Forward returns and timestamps
        te_orig_idxs = idx_orig[te_s:te_e]
        close = ohlcv["close"].values
        timestamps = ohlcv["timestamp"].values
        lookahead = 5
        fwd_returns = []
        ts_list = []
        valid_idxs = []
        valid_y_true = []
        valid_y_pred = []
        valid_y_proba = []

        for j, orig_idx in enumerate(te_orig_idxs):
            fwd_idx = orig_idx + lookahead
            if fwd_idx < len(close):
                fwd_ret = float(close[fwd_idx] / close[orig_idx] - 1.0)
            else:
                fwd_ret = 0.0
            ts = str(timestamps[orig_idx])
            fwd_returns.append(fwd_ret)
            ts_list.append(ts)
            valid_idxs.append(int(orig_idx))
            valid_y_true.append(int(y_te[j]))
            valid_y_pred.append(int(preds[j]))
            valid_y_proba.append(float(proba_pos[j]) if proba_pos is not None else 0.5)

        # Metrics for this fold
        cm = confusion_matrix(y_te, preds, labels=labels_for_cm).tolist()
        unique, counts = np.unique(preds, return_counts=True)
        pred_dist = {int(k): int(v) for k, v in zip(unique, counts)}
        auc_val = None
        if len(labels_for_cm) == 2 and proba_pos is not None:
            try:
                auc_val = float(roc_auc_score(y_te, proba_pos))
            except Exception:
                pass

        fold_metrics = {
            "fold": i,
            "train_start": int(tr_s),
            "train_end": int(tr_e),
            "test_start": int(te_s),
            "test_end": int(te_e),
            "n_train": len(y_tr),
            "n_test": len(y_te),
            "accuracy": float(accuracy_score(y_te, preds)),
            "kappa": float(cohen_kappa_score(y_te, preds)),
            "mcc": float(matthews_corrcoef(y_te, preds)),
            "f1_macro": float(f1_score(y_te, preds, average="macro", zero_division=0)),
            "precision_macro": float(precision_score(y_te, preds, average="macro", zero_division=0)),
            "recall_macro": float(recall_score(y_te, preds, average="macro", zero_division=0)),
            "auc": auc_val,
            "confusion": cm,
            "pred_dist": pred_dist,
        }
        folds_metrics.append(fold_metrics)

        fp = FoldPrediction(
            fold=i,
            test_start=int(te_s),
            test_end=int(te_e),
            timestamps=ts_list,
            y_true=valid_y_true,
            y_pred=valid_y_pred,
            y_proba=valid_y_proba,
            forward_return=fwd_returns,
            indices=valid_idxs,
        )
        fold_predictions.append(fp)

    # Aggregate metrics
    result = ModelResult(model_name="LogisticRegression", folds=folds_metrics)
    result.avg_accuracy = float(np.mean([f["accuracy"] for f in folds_metrics]))
    result.avg_kappa = float(np.mean([f["kappa"] for f in folds_metrics]))
    result.avg_mcc = float(np.mean([f["mcc"] for f in folds_metrics]))
    result.avg_f1 = float(np.mean([f["f1_macro"] for f in folds_metrics]))
    aucs = [f["auc"] for f in folds_metrics if f["auc"] is not None]
    result.avg_auc = float(np.mean(aucs)) if aucs else None

    logger.info(f"  → LR f1={result.avg_f1:.4f}  auc={result.avg_auc}  n_folds={len(folds_metrics)}")
    return result, fold_predictions


# ── save / load predictions ───────────────────────────────────────


def predictions_to_dataframe(fold_predictions: list[FoldPrediction]) -> pd.DataFrame:
    """Convert list of FoldPrediction to a single DataFrame."""
    rows = []
    for fp in fold_predictions:
        for i in range(len(fp.timestamps)):
            rows.append({
                "fold": fp.fold,
                "timestamp": fp.timestamps[i],
                "y_true": fp.y_true[i],
                "y_pred": fp.y_pred[i],
                "y_proba": fp.y_proba[i],
                "forward_return": fp.forward_return[i],
                "index_orig": fp.indices[i],
            })
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def save_predictions_parquet(fold_predictions: list[FoldPrediction], filename: str):
    path = REPORT_DIR / filename
    df = predictions_to_dataframe(fold_predictions)
    df.to_parquet(path, index=False)
    logger.info(f"[parquet] saved {len(df)} predictions to {path}")


def load_predictions_parquet(filename: str) -> pd.DataFrame | None:
    path = REPORT_DIR / filename
    if not path.exists():
        logger.warning(f"[parquet] not found: {path}")
        return None
    df = pd.read_parquet(path)
    logger.info(f"[parquet] loaded {len(df)} predictions from {path}")
    return df


# ── portfolio metrics ─────────────────────────────────────────────


def compute_trade_returns(df: pd.DataFrame, cost_per_side: float = 0.0) -> pd.Series:
    """Compute per-trade P&L from predictions and forward returns.

    BUY (y_pred=1):  ret = forward_return - cost
    SELL (y_pred=0): ret = -forward_return - cost

    Returns a Series with timestamp index.
    """
    ret = np.where(
        df["y_pred"] == 1,
        df["forward_return"].values - cost_per_side,
        -df["forward_return"].values - cost_per_side,
    )
    return pd.Series(ret, index=pd.to_datetime(df["timestamp"])).sort_index()


def equity_curve(trade_returns: pd.Series) -> pd.Series:
    """Compute equity curve from trade returns (starting at 1.0)."""
    return (1.0 + trade_returns).cumprod()


def compute_portfolio_metrics(
    trade_returns: pd.Series,
    name: str = "portfolio",
    annual_factor: float = 365.0,
) -> dict:
    """Compute CAGR, Sharpe, Sortino, Calmar, Max DD, Win Rate, Profit Factor.

    trade_returns: Series with DatetimeIndex of PER-TRADE returns.
    """
    if len(trade_returns) < 10:
        return {"name": name, "status": "insufficient_data", "n_trades": len(trade_returns)}

    # Convert to daily returns
    daily = trade_returns.resample("D").sum()
    daily = daily.dropna()

    if len(daily) < 5:
        return {"name": name, "status": "insufficient_data", "n_trades": len(trade_returns), "n_days": len(daily)}

    equity = (1.0 + daily).cumprod()
    total_return = equity.iloc[-1] / equity.iloc[0] - 1.0
    n_days = len(daily)
    years = n_days / annual_factor

    # CAGR
    if years > 0 and equity.iloc[0] > 0:
        cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0
    else:
        cagr = 0.0

    # Sharpe
    mean_daily = daily.mean()
    std_daily = daily.std()
    sharpe = (mean_daily / std_daily * np.sqrt(annual_factor)) if std_daily > 1e-10 else 0.0

    # Sortino
    downside = daily[daily < 0]
    downside_std = downside.std() if len(downside) > 1 else 0.0
    sortino = (mean_daily / downside_std * np.sqrt(annual_factor)) if downside_std > 1e-10 else 0.0

    # Max DD
    peak = equity.cummax()
    dd = (equity - peak) / peak
    max_dd = float(dd.min())

    # Calmar
    calmar = cagr / abs(max_dd) if max_dd < 0 else 0.0

    # Win rate (per trade, not per day)
    wins = (trade_returns > 0).sum()
    total = len(trade_returns)
    win_rate = wins / total if total > 0 else 0.0

    # Profit Factor
    gains = trade_returns[trade_returns > 0].sum()
    losses = trade_returns[trade_returns < 0].sum()
    profit_factor = float(gains / abs(losses)) if abs(losses) > 1e-10 else 0.0

    # Expectancy
    expectancy = float(trade_returns.mean())

    return {
        "name": name,
        "n_trades": len(trade_returns),
        "n_days": n_days,
        "total_return": round(float(total_return), 6),
        "cagr": round(float(cagr), 6),
        "sharpe": round(float(sharpe), 4),
        "sortino": round(float(sortino), 4),
        "calmar": round(float(calmar), 4),
        "max_dd": round(float(max_dd), 6),
        "win_rate": round(float(win_rate), 4),
        "profit_factor": round(float(profit_factor), 4),
        "expectancy": round(float(expectancy), 6),
        "mean_daily_return": round(float(mean_daily), 6),
        "std_daily_return": round(float(std_daily), 6),
    }


def compute_correlations(predictions: dict[str, pd.DataFrame]) -> dict:
    """Compute pairwise Spearman correlations of signals and returns."""
    signals = {}
    returns = {}
    for sym, df in predictions.items():
        if df is None or len(df) == 0:
            continue
        ret = compute_trade_returns(df)
        ret.name = f"ret_{sym}"
        sig = df.set_index("timestamp")["y_pred"]
        sig.name = f"signal_{sym}"
        returns[sym] = ret
        signals[sym] = sig

    if len(signals) < 2:
        return {"status": "insufficient_symbols"}

    # Align by timestamp
    sig_df = pd.DataFrame(signals)
    ret_df = pd.DataFrame(returns)

    for col in sig_df.columns:
        sig_df[col] = sig_df[col].astype(float)
    for col in ret_df.columns:
        ret_df[col] = ret_df[col].astype(float)

    sig_corr = sig_df.corr(method="spearman").round(4)
    ret_corr = ret_df.corr(method="spearman").round(4)

    return {
        "signal_correlation": sig_corr.to_dict(),
        "return_correlation": ret_corr.to_dict(),
        "mean_signal_corr": float(sig_corr.values[np.triu_indices_from(sig_corr.values, k=1)].mean()) if len(sig_corr) > 1 else 0.0,
        "mean_return_corr": float(ret_corr.values[np.triu_indices_from(ret_corr.values, k=1)].mean()) if len(ret_corr) > 1 else 0.0,
    }


# ── help: heartbeat ────────────────────────────────────────────────


def heartbeat_if_due(last: float, step_name: str, start_time: float, interval: float = 30.0) -> float:
    now = time.time()
    if now - last >= interval:
        elapsed = now - start_time
        logger.info(f"[Heartbeat] step={step_name} elapsed={elapsed:.0f}s")
        return now
    return last
