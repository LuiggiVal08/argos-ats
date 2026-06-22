"""Shared utilities for quant validation v1 — data loading, features, walk-forward."""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    cohen_kappa_score,
    f1_score,
    matthews_corrcoef,
    confusion_matrix,
    precision_score,
    recall_score,
    roc_auc_score,
    r2_score,
    mean_absolute_error,
)
from sklearn.preprocessing import StandardScaler, LabelBinarizer
from sklearn.utils import shuffle

warnings.filterwarnings("ignore", category=UserWarning, module="ta")
warnings.filterwarnings("ignore", category=FutureWarning)

OHLCV_PATH = Path(__file__).parent.parent.parent / "apps" / "analytics-engine" / "data" / "btc_usdt_1h.parquet"
REPORT_DIR = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v1"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

SYMBOL = "BTC/USDT"
TIMEFRAME = "1h"
FEATURE_NAMES = [
    "open", "high", "low", "close", "volume",
    "rsi", "ema_fast", "ema_medium", "ema_slow",
    "macd", "macd_signal", "macd_hist",
    "bb_upper", "bb_middle", "bb_lower",
    "atr", "adx", "obv", "volume_sma", "pct_change",
]
N_FOLDS = 4
SHUFFLE_SEEDS = list(range(42, 52))
RANDOM_STATE = 42


# ── dataclasses ────────────────────────────────────────────────────


@dataclass
class FoldMetrics:
    fold: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    n_train: int
    n_test: int
    accuracy: float = 0.0
    kappa: float = 0.0
    mcc: float = 0.0
    f1_macro: float = 0.0
    precision_macro: float = 0.0
    recall_macro: float = 0.0
    auc: float | None = None
    confusion: list[list[int]] | None = None
    pred_dist: dict[str, int] | None = None
    r2: float | None = None
    mae: float | None = None
    dir_acc: float | None = None
    ic: float | None = None


@dataclass
class ModelResult:
    model_name: str
    folds: list[FoldMetrics] = field(default_factory=list)
    avg_accuracy: float = 0.0
    avg_kappa: float = 0.0
    avg_mcc: float = 0.0
    avg_f1: float = 0.0
    avg_auc: float | None = None
    avg_r2: float | None = None
    avg_mae: float | None = None
    avg_dir_acc: float | None = None
    avg_ic: float | None = None
    shuffle_f1_avg: float = 0.0
    shuffle_f1_std: float = 0.0
    shuffle_delta: float = 0.0
    shuffle_r2_avg: float = 0.0
    shuffle_r2_std: float = 0.0
    shuffle_r2_delta: float = 0.0


@dataclass
class NullResult:
    strategy: str
    accuracy: float = 0.0
    kappa: float = 0.0
    f1_macro: float = 0.0
    mcc: float = 0.0
    r2: float = 0.0
    mae: float = 0.0
    dir_acc: float = 0.0
    ic: float = 0.0


# ── data loading ──────────────────────────────────────────────────


def load_ohlcv() -> pd.DataFrame:
    if not OHLCV_PATH.exists():
        raise FileNotFoundError(f"OHLCV cache not found at {OHLCV_PATH}.")
    print(f"[data] loading {OHLCV_PATH}")
    df = pd.read_parquet(OHLCV_PATH)
    print(f"[data] {len(df)} rows, {df['timestamp'].min()} → {df['timestamp'].max()}")
    return df


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    import ta as ta_lib
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)
    raw: dict[str, pd.Series] = {
        "open": df["open"].astype(float),
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "rsi": ta_lib.momentum.RSIIndicator(close, window=14).rsi(),
        "ema_fast": ta_lib.trend.EMAIndicator(close, window=9).ema_indicator(),
        "ema_medium": ta_lib.trend.EMAIndicator(close, window=21).ema_indicator(),
        "ema_slow": ta_lib.trend.EMAIndicator(close, window=50).ema_indicator(),
    }
    macd = ta_lib.trend.MACD(close)
    raw["macd"] = macd.macd()
    raw["macd_signal"] = macd.macd_signal()
    raw["macd_hist"] = macd.macd_diff()
    bb = ta_lib.volatility.BollingerBands(close, window=20, window_dev=2)
    raw["bb_upper"] = bb.bollinger_hband()
    raw["bb_middle"] = bb.bollinger_mavg()
    raw["bb_lower"] = bb.bollinger_lband()
    raw["atr"] = ta_lib.volatility.AverageTrueRange(high, low, close, window=14).average_true_range()
    raw["adx"] = ta_lib.trend.ADXIndicator(high, low, close, window=14).adx()
    raw["obv"] = ta_lib.volume.OnBalanceVolumeIndicator(close, volume).on_balance_volume()
    raw["volume_sma"] = volume.rolling(20).mean()
    raw["pct_change"] = close.pct_change() * 100.0
    result = pd.DataFrame(raw, columns=FEATURE_NAMES)
    result = result.bfill().ffill()
    return result


# ── labeling ──────────────────────────────────────────────────────


def compute_vol_adj_returns(df: pd.DataFrame, lookahead: int = 5) -> pd.Series:
    future_close = df["close"].shift(-lookahead)
    raw_return = (future_close / df["close"] - 1.0) * 100.0
    vol = raw_return.rolling(60).std()
    return raw_return / vol.clip(lower=1e-10)


def label_3class(df: pd.DataFrame, lookahead: int = 5, threshold: float = 0.5) -> np.ndarray:
    """0=SELL, 1=HOLD, 2=BUY"""
    adj = compute_vol_adj_returns(df, lookahead)
    labels = np.full(len(adj), 1, dtype=int)
    labels[adj > threshold] = 2
    labels[adj < -threshold] = 0
    return labels


def label_binary(df: pd.DataFrame, lookahead: int = 5, threshold: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
    """0=SELL, 1=BUY (HOLD filtered out). Returns (binary_labels, valid_mask)."""
    adj = compute_vol_adj_returns(df, lookahead)
    labels = np.full(len(adj), -1, dtype=int)
    labels[adj > threshold] = 1
    labels[adj < -threshold] = 0
    valid = labels != -1
    return labels, valid


def label_regression(df: pd.DataFrame, lookahead: int = 5) -> np.ndarray:
    """Continuous vol-adjusted returns."""
    return compute_vol_adj_returns(df, lookahead).values.astype(np.float64)


# ── walk-forward ──────────────────────────────────────────────────


def walk_forward_splits(n: int, n_folds: int = N_FOLDS) -> list[dict]:
    window = n // (n_folds + 1)
    splits = []
    for i in range(n_folds):
        te = (i + 2) * window
        if i == n_folds - 1:
            te = n
        splits.append({
            "train_start": 0,
            "train_end": (i + 1) * window,
            "test_start": (i + 1) * window,
            "test_end": te,
        })
    return splits


# ── classification trainer ────────────────────────────────────────


def train_eval_classification_fold(
    X_train, y_train, X_test, y_test, fold_info: dict, model_fn,
    labels: list[int] | None = None,
) -> FoldMetrics:
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s = scaler.transform(X_test)
    model = model_fn()
    model.fit(X_train_s, y_train)
    preds = model.predict(X_test_s)
    labels_for_cm = labels or sorted(np.unique(y_test))
    cm = confusion_matrix(y_test, preds, labels=labels_for_cm).tolist()
    unique, counts = np.unique(preds, return_counts=True)
    pred_dist = {int(k): int(v) for k, v in zip(unique, counts)}

    auc_val = None
    if len(labels_for_cm) == 2 and hasattr(model, "predict_proba"):
        try:
            proba = model.predict_proba(X_test_s)
            if proba.shape[1] == 2:
                pos_idx = list(model.classes_).index(max(model.classes_))
                auc_val = float(roc_auc_score(y_test, proba[:, pos_idx]))
        except Exception:
            pass

    return FoldMetrics(
        fold=fold_info.get("fold", 0),
        train_start=fold_info["train_start"],
        train_end=fold_info["train_end"],
        test_start=fold_info["test_start"],
        test_end=fold_info["test_end"],
        n_train=len(y_train),
        n_test=len(y_test),
        accuracy=float(accuracy_score(y_test, preds)),
        kappa=float(cohen_kappa_score(y_test, preds)),
        mcc=float(matthews_corrcoef(y_test, preds)),
        f1_macro=float(f1_score(y_test, preds, average="macro", zero_division=0)),
        precision_macro=float(precision_score(y_test, preds, average="macro", zero_division=0)),
        recall_macro=float(recall_score(y_test, preds, average="macro", zero_division=0)),
        auc=auc_val,
        confusion=cm,
        pred_dist=pred_dist,
    )


def run_walk_forward_classification(
    X: np.ndarray, y: np.ndarray, splits: list[dict], model_fn,
    labels: list[int] | None = None,
) -> ModelResult:
    folds = []
    for i, sp in enumerate(splits):
        sp["fold"] = i
        X_tr, y_tr = X[sp["train_start"]:sp["train_end"]], y[sp["train_start"]:sp["train_end"]]
        X_te, y_te = X[sp["test_start"]:sp["test_end"]], y[sp["test_start"]:sp["test_end"]]
        fm = train_eval_classification_fold(X_tr, y_tr, X_te, y_te, sp, model_fn, labels)
        folds.append(fm)
    result = ModelResult(model_name="", folds=folds)
    result.avg_accuracy = float(np.mean([f.accuracy for f in folds]))
    result.avg_kappa = float(np.mean([f.kappa for f in folds]))
    result.avg_mcc = float(np.mean([f.mcc for f in folds]))
    result.avg_f1 = float(np.mean([f.f1_macro for f in folds]))
    aucs = [f.auc for f in folds if f.auc is not None]
    result.avg_auc = float(np.mean(aucs)) if aucs else None
    return result


def run_shuffle_test_classification(
    X: np.ndarray, y: np.ndarray, splits: list[dict], model_fn, seeds: list[int],
    labels: list[int] | None = None,
) -> tuple[float, float]:
    f1_scores = []
    for seed in seeds:
        y_shuffled = shuffle(y, random_state=seed)
        fold_f1s = []
        for sp in splits:
            X_tr = X[sp["train_start"]:sp["train_end"]]
            y_tr = y_shuffled[sp["train_start"]:sp["train_end"]]
            X_te = X[sp["test_start"]:sp["test_end"]]
            y_te = y_shuffled[sp["test_start"]:sp["test_end"]]
            scaler = StandardScaler()
            X_tr_s = scaler.fit_transform(X_tr)
            X_te_s = scaler.transform(X_te)
            m = model_fn()
            m.fit(X_tr_s, y_tr)
            preds = m.predict(X_te_s)
            fold_f1s.append(f1_score(y_te, preds, average="macro", zero_division=0))
        f1_scores.append(float(np.mean(fold_f1s)))
    return float(np.mean(f1_scores)), float(np.std(f1_scores))


# ── regression trainer ────────────────────────────────────────────


def train_eval_regression_fold(
    X_train, y_train, X_test, y_test, fold_info: dict, model_fn,
) -> FoldMetrics:
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s = scaler.transform(X_test)
    model = model_fn()
    model.fit(X_train_s, y_train)
    preds = model.predict(X_test_s)

    dir_acc = float(np.mean((np.sign(preds) == np.sign(y_test)).astype(float)))
    ic = float(np.corrcoef(preds, y_test)[0, 1]) if len(preds) > 1 else 0.0

    return FoldMetrics(
        fold=fold_info.get("fold", 0),
        train_start=fold_info["train_start"],
        train_end=fold_info["train_end"],
        test_start=fold_info["test_start"],
        test_end=fold_info["test_end"],
        n_train=len(y_train),
        n_test=len(y_test),
        r2=float(r2_score(y_test, preds)),
        mae=float(mean_absolute_error(y_test, preds)),
        dir_acc=dir_acc,
        ic=ic,
    )


def run_walk_forward_regression(
    X: np.ndarray, y: np.ndarray, splits: list[dict], model_fn,
) -> ModelResult:
    folds = []
    for i, sp in enumerate(splits):
        sp["fold"] = i
        X_tr, y_tr = X[sp["train_start"]:sp["train_end"]], y[sp["train_start"]:sp["train_end"]]
        X_te, y_te = X[sp["test_start"]:sp["test_end"]], y[sp["test_start"]:sp["test_end"]]
        fm = train_eval_regression_fold(X_tr, y_tr, X_te, y_te, sp, model_fn)
        folds.append(fm)
    result = ModelResult(model_name="", folds=folds)
    result.avg_r2 = float(np.mean([f.r2 for f in folds]))
    result.avg_mae = float(np.mean([f.mae for f in folds]))
    result.avg_dir_acc = float(np.mean([f.dir_acc for f in folds]))
    result.avg_ic = float(np.mean([f.ic for f in folds]))
    return result


def run_shuffle_test_regression(
    X: np.ndarray, y: np.ndarray, splits: list[dict], model_fn, seeds: list[int],
) -> tuple[float, float]:
    r2_scores = []
    for seed in seeds:
        y_shuffled = shuffle(y, random_state=seed)
        fold_r2s = []
        for sp in splits:
            X_tr = X[sp["train_start"]:sp["train_end"]]
            y_tr = y_shuffled[sp["train_start"]:sp["train_end"]]
            X_te = X[sp["test_start"]:sp["test_end"]]
            y_te = y_shuffled[sp["test_start"]:sp["test_end"]]
            scaler = StandardScaler()
            X_tr_s = scaler.fit_transform(X_tr)
            X_te_s = scaler.transform(X_te)
            m = model_fn()
            m.fit(X_tr_s, y_tr)
            preds = m.predict(X_te_s)
            fold_r2s.append(r2_score(y_te, preds))
        r2_scores.append(float(np.mean(fold_r2s)))
    return float(np.mean(r2_scores)), float(np.std(r2_scores))


# ── null baselines (classification) ───────────────────────────────


def null_classification(y: np.ndarray, labels: list[int], default_label: int, strategy_name: str) -> NullResult:
    preds = np.full_like(y, default_label)
    valid = y != -1
    return NullResult(
        strategy=strategy_name,
        accuracy=float(accuracy_score(y[valid], preds[valid])),
        kappa=float(cohen_kappa_score(y[valid], preds[valid])),
        f1_macro=float(f1_score(y[valid], preds[valid], average="macro", zero_division=0)),
        mcc=float(matthews_corrcoef(y[valid], preds[valid])),
    )


def null_persist_label(y: np.ndarray, strategy_name: str = "persist_last_label") -> NullResult:
    preds = np.full_like(y, y[0] if len(y) > 0 else 0)
    preds[1:] = y[:-1]
    valid = y != -1
    return NullResult(
        strategy=strategy_name,
        accuracy=float(accuracy_score(y[valid], preds[valid])),
        kappa=float(cohen_kappa_score(y[valid], preds[valid])),
        f1_macro=float(f1_score(y[valid], preds[valid], average="macro", zero_division=0)),
        mcc=float(matthews_corrcoef(y[valid], preds[valid])),
    )


# ── null baselines (regression) ───────────────────────────────────


def null_regression(y: np.ndarray, strategy: str) -> NullResult:
    valid = ~np.isnan(y)
    yv = y[valid]
    if strategy == "predict_zero":
        preds = np.zeros_like(yv)
    elif strategy == "predict_mean":
        preds = np.full_like(yv, np.mean(yv))
    elif strategy == "persist_last_value":
        preds = np.full_like(yv, yv[0])
        preds[1:] = yv[:-1]
    else:
        raise ValueError(f"Unknown null strategy: {strategy}")
    dir_acc = float(np.mean((np.sign(preds) == np.sign(yv)).astype(float)))
    ic = float(np.corrcoef(preds, yv)[0, 1]) if len(preds) > 1 else 0.0
    return NullResult(
        strategy=strategy,
        r2=float(r2_score(yv, preds)),
        mae=float(mean_absolute_error(yv, preds)),
        dir_acc=dir_acc,
        ic=ic if not np.isnan(ic) else 0.0,
    )


# ── persistence helpers ────────────────────────────────────────────


def save_report(data: dict, filename: str) -> Path:
    path = REPORT_DIR / filename
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"[report] saved to {path}")
    return path


def elapsed_str(t0: float) -> str:
    return f"{time.time() - t0:.1f}s"


import time
