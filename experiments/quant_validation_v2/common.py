"""Shared utilities for Quant Validation v2 — data loader + feature builder.

Loads OHLCV 1h, funding rates, computes base TA + MTF (4h/1d) + funding features.
Reuses evaluation infrastructure from QV1 common.py.
"""

from __future__ import annotations

import warnings
import json
from pathlib import Path

import numpy as np
import pandas as pd
import ta as ta_lib

warnings.filterwarnings("ignore", category=UserWarning, module="ta")
warnings.filterwarnings("ignore", category=FutureWarning)

OHLCV_PATH = Path(__file__).parent.parent.parent / "apps" / "analytics-engine" / "data" / "btc_usdt_1h.parquet"
FUNDING_PATH = Path(__file__).parent.parent.parent / "apps" / "analytics-engine" / "data" / "btc_funding_rates.parquet"
REPORT_DIR = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v2"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

SYMBOL = "BTC/USDT"
TIMEFRAME = "1h"

BASE_FEATURES = [
    "open", "high", "low", "close", "volume",
    "rsi", "ema_fast", "ema_medium", "ema_slow",
    "macd", "macd_signal", "macd_hist",
    "bb_upper", "bb_middle", "bb_lower",
    "atr", "adx", "obv", "volume_sma", "pct_change",
]

MTF_INDICATORS = [
    "rsi", "ema_fast", "ema_medium", "ema_slow",
    "macd", "macd_signal", "macd_hist",
    "bb_upper", "bb_middle", "bb_lower",
    "atr", "adx", "obv", "volume_sma", "pct_change",
]

FUNDING_FEATURES = ["funding_rate", "funding_momentum", "funding_change"]

N_FOLDS = 4
SHUFFLE_SEEDS = list(range(42, 52))
RANDOM_STATE = 42


# ── helpers ────────────────────────────────────────────────────────


def save_report(data: dict, filename: str) -> Path:
    path = REPORT_DIR / filename
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"[report] saved to {path}")
    return path


# ── data loading ──────────────────────────────────────────────────


def load_ohlcv() -> pd.DataFrame:
    if not OHLCV_PATH.exists():
        raise FileNotFoundError(f"OHLCV not found: {OHLCV_PATH}")
    df = pd.read_parquet(OHLCV_PATH)
    print(f"[data] OHLCV: {len(df)} rows, {df['timestamp'].min()} → {df['timestamp'].max()}")
    return df


def load_funding() -> pd.DataFrame:
    if not FUNDING_PATH.exists():
        raise FileNotFoundError(f"Funding not found: {FUNDING_PATH}")
    df = pd.read_parquet(FUNDING_PATH)
    print(f"[data] Funding: {len(df)} rows, {df['timestamp'].min()} → {df['timestamp'].max()}")
    return df


# ── feature builders ──────────────────────────────────────────────


def compute_base_ta(df: pd.DataFrame) -> pd.DataFrame:
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
    result = pd.DataFrame(raw, columns=BASE_FEATURES)
    result.index = pd.to_datetime(df["timestamp"]).values
    return result.bfill().ffill()


def compute_mtf_features(df: pd.DataFrame, tfs: tuple[str, ...] = ("4h", "1d")) -> pd.DataFrame:
    df_in = df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
    df_in["timestamp"] = pd.to_datetime(df_in["timestamp"])
    indexed = df_in.sort_values("timestamp").set_index("timestamp")
    ohlcv = indexed[["open", "high", "low", "close", "volume"]].astype(float)
    result = pd.DataFrame(index=ohlcv.index)
    for tf in tfs:
        resampled = ohlcv.resample(tf).agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum",
        }).dropna()
        out = pd.DataFrame(index=resampled.index)
        c, h, l, v = resampled["close"], resampled["high"], resampled["low"], resampled["volume"]
        out["rsi"] = ta_lib.momentum.RSIIndicator(c, window=14).rsi()
        out["ema_fast"] = ta_lib.trend.EMAIndicator(c, window=9).ema_indicator()
        out["ema_medium"] = ta_lib.trend.EMAIndicator(c, window=21).ema_indicator()
        out["ema_slow"] = ta_lib.trend.EMAIndicator(c, window=50).ema_indicator()
        macd = ta_lib.trend.MACD(c)
        out["macd"], out["macd_signal"], out["macd_hist"] = macd.macd(), macd.macd_signal(), macd.macd_diff()
        bb = ta_lib.volatility.BollingerBands(c, window=20, window_dev=2)
        out["bb_upper"], out["bb_middle"], out["bb_lower"] = bb.bollinger_hband(), bb.bollinger_mavg(), bb.bollinger_lband()
        w = min(14, len(resampled) - 1)
        out["atr"] = ta_lib.volatility.AverageTrueRange(h, l, c, window=w).average_true_range()
        out["adx"] = ta_lib.trend.ADXIndicator(h, l, c, window=w).adx()
        out["obv"] = ta_lib.volume.OnBalanceVolumeIndicator(c, v).on_balance_volume()
        out["volume_sma"] = v.rolling(min(20, len(resampled))).mean()
        out["pct_change"] = c.pct_change() * 100.0
        out = out.bfill().ffill().fillna(0.0)
        aligned = out.reindex(ohlcv.index, method="ffill")
        aligned.columns = [f"htf_{col}_{tf}" for col in aligned.columns]
        result = result.join(aligned, how="left")
    result = result.bfill().ffill().fillna(0.0)
    return result


def compute_funding_features(ohlcv_df: pd.DataFrame, funding_df: pd.DataFrame) -> pd.DataFrame:
    ts = ohlcv_df["timestamp"].copy()
    fund = funding_df[["timestamp", "fundingRate"]].copy()
    fund["timestamp"] = pd.to_datetime(fund["timestamp"])
    fund = fund.sort_values("timestamp").drop_duplicates(subset="timestamp").set_index("timestamp")
    ohlcv_index = pd.to_datetime(ts).sort_values()
    aligned = fund.reindex(ohlcv_index, method="ffill")
    rate = aligned["fundingRate"].fillna(0.0).values
    momentum = np.full_like(rate, 0.0, dtype=np.float64)
    change = np.full_like(rate, 0.0, dtype=np.float64)
    if len(rate) > 3:
        momentum[3:] = rate[3:] - rate[:-3]
        change[1:] = rate[1:] - rate[:-1]
    return pd.DataFrame({
        "funding_rate": rate * 100.0,
        "funding_momentum": momentum * 100.0,
        "funding_change": change * 100.0,
    }, index=ohlcv_index.values)


# ── unified feature matrix builder ────────────────────────────────


def build_feature_matrix(ohlcv: pd.DataFrame, funding: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, list[str], pd.DataFrame]:
    base = compute_base_ta(ohlcv)
    mtf = compute_mtf_features(ohlcv, ("4h", "1d"))
    fund_feat = compute_funding_features(ohlcv, funding)
    feature_names = BASE_FEATURES + list(mtf.columns) + FUNDING_FEATURES
    combined = pd.concat([base, mtf, fund_feat], axis=1)
    combined = combined.bfill().ffill().fillna(0.0).astype(np.float64)
    print(f"[features] {len(feature_names)} total: {len(BASE_FEATURES)} base + {mtf.shape[1]} mtf + {len(FUNDING_FEATURES)} funding")
    print(f"[features] shape={combined.shape}")
    return combined.values, combined.index.values, feature_names, combined


# ── label generators (same as QV1) ────────────────────────────────


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
    """0=SELL, 1=BUY. Returns (labels, valid_mask). HOLD → mask False."""
    adj = compute_vol_adj_returns(df, lookahead)
    labels = np.full(len(adj), -1, dtype=int)
    labels[adj > threshold] = 1
    labels[adj < -threshold] = 0
    return labels, labels != -1


def label_regression(df: pd.DataFrame, lookahead: int = 5) -> np.ndarray:
    return compute_vol_adj_returns(df, lookahead).values.astype(np.float64)
