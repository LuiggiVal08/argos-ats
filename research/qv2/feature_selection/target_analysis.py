#!/usr/bin/env python3
"""Target Analysis for ARGOS ATS — PHASE 1.

Comprehensive quantitative evaluation of all candidate prediction targets
for BTCUSDT Binance Futures perpetual systematic trading.

Usage:
    python scripts/target_analysis.py [--force-fetch]

Output:
    scripts/target_analysis_output/  (plots + tables)
    stdout: markdown report sections
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import warnings
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
os.environ["PYTHONWARNINGS"] = "ignore"

# ---------------------------------------------------------------------------
# 1. Data fetching
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).resolve().parent / "target_analysis_output"
DATA_DIR.mkdir(parents=True, exist_ok=True)
PLOTS_DIR = DATA_DIR / "plots"
PLOTS_DIR.mkdir(exist_ok=True)
TABLES_DIR = DATA_DIR / "tables"
TABLES_DIR.mkdir(exist_ok=True)

# Configuration
TIMEFRAMES = {"1h": "1h", "2h": "2h", "4h": "4h"}
HORIZONS = [1, 2, 3, 4, 5, 6]
THRESHOLDS = [0.25, 0.5, 0.75, 1.0]
LABEL_TYPES = ["raw", "voladj"]
VOL_WINDOW = 60  # candles for historical vol
START_DATE = "2020-01-01"
END_DATE = "2026-06-26"
SYMBOL = "BTC/USDT"
CACHE_FILE = DATA_DIR / "ohlcv_data.pkl"

# Regime definitions
REGIME_BREAKS: dict[str, tuple[str, str, str]] = {
    "pre-covid": ("2020-01-01", "2020-03-01", "bull"),
    "covid-crash": ("2020-03-01", "2020-04-01", "crisis"),
    "post-covid-bull": ("2020-04-01", "2021-04-01", "bull"),
    "2021-summer": ("2021-04-01", "2021-07-01", "sideways"),
    "2021-bull-run": ("2021-07-01", "2021-11-01", "bull"),
    "2022-crypto-winter": ("2022-01-01", "2022-11-01", "bear"),
    "ftx-crash": ("2022-11-01", "2022-12-01", "crisis"),
    "2023-recovery": ("2023-01-01", "2024-01-01", "bull"),
    "2024-etf-launch": ("2024-01-01", "2024-04-01", "bull"),
    "2024-summer": ("2024-04-01", "2024-10-01", "sideways"),
    "2024-q4-rally": ("2024-10-01", "2025-01-01", "bull"),
    "2025-consolidation": ("2025-01-01", "2025-06-01", "sideways"),
    "2025-h2": ("2025-06-01", END_DATE, "sideways"),
}


def fetch_binance_ohlcv(
    symbol: str = SYMBOL,
    timeframe: str = "1h",
    start_str: str = START_DATE,
    end_str: str = END_DATE,
) -> pd.DataFrame:
    """Fetch OHLCV data from Binance via CCXT."""
    import ccxt

    exchange = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})
    since = exchange.parse8601(start_str + "T00:00:00Z")
    end_ts = exchange.parse8601(end_str + "T00:00:00Z")
    all_candles: list[list[Any]] = []

    while since < end_ts:
        try:
            candles = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
            if not candles:
                break
            all_candles.extend(candles)
            since = candles[-1][0] + 1
            time.sleep(exchange.rateLimit / 1000)
        except Exception as e:
            print(f"  fetch error at {since}: {e}", file=sys.stderr)
            time.sleep(2)
            continue

    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    df = df[df["timestamp"] <= pd.Timestamp(end_str)]
    return df


def load_data(force_fetch: bool = False) -> dict[str, pd.DataFrame]:
    """Load or fetch OHLCV data for all timeframes.

    We fetch 1h and resample to 2h and 4h for consistency.
    """
    cache = CACHE_FILE
    if cache.exists() and not force_fetch:
        print(f"Loading cached data from {cache}")
        import pickle

        with open(cache, "rb") as f:
            return pickle.load(f)

    print("Fetching 1h OHLCV from Binance...")
    df_1h = fetch_binance_ohlcv(timeframe="1h")
    print(f"  Got {len(df_1h):,} candles from {df_1h['timestamp'].min()} to {df_1h['timestamp'].max()}")

    # Resample to 2h and 4h
    df_1h_idx = df_1h.set_index("timestamp")
    ohlcv_dict = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}

    df_2h = df_1h_idx.resample("2h").agg(ohlcv_dict).dropna().reset_index()
    df_4h = df_1h_idx.resample("4h").agg(ohlcv_dict).dropna().reset_index()

    data = {"1h": df_1h, "2h": df_2h, "4h": df_4h}

    import pickle

    with open(cache, "wb") as f:
        pickle.dump(data, f)
    print(f"Cached to {cache}")

    return data


# ---------------------------------------------------------------------------
# 2. Label computation
# ---------------------------------------------------------------------------


def compute_raw_return(close: pd.Series, horizon: int) -> pd.Series:
    """Raw forward return."""
    future = close.shift(-horizon)
    return future / close - 1.0


def compute_voladj_return(
    close: pd.Series, horizon: int, vol_window: int = VOL_WINDOW
) -> pd.Series:
    """Volatility-adjusted forward return (σ-units) with NO lookahead."""
    past_returns = close.pct_change()
    vol = past_returns.rolling(vol_window, min_periods=VOL_WINDOW // 2).std() * np.sqrt(horizon)
    vol = vol.clip(lower=1e-10)
    raw_ret = compute_raw_return(close, horizon)
    return raw_ret / vol


def label_ternary(
    values: pd.Series, threshold: float
) -> np.ndarray:
    """Ternary label: SELL(0) / HOLD(1) / BUY(2)."""
    labels = np.full(len(values), 1, dtype=int)
    labels[values > threshold] = 2
    labels[values < -threshold] = 0
    return labels


def label_binary_direction(values: pd.Series, threshold: float) -> np.ndarray:
    """Binary directional: SELL(0) / BUY(1), ignoring HOLD region."""
    labels = np.full(len(values), -1, dtype=int)  # -1 = no label
    labels[values > threshold] = 1
    labels[values < -threshold] = 0
    return labels


def label_binary_trade(values: pd.Series, threshold: float) -> np.ndarray:
    """Binary trade/no-trade: NO_TRADE(0) / TRADE(1)."""
    labels = np.zeros(len(values), dtype=int)
    labels[np.abs(values) > threshold] = 1
    return labels


# ---------------------------------------------------------------------------
# 3. Statistical metrics
# ---------------------------------------------------------------------------


def class_balance(labels: np.ndarray, n_classes: int = 3) -> dict[str, float]:
    """Return percentage of each class."""
    counts = np.bincount(labels[labels >= 0], minlength=n_classes)[:n_classes]
    total = counts.sum()
    if total == 0:
        return {str(i): 0.0 for i in range(n_classes)}
    result = {str(i): float(c) / total for i, c in enumerate(counts)}
    # Include -1 (unlabeled) for binary directional
    unlabeled = int((labels == -1).sum())
    if unlabeled > 0:
        result["-1"] = float(unlabeled) / len(labels)
    return result


def signal_density(labels: np.ndarray) -> float:
    """Fraction of non-HOLD / non-NO_TRADE labels."""
    return float(np.sum(labels >= 0)) / len(labels)


def signal_persistence(labels: np.ndarray, max_lag: int = 5) -> dict[int, float]:
    """Autocorrelation of label series at various lags."""
    clean = labels[labels >= 0].astype(float)
    if len(clean) < max_lag + 10:
        return {lag: 0.0 for lag in range(1, max_lag + 1)}
    result: dict[int, float] = {}
    for lag in range(1, max_lag + 1):
        result[lag] = float(np.corrcoef(clean[:-lag], clean[lag:])[0, 1]) if len(clean) > lag else 0.0
    return result


def compute_snr(values: pd.Series) -> float:
    """Signal-to-noise ratio: |mean| / std."""
    clean = values.dropna().values
    if len(clean) < 10 or np.std(clean) == 0:
        return 0.0
    return float(np.abs(np.mean(clean)) / np.std(clean))


def compute_entropy(labels: np.ndarray, n_classes: int = 3) -> float:
    """Shannon entropy of label distribution (in bits)."""
    counts = np.bincount(labels[labels >= 0], minlength=n_classes)[:n_classes]
    probs = counts / counts.sum()
    probs = probs[probs > 0]
    if len(probs) == 0:
        return 0.0
    return float(-np.sum(probs * np.log2(probs)))


def class_balance_ternary(close: pd.Series, horizon: int, threshold: float, voladj: bool = True) -> dict:
    """Compute class balance for a single (horizon, threshold, voladj) config."""
    ret = compute_voladj_return(close, horizon) if voladj else compute_raw_return(close, horizon)
    labels = label_ternary(ret, threshold)
    balance = class_balance(labels, 3)
    balance["entropy"] = compute_entropy(labels, 3)
    balance["signal_density"] = float(np.sum(labels != 1)) / len(labels)
    # Persistence
    pers = signal_persistence(labels)
    balance["persistence_lag1"] = pers.get(1, 0.0)
    # SNR of the return series
    balance["snr"] = compute_snr(ret)
    balance["n_buys"] = int(np.sum(labels == 2))
    balance["n_sells"] = int(np.sum(labels == 0))
    balance["n_holds"] = int(np.sum(labels == 1))
    balance["total"] = len(labels)
    balance["horizon"] = horizon
    balance["threshold"] = threshold
    return balance


# ---------------------------------------------------------------------------
# 4. Regime segmentation
# ---------------------------------------------------------------------------


def detect_regime(close: pd.Series, timestamp: pd.Series) -> pd.Series:
    """Detect market regime using SMA slope + volatility."""
    # Use 50-period SMA on hourly data
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()

    # Rolling volatility (20-period)
    returns = close.pct_change()
    vol20 = returns.rolling(20).std() * np.sqrt(24 * 365)  # annualized vol
    vol_threshold = vol20.quantile(0.90)

    regime = pd.Series(index=close.index, dtype=object)

    # Order matters: priority crisis > bull/bear > sideways

    # Bull: price above both SMAs
    bull_mask = (close > sma50) & (sma50 > sma200)
    regime[bull_mask] = "bull"

    # Bear: price below both SMAs
    bear_mask = (close < sma50) & (sma50 < sma200)
    regime[bear_mask] = "bear"

    # Sideways: everything else (excluding crisis candidates)
    regime[regime.isna()] = "sideways"

    # Crisis: extreme vol (overrides bull/bear/sideways)
    crisis_mask = vol20 > vol_threshold
    regime[crisis_mask] = "crisis"

    return regime


def volatility_bucket(vol: pd.Series, n_buckets: int = 5) -> pd.Series:
    """Quantile-based volatility buckets (1=lowest vol, 5=highest vol)."""
    return pd.qcut(vol, n_buckets, labels=False, duplicates="drop") + 1


# ---------------------------------------------------------------------------
# 5. Main analysis
# ---------------------------------------------------------------------------


def run_analysis(data: dict[str, pd.DataFrame]) -> dict:
    """Run complete target analysis."""
    results: dict = {}

    for tf_name, tf_df in data.items():
        close = tf_df["close"]
        timestamp = tf_df["timestamp"]

        print(f"\n{'='*60}")
        print(f"Analyzing {tf_name} ({len(tf_df):,} candles)")
        print(f"{'='*60}")

        # Regime detection
        regime = detect_regime(close, timestamp)

        # Volatility buckets
        returns = close.pct_change()
        vol = returns.rolling(20).std()
        vol_bucket = volatility_bucket(vol)

        tf_results: dict = {}

        for horizon in HORIZONS:
            for threshold in THRESHOLDS:
                for voladj in [True, False]:
                    key = f"h{horizon}_t{threshold}_{'voladj' if voladj else 'raw'}"

                    # Volatility-normalized labels
                    ret_vadj = compute_voladj_return(close, horizon)
                    labels_vadj = label_ternary(ret_vadj, threshold)

                    # Raw labels
                    ret_raw = compute_raw_return(close, horizon)
                    labels_raw = label_ternary(ret_raw, threshold)

                    labels = labels_vadj if voladj else labels_raw
                    ret = ret_vadj if voladj else ret_raw

                    # Class balance
                    bal = class_balance(labels, 3)
                    bal_named = {
                        "pct_sell": bal.get("0", 0),
                        "pct_hold": bal.get("1", 0),
                        "pct_buy": bal.get("2", 0),
                    }

                    # Entropy
                    ent = compute_entropy(labels, 3)

                    # Signal density
                    density = float(np.sum(labels != 1)) / len(labels)

                    # Persistence
                    pers = signal_persistence(labels)
                    pers_l1 = pers.get(1, 0.0)

                    # SNR
                    snr = compute_snr(ret)

                    # Mutual Information approximation
                    # Use adjusted mutual info between label and forward return
                    mi = 0.0
                    if len(labels) > 100:
                        from sklearn.metrics import adjusted_mutual_info_score
                        clean_mask = labels >= 0
                        valid_mask = clean_mask & ~np.isnan(ret.values)
                        if valid_mask.sum() > 100:
                            ret_binned = pd.qcut(
                                ret.values[valid_mask], 5, labels=False, duplicates="drop"
                            )
                            mi = adjusted_mutual_info_score(
                                labels[valid_mask], ret_binned,
                            )

                    # Stability across regimes
                    regime_stability: dict[str, dict] = {}
                    for reg_name in ["bull", "bear", "sideways", "crisis"]:
                        mask = regime == reg_name
                        if mask.sum() < 50:
                            continue
                        reg_labels = labels[mask.values]
                        reg_bal = class_balance(reg_labels, 3)
                        regime_stability[reg_name] = {
                            "pct_sell": reg_bal.get("0", 0),
                            "pct_hold": reg_bal.get("1", 0),
                            "pct_buy": reg_bal.get("2", 0),
                            "pct_trade": float(np.sum(reg_labels != 1)) / len(reg_labels),
                        }

                    # Stability across vol buckets
                    vol_stability: dict[str, dict] = {}
                    for b in range(1, 6):
                        mask = vol_bucket == b
                        if mask.sum() < 50:
                            continue
                        b_labels = labels[mask.values]
                        b_bal = class_balance(b_labels, 3)
                        vol_stability[str(b)] = {
                            "pct_sell": b_bal.get("0", 0),
                            "pct_hold": b_bal.get("1", 0),
                            "pct_buy": b_bal.get("2", 0),
                            "pct_trade": float(np.sum(b_labels != 1)) / len(b_labels),
                        }

                    # Binary directional labels
                    labels_bin_dir = label_binary_direction(ret, threshold)
                    bin_dir_valid = labels_bin_dir[labels_bin_dir >= 0]
                    bal_bin_dir = class_balance(labels_bin_dir, 2)

                    # Binary trade/no-trade labels
                    labels_bin_trade = label_binary_trade(ret, threshold)
                    bal_bin_trade = class_balance(labels_bin_trade, 2)

                    tf_results[key] = {
                        "timeframe": tf_name,
                        "horizon": horizon,
                        "threshold": threshold,
                        "voladj": voladj,
                        "class_balance": bal_named,
                        "entropy": ent,
                        "signal_density": density,
                        "persistence_lag1": pers_l1,
                        "snr": snr,
                        "mutual_info": mi,
                        "regime_stability": regime_stability,
                        "vol_stability": vol_stability,
                        "binary_directional_balance": bal_bin_dir,
                        "binary_trade_balance": bal_bin_trade,
                    }

        results[tf_name] = tf_results

    return results


# ---------------------------------------------------------------------------
# 6. Reporting
# ---------------------------------------------------------------------------


def summary_table(results: dict) -> pd.DataFrame:
    """Build summary table across all configurations."""
    rows = []
    for tf_name, tf_results in results.items():
        for key, r in tf_results.items():
            rows.append(
                {
                    "timeframe": r["timeframe"],
                    "horizon": r["horizon"],
                    "threshold": r["threshold"],
                    "voladj": "voladj" if r["voladj"] else "raw",
                    "%BUY": round(r["class_balance"]["pct_buy"] * 100, 1),
                    "%HOLD": round(r["class_balance"]["pct_hold"] * 100, 1),
                    "%SELL": round(r["class_balance"]["pct_sell"] * 100, 1),
                    "%TRADE": round(r["signal_density"] * 100, 1),
                    "entropy": round(r["entropy"], 3),
                    "persist_lag1": round(r["persistence_lag1"], 3),
                    "SNR": round(r["snr"], 4),
                    "MI": round(r["mutual_info"], 4),
                }
            )
    df = pd.DataFrame(rows)
    return df


def regime_stability_table(results: dict) -> pd.DataFrame:
    """Build regime stability summary."""
    rows = []
    for tf_name, tf_results in results.items():
        for key, r in tf_results.items():
            for regime_name, stats in r.get("regime_stability", {}).items():
                rows.append(
                    {
                        "timeframe": r["timeframe"],
                        "config": f'h{r["horizon"]}_t{r["threshold"]}_{"voladj" if r["voladj"] else "raw"}',
                        "regime": regime_name,
                        "%TRADE": round(stats.get("pct_trade", 0) * 100, 1),
                        "%BUY": round(stats.get("pct_buy", 0) * 100, 1),
                        "%SELL": round(stats.get("pct_sell", 0) * 100, 1),
                    }
                )
    return pd.DataFrame(rows)


def vol_stability_table(results: dict) -> pd.DataFrame:
    """Build volatility bucket stability summary."""
    rows = []
    for tf_name, tf_results in results.items():
        for key, r in tf_results.items():
            for bucket, stats in r.get("vol_stability", {}).items():
                rows.append(
                    {
                        "timeframe": r["timeframe"],
                        "config": f'h{r["horizon"]}_t{r["threshold"]}_{"voladj" if r["voladj"] else "raw"}',
                        "vol_bucket": bucket,
                        "%TRADE": round(stats.get("pct_trade", 0) * 100, 1),
                        "%BUY": round(stats.get("pct_buy", 0) * 100, 1),
                        "%SELL": round(stats.get("pct_sell", 0) * 100, 1),
                    }
                )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 7. Plotting
# ---------------------------------------------------------------------------


def plot_class_balance_heatmap(summary: pd.DataFrame, tf_name: str, voladj: bool = True):
    """Heatmap of class balance across horizons and thresholds."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return

    sub = summary[(summary["timeframe"] == tf_name) & (summary["voladj"] == ("voladj" if voladj else "raw"))]
    if sub.empty:
        return

    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    titles = ["%BUY", "%HOLD", "%SELL"]
    classes = ["%BUY", "%HOLD", "%SELL"]

    for ax, title, cls in zip(axes, titles, classes):
        pivot = sub.pivot_table(values=cls, index="horizon", columns="threshold", aggfunc="first")
        im = ax.imshow(pivot.values, cmap="RdYlGn", aspect="auto", vmin=0, vmax=100)
        ax.set_xticks(range(len(pivot.columns)))
        ax.set_xticklabels([f"θ={c}" for c in pivot.columns])
        ax.set_yticks(range(len(pivot.index)))
        ax.set_yticklabels([f"h={h}" for h in pivot.index])
        ax.set_title(title)
        for i in range(len(pivot.index)):
            for j in range(len(pivot.columns)):
                val = pivot.values[i, j]
                ax.text(j, i, f"{val:.0f}%", ha="center", va="center", fontsize=9, color="black" if 20 < val < 80 else "white")
        plt.colorbar(im, ax=ax)

    fig.suptitle(f"{tf_name} — {'VolAdj' if voladj else 'Raw'} Class Balance", fontsize=14)
    plt.tight_layout()
    fname = PLOTS_DIR / f"class_balance_{tf_name}_{'voladj' if voladj else 'raw'}.png"
    plt.savefig(fname, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved {fname}")


def plot_signal_density(summary: pd.DataFrame):
    """Signal density across configurations."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return

    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    for ax, tf_name in zip(axes, TIMEFRAMES):
        sub = summary[summary["timeframe"] == tf_name]
        for voladj_label, marker, ls in [("voladj", "o", "-"), ("raw", "s", "--")]:
            s = sub[sub["voladj"] == voladj_label]
            for thresh, color in zip(THRESHOLDS, ["blue", "green", "orange", "red"]):
                t = s[s["threshold"] == thresh]
                if not t.empty:
                    t = t.sort_values("horizon")
                    ax.plot(
                        t["horizon"], t["%TRADE"],
                        marker=marker, color=color, linestyle=ls,
                        label=f"θ={thresh} ({voladj_label})",
                    )
        ax.set_xlabel("Horizon (candles)")
        ax.set_ylabel("% Trade signals")
        ax.set_title(f"{tf_name}")
        ax.legend(fontsize=7)
        ax.grid(alpha=0.3)

    fig.suptitle("Signal Density vs Horizon", fontsize=14)
    plt.tight_layout()
    fname = PLOTS_DIR / "signal_density.png"
    plt.savefig(fname, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved {fname}")


def plot_entropy_comparison(summary: pd.DataFrame):
    """Entropy across configurations."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return

    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    for ax, tf_name in zip(axes, TIMEFRAMES):
        sub = summary[summary["timeframe"] == tf_name]
        for voladj_label, marker, ls in [("voladj", "o", "-"), ("raw", "s", "--")]:
            s = sub[sub["voladj"] == voladj_label]
            for thresh, color in zip(THRESHOLDS, ["blue", "green", "orange", "red"]):
                t = s[s["threshold"] == thresh]
                if not t.empty:
                    t = t.sort_values("horizon")
                    ax.plot(
                        t["horizon"], t["entropy"],
                        marker=marker, color=color, linestyle=ls,
                        label=f"θ={thresh} ({voladj_label})",
                    )
        ax.axhline(y=np.log2(3), color="gray", linestyle=":", label="max entropy (3 classes)")
        ax.set_xlabel("Horizon (candles)")
        ax.set_ylabel("Entropy (bits)")
        ax.set_title(f"{tf_name}")
        ax.legend(fontsize=7)
        ax.grid(alpha=0.3)

    fig.suptitle("Label Entropy vs Horizon", fontsize=14)
    plt.tight_layout()
    fname = PLOTS_DIR / "entropy_comparison.png"
    plt.savefig(fname, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved {fname}")


def plot_persistence(summary: pd.DataFrame):
    """Persistence (autocorrelation lag 1) across configurations."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return

    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    for ax, tf_name in zip(axes, TIMEFRAMES):
        sub = summary[summary["timeframe"] == tf_name]
        for voladj_label, marker in [("voladj", "o"), ("raw", "s")]:
            s = sub[sub["voladj"] == voladj_label]
            for thresh, color in zip(THRESHOLDS, ["blue", "green", "orange", "red"]):
                t = s[s["threshold"] == thresh]
                if not t.empty:
                    t = t.sort_values("horizon")
                    ax.plot(
                        t["horizon"], abs(t["persist_lag1"]),
                        marker=marker, color=color,
                        label=f"θ={thresh} ({voladj_label})",
                    )
        ax.set_xlabel("Horizon (candles)")
        ax.set_ylabel("|Autocorrelation Lag-1|")
        ax.set_title(f"{tf_name}")
        ax.legend(fontsize=7)
        ax.grid(alpha=0.3)

    fig.suptitle("Signal Persistence (lower = more independent)", fontsize=14)
    plt.tight_layout()
    fname = PLOTS_DIR / "persistence.png"
    plt.savefig(fname, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved {fname}")


def plot_snr(summary: pd.DataFrame):
    """SNR across configurations."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return

    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    for ax, tf_name in zip(axes, TIMEFRAMES):
        sub = summary[summary["timeframe"] == tf_name]
        for voladj_label, marker in [("voladj", "o"), ("raw", "s")]:
            s = sub[sub["voladj"] == voladj_label]
            for thresh, color in zip(THRESHOLDS, ["blue", "green", "orange", "red"]):
                t = s[s["threshold"] == thresh]
                if not t.empty:
                    t = t.sort_values("horizon")
                    ax.plot(
                        t["horizon"], t["SNR"],
                        marker=marker, color=color,
                        label=f"θ={thresh} ({voladj_label})",
                    )
        ax.set_xlabel("Horizon (candles)")
        ax.set_ylabel("SNR (|mean|/std)")
        ax.set_title(f"{tf_name}")
        ax.legend(fontsize=7)
        ax.grid(alpha=0.3)

    fig.suptitle("Signal-to-Noise Ratio", fontsize=14)
    plt.tight_layout()
    fname = PLOTS_DIR / "snr.png"
    plt.savefig(fname, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved {fname}")


def plot_mutual_information(summary: pd.DataFrame):
    """Mutual Information across configurations."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return

    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    for ax, tf_name in zip(axes, TIMEFRAMES):
        sub = summary[summary["timeframe"] == tf_name]
        for voladj_label, marker in [("voladj", "o"), ("raw", "s")]:
            s = sub[sub["voladj"] == voladj_label]
            for thresh, color in zip(THRESHOLDS, ["blue", "green", "orange", "red"]):
                t = s[s["threshold"] == thresh]
                if not t.empty:
                    t = t.sort_values("horizon")
                    ax.plot(
                        t["horizon"], t["MI"],
                        marker=marker, color=color,
                        label=f"θ={thresh} ({voladj_label})",
                    )
        ax.set_xlabel("Horizon (candles)")
        ax.set_ylabel("Adj Mutual Information")
        ax.set_title(f"{tf_name}")
        ax.legend(fontsize=7)
        ax.grid(alpha=0.3)

    fig.suptitle("Adjusted Mutual Information", fontsize=14)
    plt.tight_layout()
    fname = PLOTS_DIR / "mutual_information.png"
    plt.savefig(fname, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved {fname}")


def plot_regime_stability(regime_df: pd.DataFrame, tf_name: str = "1h"):
    """Plot %TRADE by regime for a given timeframe."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return

    sub = regime_df[regime_df["timeframe"] == tf_name]
    if sub.empty:
        return

    # Pick voladj configs only
    sub = sub[sub["config"].str.contains("voladj")]
    pivot = sub.pivot_table(values="%TRADE", index="regime", columns="config", aggfunc="first")

    fig, ax = plt.subplots(figsize=(12, 6))
    pivot.plot(kind="bar", ax=ax)
    ax.set_ylabel("% Trade signals")
    ax.set_title(f"{tf_name} — Trade Signal Rate by Regime")
    ax.legend(fontsize=7, loc="best")
    ax.grid(alpha=0.3, axis="y")
    plt.tight_layout()
    fname = PLOTS_DIR / f"regime_stability_{tf_name}.png"
    plt.savefig(fname, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved {fname}")


def plot_vol_stability(vol_df: pd.DataFrame, tf_name: str = "1h"):
    """Plot %TRADE by vol bucket for a given timeframe."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return

    sub = vol_df[vol_df["timeframe"] == tf_name]
    if sub.empty:
        return
    sub = sub[sub["config"].str.contains("voladj")]
    pivot = sub.pivot_table(values="%TRADE", index="vol_bucket", columns="config", aggfunc="first")

    fig, ax = plt.subplots(figsize=(12, 6))
    pivot.plot(kind="bar", ax=ax)
    ax.set_ylabel("% Trade signals")
    ax.set_xlabel("Volatility bucket (1=low → 5=high)")
    ax.set_title(f"{tf_name} — Trade Signal Rate by Volatility Bucket")
    ax.legend(fontsize=7, loc="best")
    ax.grid(alpha=0.3, axis="y")
    plt.tight_layout()
    fname = PLOTS_DIR / f"vol_stability_{tf_name}.png"
    plt.savefig(fname, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved {fname}")


def print_regime_breakdown(data: dict[str, pd.DataFrame]):
    """Print regime breakdown for each timeframe."""
    print("\n\n## Regime Breakdown\n")
    for tf_name, tf_df in data.items():
        close = tf_df["close"]
        timestamp = tf_df["timestamp"]
        regime = detect_regime(close, timestamp)
        print(f"\n### {tf_name}\n")
        print("| Regime | Candles | % of Total |")
        print("|--------|---------|------------|")
        for r in ["bull", "bear", "sideways", "crisis"]:
            count = int((regime == r).sum())
            pct = count / len(regime) * 100
            print(f"| {r} | {count:,} | {pct:.1f}% |")


def print_regime_date_breakdown(data: dict[str, pd.DataFrame]):
    """Print date ranges for each regime segment."""
    print("\n## Regime Date Ranges\n")
    print("| Name | Start | End | Type |")
    print("|------|-------|-----|------|")
    for name, (start, end, rtype) in REGIME_BREAKS.items():
        print(f"| {name} | {start} | {end} | {rtype} |")


# ---------------------------------------------------------------------------
# 8. Main
# ---------------------------------------------------------------------------


def inspect_best_configs(summary: pd.DataFrame, top_n: int = 10):
    """Inspect top configurations by various criteria."""
    voladj = summary[summary["voladj"] == "voladj"]

    print("\n\n## Top Configurations by Criteria\n")

    # By entropy (higher = more balanced)
    top_entropy = voladj.nlargest(top_n, "entropy")
    print("\n### By Entropy (highest = most balanced classes)\n")
    print(top_entropy[["timeframe", "horizon", "threshold", "%BUY", "%HOLD", "%SELL", "entropy"]].to_string(index=False))

    # By signal density (higher = more trades)
    high_density = voladj[voladj["%TRADE"] >= 30].sort_values("%TRADE", ascending=False)
    print(f"\n### Configs with ≥30% Trade Density\n")
    print(high_density[["timeframe", "horizon", "threshold", "%TRADE", "entropy", "SNR"]].to_string(index=False))

    # By SNR (higher = cleaner signal)
    top_snr = voladj.nlargest(top_n, "SNR")
    print("\n### By SNR (highest = cleaner signal)\n")
    print(top_snr[["timeframe", "horizon", "threshold", "SNR", "entropy", "%TRADE"]].to_string(index=False))

    # By persistence (lower = more independent labels)
    low_persist = voladj.nsmallest(top_n, "persist_lag1")
    print("\n### By Persistence (lowest |autocorrelation| = most independent)\n")
    print(low_persist[["timeframe", "horizon", "threshold", "persist_lag1", "entropy", "%TRADE"]].to_string(index=False))

    # By mutual information
    top_mi = voladj.nlargest(top_n, "MI")
    print("\n### By Mutual Information (highest = most signal in labels)\n")
    print(top_mi[["timeframe", "horizon", "threshold", "MI", "entropy", "%TRADE"]].to_string(index=False))


def compute_optimality_score(summary: pd.DataFrame) -> pd.DataFrame:
    """Compute a composite optimality score for voladj configs.

    Score = w1 * norm_entropy + w2 * norm_density + w3 * norm_snr + w4 * (1-persistence) + w5 * norm_mi
    """
    voladj = summary[summary["voladj"] == "voladj"].copy()
    if voladj.empty:
        return voladj

    def normalize(s):
        return (s - s.min()) / (s.max() - s.min() + 1e-10)

    voladj["score_entropy"] = normalize(voladj["entropy"])
    voladj["score_density"] = normalize(voladj["%TRADE"])
    voladj["score_snr"] = normalize(voladj["SNR"])
    voladj["score_persist"] = 1.0 - normalize(voladj["persist_lag1"].abs())
    voladj["score_mi"] = normalize(voladj["MI"])

    # Weights: entropy 25%, density 20%, SNR 20%, persistence 20%, MI 15%
    voladj["composite_score"] = (
        0.25 * voladj["score_entropy"]
        + 0.20 * voladj["score_density"]
        + 0.20 * voladj["score_snr"]
        + 0.20 * voladj["score_persist"]
        + 0.15 * voladj["score_mi"]
    )

    voladj = voladj.sort_values("composite_score", ascending=False)
    return voladj


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force-fetch", action="store_true", help="Force re-fetch data from Binance")
    args = parser.parse_args()

    print("# ARGOS ATS — Phase 1: Target Definition & Statistical Validation\n")
    print(f"Analysis date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")
    print(f"Data range: {START_DATE} to {END_DATE}")
    print(f"Symbol: {SYMBOL}")
    print(f"Timeframes: {', '.join(TIMEFRAMES)}")
    print(f"Horizons: {', '.join(str(h) for h in HORIZONS)} (candles forward)")
    print(f"Thresholds: {', '.join(f'θ={t}' for t in THRESHOLDS)} σ-units")
    print(f"Label types: raw return, volatility-adjusted return")
    print(f"Vol window: {VOL_WINDOW} candles\n")

    # Load data
    data = load_data(force_fetch=args.force_fetch)

    for tf_name, tf_df in data.items():
        print(f"\n{tf_name}: {len(tf_df):,} candles | "
              f"{tf_df['timestamp'].min().date()} → {tf_df['timestamp'].max().date()}")

    # Regime date breakdown
    print_regime_date_breakdown(data)
    print_regime_breakdown(data)

    # Run analysis
    print("\n\n## Statistical Analysis\n")
    results = run_analysis(data)

    # Build summary table
    summary = summary_table(results)

    # Print all tables
    print("\n\n## Summary Table\n")
    print("### All Configurations — Volatility-Adjusted Labels\n")
    voladj_summary = summary[summary["voladj"] == "voladj"].copy()
    print(voladj_summary.to_string(index=False))

    print("\n\n### All Configurations — Raw Return Labels\n")
    raw_summary = summary[summary["voladj"] == "raw"].copy()
    print(raw_summary.to_string(index=False))

    # Regime stability
    regime_df = regime_stability_table(results)
    print("\n\n## Regime Stability\n")
    print("### Trade Signal Rate by Regime and Config\n")
    for tf_name in TIMEFRAMES:
        sub = regime_df[regime_df["timeframe"] == tf_name]
        if not sub.empty:
            print(f"\n{tf_name}:\n")
            print(sub.to_string(index=False))

    # Vol stability
    vol_df = vol_stability_table(results)
    print("\n\n## Volatility Bucket Stability\n")
    print("### Trade Signal Rate by Volatility Bucket\n")
    for tf_name in TIMEFRAMES:
        sub = vol_df[vol_df["timeframe"] == tf_name]
        if not sub.empty:
            print(f"\n{tf_name}:\n")
            print(sub.to_string(index=False))

    # Best configs
    inspect_best_configs(summary)
    optimal = compute_optimality_score(summary)
    print("\n\n## Composite Optimality Score\n")
    print("### Top 10 Configurations\n")
    print(optimal.head(10)[
        ["timeframe", "horizon", "threshold", "%BUY", "%HOLD", "%SELL", "%TRADE", "entropy", "SNR", "composite_score"]
    ].to_string(index=False))

    print(f"\n\n## Best Overall Config\n")
    best = optimal.iloc[0]
    print(f"Timeframe: {best['timeframe']}")
    print(f"Horizon: {best['horizon']} candles")
    print(f"Threshold: θ={best['threshold']}")
    print(f"Class balance: BUY={best['%BUY']}% | HOLD={best['%HOLD']}% | SELL={best['%SELL']}%")
    print(f"Trade density: {best['%TRADE']}%")
    print(f"Entropy: {best['entropy']:.3f} bits")
    print(f"SNR: {best['SNR']:.4f}")
    print(f"Persistence (lag-1): {best['persist_lag1']:.4f}")
    print(f"Mutual Information: {best['MI']:.4f}")
    print(f"Composite score: {best['composite_score']:.4f}")

    # Generate plots
    print("\n\n## Generating Plots\n")
    try:
        for tf_name in TIMEFRAMES:
            plot_class_balance_heatmap(summary, tf_name, voladj=True)
            plot_class_balance_heatmap(summary, tf_name, voladj=False)
        plot_signal_density(summary)
        plot_entropy_comparison(summary)
        plot_persistence(summary)
        plot_snr(summary)
        plot_mutual_information(summary)
        for tf_name in TIMEFRAMES:
            plot_regime_stability(regime_df, tf_name)
            plot_vol_stability(vol_df, tf_name)
        print("\nAll plots saved to", PLOTS_DIR)
    except Exception as e:
        print(f"Plotting error: {e}", file=sys.stderr)

    # Save summary tables
    summary.to_csv(TABLES_DIR / "summary.csv", index=False)
    regime_df.to_csv(TABLES_DIR / "regime_stability.csv", index=False)
    vol_df.to_csv(TABLES_DIR / "vol_stability.csv", index=False)
    optimal.to_csv(TABLES_DIR / "optimal_scores.csv", index=False)
    print(f"\nTables saved to {TABLES_DIR}")

    print("\n\n## Statistical Conclusions\n")

    # Print regime-by-regime analysis for the best config
    best_tf = best["timeframe"]
    best_h = best["horizon"]
    best_t = best["threshold"]
    best_key = f"h{best_h}_t{best_t}_voladj"

    if best_tf in results and best_key in results[best_tf]:
        best_r = results[best_tf][best_key]
        print(f"### Regime Stability: {best_tf} h={best_h} θ={best_t}\n")
        print("| Regime | %BUY | %HOLD | %SELL | %TRADE |")
        print("|--------|------|-------|-------|--------|")
        for reg_name in ["bull", "bear", "sideways", "crisis"]:
            if reg_name in best_r.get("regime_stability", {}):
                s = best_r["regime_stability"][reg_name]
                print(f"| {reg_name} | {s['pct_buy']*100:.1f}% | {s['pct_hold']*100:.1f}% | {s['pct_sell']*100:.1f}% | {s['pct_trade']*100:.1f}% |")

        print(f"\n### Volatility Bucket Stability: {best_tf} h={best_h} θ={best_t}\n")
        print("| Vol Bucket | %BUY | %HOLD | %SELL | %TRADE |")
        print("|------------|------|-------|-------|--------|")
        for b in ["1", "2", "3", "4", "5"]:
            if b in best_r.get("vol_stability", {}):
                s = best_r["vol_stability"][b]
                print(f"| {b} | {s['pct_buy']*100:.1f}% | {s['pct_hold']*100:.1f}% | {s['pct_sell']*100:.1f}% | {s['pct_trade']*100:.1f}% |")

    # Leakage verification
    print("\n\n### Leakage Verification\n")
    print("The vol-adjusted target uses ONLY historical returns for σ estimation:")
    print("  σ_i = std(past_returns[i-VOL_WINDOW : i]) * sqrt(horizon)")
    print("  r_i = close[i+horizon] / close[i] - 1")
    print("  y_i = r_i / σ_i")
    print("")
    print("The future return (close[i+horizon]) is NEVER used in σ computation.")
    print("This is verified by the LabelEngine.verify_no_future_leakage_in_volatility() test.")

    print("\n---\n")
    print("Analysis complete. Results saved to:", DATA_DIR)


if __name__ == "__main__":
    main()
