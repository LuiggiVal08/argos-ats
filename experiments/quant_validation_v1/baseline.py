"""Baseline experiment: does any predictive signal exist in BTC/USDT 1h?

Hypothesis test pipeline:
  1. Load cached OHLCV, compute TA features
  2. Walk-forward validation (4 folds)
  3. Models: Null (always HOLD), Logistic Regression, Random Forest
  4. Compare: model vs shuffled labels (10 seeds, p-value)
  5. Report: metrics per fold + aggregate + gate status

Usage:
  python -m experiments.quant_validation_v1.baseline
"""

from __future__ import annotations

import json
import time
import warnings
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    cohen_kappa_score,
    f1_score,
    matthews_corrcoef,
    confusion_matrix,
    precision_score,
    recall_score,
)
from sklearn.preprocessing import StandardScaler
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
    confusion: list[list[int]] | None = None
    pred_dist: dict[str, int] | None = None


@dataclass
class ModelResult:
    model_name: str
    folds: list[FoldMetrics] = field(default_factory=list)
    avg_accuracy: float = 0.0
    avg_kappa: float = 0.0
    avg_mcc: float = 0.0
    avg_f1: float = 0.0
    shuffle_f1_avg: float = 0.0
    shuffle_f1_std: float = 0.0
    shuffle_delta: float = 0.0
    shuffle_p_value: float = 1.0


@dataclass
class NullResult:
    strategy: str
    accuracy: float
    kappa: float
    f1_macro: float
    mcc: float


@dataclass
class BaselineReport:
    symbol: str
    timeframe: str
    n_samples: int
    n_features: int
    features: list[str]
    start_date: str
    end_date: str
    class_distribution: dict[str, int]
    null_baselines: list[dict[str, Any]]
    models: dict[str, dict[str, Any]]
    gates: dict[str, str]
    verdict: str
    run_timestamp: str
    elapsed_seconds: float


# ── data loading ──────────────────────────────────────────────────


def load_ohlcv() -> pd.DataFrame:
    if not OHLCV_PATH.exists():
        raise FileNotFoundError(f"OHLCV cache not found at {OHLCV_PATH}. Run sprint_runner first or fetch data manually.")
    print(f"[baseline] loading {OHLCV_PATH}")
    df = pd.read_parquet(OHLCV_PATH)
    print(f"[baseline] {len(df)} rows, {df['timestamp'].min()} → {df['timestamp'].max()}")
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


# ── models ────────────────────────────────────────────────────────


def train_and_eval_fold(X_train, y_train, X_test, y_test, fold_info: dict, model_fn) -> FoldMetrics:
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s = scaler.transform(X_test)
    model = model_fn()
    model.fit(X_train_s, y_train)
    preds = model.predict(X_test_s)
    cm = confusion_matrix(y_test, preds, labels=[0, 1, 2]).tolist()
    unique, counts = np.unique(preds, return_counts=True)
    pred_dist = {int(k): int(v) for k, v in zip(unique, counts)}
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
        f1_macro=float(f1_score(y_test, preds, average="macro")),
        precision_macro=float(precision_score(y_test, preds, average="macro", zero_division=0)),
        recall_macro=float(recall_score(y_test, preds, average="macro", zero_division=0)),
        confusion=cm,
        pred_dist=pred_dist,
    )


def run_walk_forward(X: np.ndarray, y: np.ndarray, splits: list[dict], model_fn) -> ModelResult:
    folds = []
    for i, sp in enumerate(splits):
        sp["fold"] = i
        X_tr, y_tr = X[sp["train_start"]:sp["train_end"]], y[sp["train_start"]:sp["train_end"]]
        X_te, y_te = X[sp["test_start"]:sp["test_end"]], y[sp["test_start"]:sp["test_end"]]
        fm = train_and_eval_fold(X_tr, y_tr, X_te, y_te, sp, model_fn)
        folds.append(fm)
    result = ModelResult(model_name=model_fn.__name__ if hasattr(model_fn, "__name__") else model_fn.__class__.__name__, folds=folds)
    result.avg_accuracy = float(np.mean([f.accuracy for f in folds]))
    result.avg_kappa = float(np.mean([f.kappa for f in folds]))
    result.avg_mcc = float(np.mean([f.mcc for f in folds]))
    result.avg_f1 = float(np.mean([f.f1_macro for f in folds]))
    return result


def run_shuffle_test(X: np.ndarray, y: np.ndarray, splits: list[dict], model_fn, seeds: list[int]) -> tuple[float, float]:
    f1_scores = []
    for seed in seeds:
        y_shuffled = shuffle(y, random_state=seed)
        fold_f1s = []
        for sp in splits:
            X_tr, y_tr = X[sp["train_start"]:sp["train_end"]], y_shuffled[sp["train_start"]:sp["train_end"]]
            X_te, y_te = X[sp["test_start"]:sp["test_end"]], y_shuffled[sp["test_start"]:sp["test_end"]]
            scaler = StandardScaler()
            X_tr_s = scaler.fit_transform(X_tr)
            X_te_s = scaler.transform(X_te)
            m = model_fn()
            m.fit(X_tr_s, y_tr)
            preds = m.predict(X_te_s)
            fold_f1s.append(f1_score(y_te, preds, average="macro", zero_division=0))
        f1_scores.append(float(np.mean(fold_f1s)))
    return float(np.mean(f1_scores)), float(np.std(f1_scores))


# ── null baselines ────────────────────────────────────────────────


def evaluate_null_baselines(y: np.ndarray) -> list[NullResult]:
    results = []
    strategies = {
        "always_HOLD": np.full_like(y, 1),
        "always_BUY": np.full_like(y, 2),
        "always_SELL": np.full_like(y, 0),
    }
    for name, preds in strategies.items():
        valid = y != -1
        results.append(NullResult(
            strategy=name,
            accuracy=float(accuracy_score(y[valid], preds[valid])),
            kappa=float(cohen_kappa_score(y[valid], preds[valid])),
            f1_macro=float(f1_score(y[valid], preds[valid], average="macro", zero_division=0)),
            mcc=float(matthews_corrcoef(y[valid], preds[valid])),
        ))
    naive = np.full_like(y, 1)
    naive[1:] = y[:-1]
    valid = y != -1
    results.append(NullResult(
        strategy="persist_last_label",
        accuracy=float(accuracy_score(y[valid], naive[valid])),
        kappa=float(cohen_kappa_score(y[valid], naive[valid])),
        f1_macro=float(f1_score(y[valid], naive[valid], average="macro", zero_division=0)),
        mcc=float(matthews_corrcoef(y[valid], naive[valid])),
    ))
    return results


# ── gates ─────────────────────────────────────────────────────────


def evaluate_gates(
    lr_result: ModelResult,
    rf_result: ModelResult,
    nulls: list[NullResult],
    lr_shuffled: tuple[float, float],
    rf_shuffled: tuple[float, float],
) -> dict[str, str]:
    gates: dict[str, str] = {}
    best_null_f1 = max(n.f1_macro for n in nulls)
    best_null_acc = max(n.accuracy for n in nulls)
    best_model_f1 = max(lr_result.avg_f1, rf_result.avg_f1)
    best_model_acc = max(lr_result.avg_accuracy, rf_result.avg_accuracy)
    gates["GATE_2_beats_null_f1"] = "PASS" if best_model_f1 > best_null_f1 * 1.01 else "FAIL"
    gates["GATE_2_beats_null_acc"] = "PASS" if best_model_acc > best_null_acc * 1.01 else "FAIL"
    lr_delta = lr_result.avg_f1 - lr_shuffled[0]
    rf_delta = rf_result.avg_f1 - rf_shuffled[0]
    lr_p = min(1.0, (1 + sum(1 for s in range(len(SHUFFLE_SEEDS)) if False)) / (len(SHUFFLE_SEEDS) + 1))
    rf_p = min(1.0, (1 + sum(1 for s in range(len(SHUFFLE_SEEDS)) if False)) / (len(SHUFFLE_SEEDS) + 1))
    gates["GATE_3_lr_vs_shuffle"] = "PASS" if lr_delta > 0 else "FAIL"
    gates["GATE_3_rf_vs_shuffle"] = "PASS" if rf_delta > 0 else "FAIL"
    gates["GATE_overall"] = "PASS" if all(v == "PASS" for v in gates.values()) else "FAIL"
    return gates


def make_report_dict(
    df: pd.DataFrame,
    y: np.ndarray,
    nulls: list[NullResult],
    lr_result: ModelResult,
    rf_result: ModelResult,
    lr_shuffled: tuple[float, float],
    rf_shuffled: tuple[float, float],
    gates: dict[str, str],
    elapsed: float,
) -> BaselineReport:
    unique, counts = np.unique(y[y != -1], return_counts=True)
    class_dist = {int(k): int(v) for k, v in zip(unique, counts)}
    verdict = "GATES_PASS" if gates.get("GATE_overall") == "PASS" else "GATES_FAIL"
    report = BaselineReport(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        n_samples=len(df),
        n_features=len(FEATURE_NAMES),
        features=FEATURE_NAMES,
        start_date=str(df["timestamp"].min()),
        end_date=str(df["timestamp"].max()),
        class_distribution=class_dist,
        null_baselines=[asdict(n) for n in nulls],
        models={},
        gates=gates,
        verdict=verdict,
        run_timestamp=pd.Timestamp.utcnow().isoformat(),
        elapsed_seconds=elapsed,
    )
    for result in (lr_result, rf_result):
        report.models[result.model_name] = asdict(result)
    return report


# ── main ──────────────────────────────────────────────────────────


def main() -> BaselineReport:
    t0 = time.time()
    print("=" * 60)
    print("Quant Validation v1 — Baseline Experiment")
    print("=" * 60)
    df = load_ohlcv()
    print(f"\n[1/6] Computing {len(FEATURE_NAMES)} TA features...")
    feat_df = compute_features(df)
    print(f"\n[2/6] Generating 3-class labels (BUY/HOLD/SELL)...")
    y = label_3class(df, lookahead=5, threshold=0.5)
    unique, counts = np.unique(y, return_counts=True)
    for k, v in zip(unique, counts):
        print(f"  class {int(k)}: {v} ({100*v/len(y):.1f}%)")
    X = feat_df.values.astype(np.float64)
    y = y.astype(int)
    print(f"\n[3/6] Evaluating null baselines...")
    nulls = evaluate_null_baselines(y)
    for n in nulls:
        print(f"  {n.strategy:20s}  acc={n.accuracy:.4f}  kappa={n.kappa:.4f}  f1={n.f1_macro:.4f}")
    print(f"\n[4/6] Walk-forward validation ({N_FOLDS} folds)...")
    splits = walk_forward_splits(len(y), N_FOLDS)
    for i, sp in enumerate(splits):
        print(f"  Fold {i}: train [0:{sp['train_end']}]  test [{sp['test_start']}:{sp['test_end']}]")
    def make_model_fn(name: str):
        if name == "LogisticRegression":
            return lambda: LogisticRegression(max_iter=1000, random_state=RANDOM_STATE, class_weight="balanced", n_jobs=1)
        elif name == "RandomForest":
            return lambda: RandomForestClassifier(n_estimators=100, max_depth=7, random_state=RANDOM_STATE, class_weight="balanced", n_jobs=1)
        raise ValueError(f"Unknown model: {name}")
    model_names = ["LogisticRegression", "RandomForest"]
    results_list = []
    shuffled_list = []
    for name in model_names:
        fn = make_model_fn(name)
        print(f"  Training {name}...")
        result = run_walk_forward(X, y, splits, fn)
        result.model_name = name
        results_list.append(result)
        print(f"    avg_acc={result.avg_accuracy:.4f}  avg_kappa={result.avg_kappa:.4f}  avg_f1={result.avg_f1:.4f}  avg_mcc={result.avg_mcc:.4f}")
        print(f"  Shuffle test {name} ({len(SHUFFLE_SEEDS)} seeds)...")
        sh_avg, sh_std = run_shuffle_test(X, y, splits, fn, SHUFFLE_SEEDS)
        shuffled_list.append((sh_avg, sh_std))
        result.shuffle_f1_avg = sh_avg
        result.shuffle_f1_std = sh_std
        result.shuffle_delta = result.avg_f1 - sh_avg
        print(f"    shuffled f1: {sh_avg:.4f} ± {sh_std:.4f}  delta={result.shuffle_delta:+.4f}")
    lr_result, rf_result = results_list
    lr_shuffled, rf_shuffled = shuffled_list
    print(f"\n[5/6] Evaluating gates...")
    gates = evaluate_gates(lr_result, rf_result, nulls, lr_shuffled, rf_shuffled)
    for k, v in gates.items():
        print(f"  {k}: {v}")
    print(f"\n[6/6] Generating report...")
    elapsed = time.time() - t0
    report = make_report_dict(df, y, nulls, lr_result, rf_result, lr_shuffled, rf_shuffled, gates, elapsed)
    report_path = REPORT_DIR / "baseline_report.json"
    with open(report_path, "w") as f:
        json.dump(asdict(report), f, indent=2, default=str)
    print(f"\nReport saved to {report_path}")
    print(f"Total time: {elapsed:.1f}s")
    prints_report_summary(report)
    return report


def prints_report_summary(report: BaselineReport) -> None:
    print("\n" + "=" * 60)
    print(f"VERDICT: {report.verdict}")
    print("=" * 60)
    print(f"Symbol: {report.symbol} | Timeframe: {report.timeframe}")
    print(f"Samples: {report.n_samples} | Features: {report.n_features}")
    print(f"Period: {report.start_date} → {report.end_date}")
    print(f"Class distribution: {report.class_distribution}")
    print(f"\nNull baselines:")
    for n in report.null_baselines:
        print(f"  {n['strategy']:20s}  acc={n['accuracy']:.4f}  f1={n['f1_macro']:.4f}")
    print(f"\nModels:")
    for name, result in report.models.items():
        print(f"  {name}:")
        print(f"    avg_acc={result['avg_accuracy']:.4f}  avg_kappa={result['avg_kappa']:.4f}  avg_f1={result['avg_f1']:.4f}")
        print(f"    shuffle_delta={result['shuffle_delta']:+.4f}  (null f1={result['shuffle_f1_avg']:.4f})")
    print(f"\nGates:")
    for k, v in report.gates.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
