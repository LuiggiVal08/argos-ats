"""Signal Validation Sprint — Phase 2 del roadmap cuantitativo V2.

H0: No existe señal predictiva explotable en OHLCV+TA en timeframe 1h.
H1: Existe señal estadística débil pero consistente.

Ejecuta 4 modelos × 3 framings con walk-forward (6 splits) + shuffle-label test.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import (
    accuracy_score,
    cohen_kappa_score,
    f1_score,
    matthews_corrcoef,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    recall_score,
    r2_score,
)
from sklearn.preprocessing import StandardScaler

# ── paths ────────────────────────────────────────────────────────
OHLCV_CACHE = Path(__file__).parent.parent / "data" / "btc_usdt_1h.parquet"
REPORT_DIR = Path(__file__).parent.parent / "reports" / "sprint"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

SYMBOL = "BTC/USDT"
TIMEFRAME = "1h"
YEARS = 4
N_WALK_WINDOWS = 4
SHUFFLE_SEEDS = list(range(42, 47))
BASELINE_HOLD_SYMBOL = "BH"
FEATURE_NAMES: tuple[str, ...] = (
    "open", "high", "low", "close", "volume",
    "rsi", "ema_fast", "ema_medium", "ema_slow",
    "macd", "macd_signal", "macd_hist",
    "bb_upper", "bb_middle", "bb_lower",
    "atr", "adx", "obv", "volume_sma", "pct_change",
)

# ── data types ────────────────────────────────────────────────────


@dataclass
class WalkFoldMetrics:
    fold: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    accuracy: float | None = None
    kappa: float | None = None
    mcc: float | None = None
    f1: float | None = None
    precision: float | None = None
    recall: float | None = None
    mse: float | None = None
    mae: float | None = None
    r2: float | None = None
    dir_acc: float | None = None
    n_buy_pred: int | None = None
    n_sell_pred: int | None = None
    n_hold_pred: int | None = None


@dataclass
class ModelResult:
    model_name: str
    framing: str
    walk_folds: list[WalkFoldMetrics] = field(default_factory=list)
    avg_accuracy: float | None = None
    avg_kappa: float | None = None
    avg_mcc: float | None = None
    avg_f1: float | None = None
    avg_precision: float | None = None
    avg_recall: float | None = None
    avg_mse: float | None = None
    avg_r2: float | None = None
    avg_dir_acc: float | None = None
    avg_n_buy: float | None = None
    avg_n_sell: float | None = None
    avg_n_hold: float | None = None
    shuffle_f1_avg: float | None = None
    shuffle_f1_std: float | None = None
    shuffle_delta: float | None = None
    baseline_vs_hold: float | None = None


@dataclass
class FramingResult:
    framing: str
    models: dict[str, ModelResult] = field(default_factory=dict)
    best_model: str | None = None
    gate_0a_passed: bool | None = None  # señal > azar consistente
    gate_0b_sharpe_equiv: float | None = None  # proxy: dir_acc ajustada


@dataclass
class SprintReport:
    symbol: str
    timeframe: str
    n_samples: int
    n_features: int
    start_date: str
    end_date: str
    framings: dict[str, FramingResult] = field(default_factory=dict)
    verdict: str = ""  # CASO_A | CASO_B | CASO_C
    verdict_reason: str = ""
    run_timestamp: str = ""
    elapsed_seconds: float = 0.0


# ── OHLCV loading ────────────────────────────────────────────────


async def fetch_ohlcv() -> pd.DataFrame:
    if OHLCV_CACHE.exists():
        print(f"[sprint] loading cached OHLCV from {OHLCV_CACHE}")
        return pd.read_parquet(OHLCV_CACHE)

    print(f"[sprint] fetching {SYMBOL} {TIMEFRAME} from Binance...")
    import ccxt.async_support as ccxt

    exchange = ccxt.binance({
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    })
    since = exchange.parse8601(f"{2026 - YEARS}-01-01T00:00:00Z")
    all_ohlcv: list[list] = []
    chunk = 1000
    while True:
        raw = await exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=chunk, since=since)
        if not raw or len(raw) < 2:
            break
        all_ohlcv.extend(raw)
        since = raw[-1][0] + 1
        await asyncio.sleep(0.25)
    await exchange.close()

    df = pd.DataFrame(all_ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    print(f"[sprint] fetched {len(df)} candles")
    df.to_parquet(OHLCV_CACHE)
    return df


# ── feature engineering ──────────────────────────────────────────


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    import ta as ta_lib

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)

    raw_features: dict[str, pd.Series] = {
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
    raw_features["macd"] = macd.macd()
    raw_features["macd_signal"] = macd.macd_signal()
    raw_features["macd_hist"] = macd.macd_diff()

    bb = ta_lib.volatility.BollingerBands(close, window=20, window_dev=2)
    raw_features["bb_upper"] = bb.bollinger_hband()
    raw_features["bb_middle"] = bb.bollinger_mavg()
    raw_features["bb_lower"] = bb.bollinger_lband()

    raw_features["atr"] = ta_lib.volatility.AverageTrueRange(high, low, close, window=14).average_true_range()
    raw_features["adx"] = ta_lib.trend.ADXIndicator(high, low, close, window=14).adx()
    raw_features["obv"] = ta_lib.volume.OnBalanceVolumeIndicator(close, volume).on_balance_volume()
    raw_features["volume_sma"] = volume.rolling(20).mean()
    raw_features["pct_change"] = close.pct_change() * 100.0

    result = pd.DataFrame(raw_features, columns=FEATURE_NAMES)
    result = result.bfill().ffill()
    return result


def compute_volatility_adj_returns(df: pd.DataFrame, lookahead: int = 5) -> pd.Series:
    future_close = df["close"].shift(-lookahead)
    raw_return = (future_close / df["close"] - 1.0) * 100.0
    vol = raw_return.rolling(60).std()
    adj = raw_return / vol.clip(lower=1e-10)
    return adj


# ── label generators ──────────────────────────────────────────────


def label_classification(df: pd.DataFrame, lookahead: int = 5) -> np.ndarray:
    adj_returns = compute_volatility_adj_returns(df, lookahead)
    labels = np.full(len(adj_returns), 1, dtype=int)  # default HOLD
    labels[adj_returns > 0.5] = 2   # BUY
    labels[adj_returns < -0.5] = 0  # SELL
    return labels


def label_binary(df: pd.DataFrame, lookahead: int = 5, threshold: float = 0.002) -> np.ndarray:
    adj_returns = compute_volatility_adj_returns(df, lookahead)
    labels = np.full(len(adj_returns), -1, dtype=int)
    labels[adj_returns > threshold] = 1   # BUY
    labels[adj_returns < -threshold] = 0  # SELL
    return labels


def label_regression(df: pd.DataFrame, lookahead: int = 5) -> np.ndarray:
    adj_returns = compute_volatility_adj_returns(df, lookahead)
    return adj_returns.values.astype(np.float64)


# ── walk-forward splits ──────────────────────────────────────────


def walk_forward_splits(n: int, n_windows: int = N_WALK_WINDOWS) -> list[dict]:
    """Expanding window: each fold trains on all prior data and tests on the next chunk."""
    window_size = n // (n_windows + 1)
    splits: list[dict] = []
    for i in range(n_windows):
        test_end = (i + 2) * window_size
        if i == n_windows - 1:
            test_end = n
        splits.append({
            "fold": i,
            "train_end": (i + 1) * window_size,
            "test_start": (i + 1) * window_size,
            "test_end": test_end,
        })
    return splits


# ── model definitions ────────────────────────────────────────────


def _make_models() -> dict[str, BaseEstimator]:
    return {
        "LogisticRegression": LogisticRegression(
            max_iter=2000, C=1.0, solver="lbfgs", class_weight="balanced", random_state=42
        ),
        "RandomForest": RandomForestClassifier(
            max_depth=7, n_estimators=100, class_weight="balanced", random_state=42, n_jobs=-1
        ),
        "HistGradientBoosting": HistGradientBoostingClassifier(
            max_depth=3, learning_rate=0.1, random_state=42, early_stopping=False
        ),
    }


def _make_regression_models() -> dict[str, BaseEstimator]:
    return {
        "Ridge(lags=5)": Ridge(alpha=1.0, random_state=42),
    }


# ── evaluation helpers ────────────────────────────────────────────


def _clf_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, Any]:
    labels = sorted(set(y_true) | set(y_pred))
    m: dict[str, Any] = {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "kappa": float(cohen_kappa_score(y_true, y_pred)),
        "f1": float(f1_score(y_true, y_pred, average="weighted", labels=labels)),
        "mcc": float(matthews_corrcoef(y_true, y_pred)),
    }
    try:
        m["precision"] = float(precision_score(y_true, y_pred, average="weighted", labels=labels, zero_division=0))
        m["recall"] = float(recall_score(y_true, y_pred, average="weighted", labels=labels, zero_division=0))
    except Exception:
        m["precision"] = 0.0
        m["recall"] = 0.0
    class_counts = pd.Series(y_pred).value_counts()
    m["n_buy_pred"] = int(class_counts.get(2, class_counts.get(1, 0)))
    m["n_sell_pred"] = int(class_counts.get(0, 0))
    m["n_hold_pred"] = int(class_counts.get(1, 0))
    return m


def _reg_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, Any]:
    m: dict[str, Any] = {
        "mse": float(mean_squared_error(y_true, y_pred)),
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "r2": float(r2_score(y_true, y_pred)),
    }
    dir_acc = np.mean((np.sign(y_true) == np.sign(y_pred)) | ((y_true == 0) & (y_pred == 0)))
    m["dir_acc"] = float(dir_acc)
    return m


# ── single fold runner ────────────────────────────────────────────


def _run_fold_clf(
    model: BaseEstimator, X_train: np.ndarray, y_train: np.ndarray,
    X_test: np.ndarray, y_test: np.ndarray, fold: int,
    scaler: StandardScaler,
) -> WalkFoldMetrics:
    X_train_s = scaler.fit_transform(X_train)
    X_test_s = scaler.transform(X_test)
    model.fit(X_train_s, y_train)
    y_pred = model.predict(X_test_s)
    m = _clf_metrics(y_test, y_pred)
    return WalkFoldMetrics(
        fold=fold,
        train_start=0, train_end=len(X_train),
        test_start=len(X_train), test_end=len(X_train) + len(X_test),
        **m,
    )


def _run_fold_reg(
    model: BaseEstimator, X_train: np.ndarray, y_train: np.ndarray,
    X_test: np.ndarray, y_test: np.ndarray, fold: int,
    scaler: StandardScaler,
) -> WalkFoldMetrics:
    X_train_s = scaler.fit_transform(X_train)
    X_test_s = scaler.transform(X_test)
    if hasattr(model, "fit"):
        model.fit(X_train_s, y_train)
    y_pred = model.predict(X_test_s)
    m = _reg_metrics(y_test, y_pred)
    return WalkFoldMetrics(
        fold=fold,
        train_start=0, train_end=len(X_train),
        test_start=len(X_train), test_end=len(X_train) + len(X_test),
        **m,
    )


# ── shuffle-label test ────────────────────────────────────────────


def _shuffle_test_clf(
    model: BaseEstimator, X: np.ndarray, y: np.ndarray,
    n_seeds: int = 10,
) -> tuple[float, float]:
    scaler = StandardScaler()
    X_s = scaler.fit_transform(X)
    f1_scores: list[float] = []
    model_cls = type(model)
    for seed in range(n_seeds):
        rng = np.random.RandomState(seed)
        y_shuff = y.copy()
        rng.shuffle(y_shuff)
        model_c = model_cls(**model.get_params())
        model_c.fit(X_s, y_shuff)
        y_pred = model_c.predict(X_s)
        labels = sorted(set(y_shuff) | set(y_pred))
        f1_scores.append(f1_score(y_shuff, y_pred, average="weighted", labels=labels))
    return float(np.mean(f1_scores)), float(np.std(f1_scores))


def _shuffle_test_reg(
    model: BaseEstimator, X: np.ndarray, y: np.ndarray,
    n_seeds: int = 10,
) -> tuple[float, float]:
    scaler = StandardScaler()
    X_s = scaler.fit_transform(X)
    dir_accs: list[float] = []
    model_cls = type(model)
    for seed in range(n_seeds):
        rng = np.random.RandomState(seed)
        y_shuff = y.copy()
        rng.shuffle(y_shuff)
        model_c = model_cls(**model.get_params())
        model_c.fit(X_s, y_shuff)
        y_pred = model_c.predict(X_s)
        dir_acc = np.mean((np.sign(y_shuff) == np.sign(y_pred)) | ((y_shuff == 0) & (y_pred == 0)))
        dir_accs.append(float(dir_acc))
    return float(np.mean(dir_accs)), float(np.std(dir_accs))


# ── framing runners ──────────────────────────────────────────────


def _run_classification_framing(
    X: np.ndarray, df: pd.DataFrame,
) -> FramingResult:
    result = FramingResult(framing="A_classification")
    y = label_classification(df)
    valid = ~np.isnan(y)
    X_v, y_v = X[valid], y[valid]
    splits = walk_forward_splits(len(X_v))

    models = _make_models()
    for name, model in models.items():
        mr = ModelResult(model_name=name, framing="A_classification")
        for sp in splits:
            train_slc = slice(0, sp["train_end"])
            test_slc = slice(sp["test_start"], sp["test_end"])
            fold = _run_fold_clf(model, X_v[train_slc], y_v[train_slc], X_v[test_slc], y_v[test_slc], sp["fold"], StandardScaler())
            mr.walk_folds.append(fold)
        mr.avg_accuracy = float(np.mean([f.accuracy for f in mr.walk_folds if f.accuracy is not None]))
        mr.avg_kappa = float(np.mean([f.kappa for f in mr.walk_folds if f.kappa is not None]))
        mr.avg_f1 = float(np.mean([f.f1 for f in mr.walk_folds if f.f1 is not None]))
        mr.avg_mcc = float(np.mean([f.mcc for f in mr.walk_folds if f.mcc is not None]))
        mr.avg_precision = float(np.mean([f.precision for f in mr.walk_folds if f.precision is not None]))
        mr.avg_recall = float(np.mean([f.recall for f in mr.walk_folds if f.recall is not None]))
        mr.avg_n_buy = float(np.mean([f.n_buy_pred for f in mr.walk_folds if f.n_buy_pred is not None]))
        mr.avg_n_sell = float(np.mean([f.n_sell_pred for f in mr.walk_folds if f.n_sell_pred is not None]))
        mr.avg_n_hold = float(np.mean([f.n_hold_pred for f in mr.walk_folds if f.n_hold_pred is not None]))

        shuf_avg, shuf_std = _shuffle_test_clf(model, X_v, y_v, n_seeds=len(SHUFFLE_SEEDS))
        mr.shuffle_f1_avg = shuf_avg
        mr.shuffle_f1_std = shuf_std
        mr.shuffle_delta = mr.avg_f1 - shuf_avg if mr.avg_f1 is not None else None

        hold_pred = np.full_like(y_v, 1)
        hold_f1 = f1_score(y_v, hold_pred, average="weighted", labels=sorted(set(y_v)))
        mr.baseline_vs_hold = mr.avg_f1 - hold_f1 if mr.avg_f1 is not None else None

        result.models[name] = mr

    best = max(result.models.values(), key=lambda m: m.shuffle_delta if m.shuffle_delta is not None else -999)
    result.best_model = best.model_name
    result.gate_0a_passed = any(m.shuffle_delta is not None and m.shuffle_delta > 0.05 for m in result.models.values())
    return result


def _run_binary_framing(X: np.ndarray, df: pd.DataFrame) -> FramingResult:
    result = FramingResult(framing="B_binary")
    y = label_binary(df)
    valid = y != -1
    X_v, y_v = X[valid], y[valid]
    if len(X_v) < 1000:
        print(f"[sprint] B_binary: only {len(X_v)} samples after threshold, skipping")
        return result
    splits = walk_forward_splits(len(X_v))

    models = _make_models()
    for name, model in models.items():
        mr = ModelResult(model_name=name, framing="B_binary")
        for sp in splits:
            train_slc = slice(0, sp["train_end"])
            test_slc = slice(sp["test_start"], sp["test_end"])
            fold = _run_fold_clf(model, X_v[train_slc], y_v[train_slc], X_v[test_slc], y_v[test_slc], sp["fold"], StandardScaler())
            mr.walk_folds.append(fold)
        mr.avg_accuracy = float(np.mean([f.accuracy for f in mr.walk_folds if f.accuracy is not None]))
        mr.avg_kappa = float(np.mean([f.kappa for f in mr.walk_folds if f.kappa is not None]))
        mr.avg_f1 = float(np.mean([f.f1 for f in mr.walk_folds if f.f1 is not None]))

        shuf_avg, shuf_std = _shuffle_test_clf(model, X_v, y_v, n_seeds=len(SHUFFLE_SEEDS))
        mr.shuffle_f1_avg = shuf_avg
        mr.shuffle_f1_std = shuf_std
        mr.shuffle_delta = mr.avg_f1 - shuf_avg if mr.avg_f1 is not None else None

        # baseline for binary: predict majority class
        majority = np.bincount(y_v.astype(int)).argmax()
        hold_pred = np.full_like(y_v, majority)
        hold_f1 = f1_score(y_v, hold_pred, average="weighted", labels=sorted(set(y_v)))
        mr.baseline_vs_hold = mr.avg_f1 - hold_f1 if mr.avg_f1 is not None else None

        result.models[name] = mr

    best = max(result.models.values(), key=lambda m: m.shuffle_delta if m.shuffle_delta is not None else -999)
    result.best_model = best.model_name
    result.gate_0a_passed = any(m.shuffle_delta is not None and m.shuffle_delta > 0.05 for m in result.models.values())
    return result


def _run_regression_framing(X: np.ndarray, df: pd.DataFrame) -> FramingResult:
    result = FramingResult(framing="C_regression")
    y = label_regression(df)
    valid = ~np.isnan(y) & np.isfinite(y)
    X_v, y_v = X[valid], y[valid]
    splits = walk_forward_splits(len(X_v))

    models = _make_regression_models()
    for name, model in models.items():
        mr = ModelResult(model_name=name, framing="C_regression")

        # Build lags for Ridge
        n_lags = 5
        X_lagged = np.column_stack([X_v] + [np.roll(X_v, i + 1, axis=0) for i in range(n_lags)])
        # Trim first n_lags rows (they have NaN/rolled values we don't trust)
        X_lagged = X_lagged[n_lags:]
        y_lagged = y_v[n_lags:]

        scaler = StandardScaler()

        for sp in splits:
            train_end = sp["train_end"] - n_lags
            test_start = sp["test_start"] - n_lags
            test_end = sp["test_end"] - n_lags
            if test_start <= 0 or train_end <= 0:
                continue
            fold = _run_fold_reg(
                model, X_lagged[:train_end], y_lagged[:sp["train_end"] - n_lags],
                X_lagged[test_start:test_end], y_lagged[test_start:test_end],
                sp["fold"], scaler,
            )
            mr.walk_folds.append(fold)
        mr.avg_mse = float(np.mean([f.mse for f in mr.walk_folds if f.mse is not None]))
        mr.avg_r2 = float(np.mean([f.r2 for f in mr.walk_folds if f.r2 is not None]))
        mr.avg_dir_acc = float(np.mean([f.dir_acc for f in mr.walk_folds if f.dir_acc is not None]))

        shuf_avg, shuf_std = _shuffle_test_reg(model, X_v, y_v, n_seeds=len(SHUFFLE_SEEDS))
        mr.shuffle_f1_avg = shuf_avg
        mr.shuffle_f1_std = shuf_std
        mr.shuffle_delta = (mr.avg_dir_acc or 0.0) - shuf_avg

        # baseline: predict mean return
        mean_return = np.mean(y_v[n_lags:])
        hold_pred = np.full_like(y_v[n_lags:], mean_return)
        hold_dir = np.mean((np.sign(y_v[n_lags:]) == np.sign(hold_pred)) | ((y_v[n_lags:] == 0) & (hold_pred == 0)))
        mr.baseline_vs_hold = (mr.avg_dir_acc or 0.0) - hold_dir

        result.models[name] = mr

    if result.models:
        best = max(result.models.values(), key=lambda m: m.shuffle_delta if m.shuffle_delta is not None else -999)
        result.best_model = best.model_name
        result.gate_0a_passed = any(
            m.shuffle_delta is not None and m.shuffle_delta > 0.03 for m in result.models.values()
        )
    return result


# ── verdict ──────────────────────────────────────────────────────


def _compute_verdict(framings: dict[str, FramingResult], n_samples: int) -> tuple[str, str]:
    passed = [f for f in framings.values() if f.gate_0a_passed]
    if not passed:
        return ("CASO_A", "Ningún framing supera GATE 0A. No hay señal distinguible de ruido.")

    max_delta = 0.0
    best_framing = ""
    for f_name, f_res in framings.items():
        for m_name, m_res in f_res.models.items():
            if m_res.shuffle_delta is not None and m_res.shuffle_delta > max_delta:
                max_delta = m_res.shuffle_delta
                best_framing = f"{f_name}/{m_name}"

    if max_delta < 0.03:
        return ("CASO_A", f"Señal marginal (delta={max_delta:.4f}), no consistente. No explotable.")
    elif max_delta < 0.10:
        return ("CASO_B", f"Señal débil detectada (delta={max_delta:.4f}, best={best_framing}). Consistente pero no explotable económicamente sin mejora sustancial.")
    else:
        return ("CASO_C", f"Señal real confirmada (delta={max_delta:.4f}, best={best_framing}). Alpha potencial explotable.")


# ── main ──────────────────────────────────────────────────────────


async def main() -> None:
    t0 = time.time()

    df = await fetch_ohlcv()
    print(f"[sprint] {len(df)} candles from {df['timestamp'].min()} to {df['timestamp'].max()}")
    print(f"[sprint] computing {len(FEATURE_NAMES)} features...")
    features_df = compute_features(df)
    X = features_df.values.astype(np.float64)
    print(f"[sprint] feature matrix: {X.shape}")

    print("[sprint] running framing A (classification: BUY/HOLD/SELL)...")
    fr_a = _run_classification_framing(X, df)
    for m in fr_a.models.values():
        print(f"  {m.model_name}: acc={m.avg_accuracy:.4f} kappa={m.avg_kappa:.4f} f1={m.avg_f1:.4f} shuf_delta={m.shuffle_delta:.4f} vsBH={m.baseline_vs_hold:.4f}")

    print("[sprint] running framing B (binary: BUY vs SELL)...")
    fr_b = _run_binary_framing(X, df)
    for m in fr_b.models.values():
        print(f"  {m.model_name}: acc={m.avg_accuracy:.4f} kappa={m.avg_kappa:.4f} f1={m.avg_f1:.4f} shuf_delta={m.shuffle_delta:.4f} vsBH={m.baseline_vs_hold:.4f}")

    print("[sprint] running framing C (regression: future return)...")
    fr_c = _run_regression_framing(X, df)
    for m in fr_c.models.values():
        print(f"  {m.model_name}: r2={m.avg_r2:.4f} dir_acc={m.avg_dir_acc:.4f} shuf_delta={m.shuffle_delta:.4f} vsBH={m.baseline_vs_hold:.4f}")

    elapsed = time.time() - t0
    verdict, reason = _compute_verdict(
        {"A_classification": fr_a, "B_binary": fr_b, "C_regression": fr_c},
        len(X),
    )

    report = SprintReport(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        n_samples=len(X),
        n_features=X.shape[1],
        start_date=str(df["timestamp"].min()),
        end_date=str(df["timestamp"].max()),
        framings={
            "A_classification": fr_a,
            "B_binary": fr_b,
            "C_regression": fr_c,
        },
        verdict=verdict,
        verdict_reason=reason,
        run_timestamp=pd.Timestamp.now(tz="UTC").isoformat(),
        elapsed_seconds=elapsed,
    )

    report_path = REPORT_DIR / "sprint_report.json"
    with open(report_path, "w") as f:
        json.dump(asdict(report), f, indent=2, default=str)
    print(f"\n[sprint] report written to {report_path}")
    print(f"[sprint] verdict: {verdict}")
    print(f"[sprint] reason: {reason}")
    print(f"[sprint] elapsed: {elapsed:.1f}s")
    return report


if __name__ == "__main__":
    report = asyncio.run(main())
    sys.exit(0 if report.verdict == "CASO_A" else 1)
