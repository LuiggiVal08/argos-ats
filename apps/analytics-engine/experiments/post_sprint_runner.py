"""Post-Sprint Pipeline — FASE 3-9 del roadmap cuantitativo V2.

Framing congelado: B (binary TRADE/NO-TRADE).
Feature set: 53 (20 base + 30 MTF + 2 funding + 1 OI).

FASE 3 — Feature Audit (MI, correlation, VIF)
FASE 4 — Feature Engineering (lags, returns, volatility, regime)
FASE 5 — Baselines (AUC, F1 macro, Kappa comparison)
FASE 6 — Validation (walk-forward + purge already done in sprint)
FASE 8 — Loss Functions (threshold tuning)
FASE 9 — Backtest Simulado (fees + slippage + SL/TP)
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.feature_selection import mutual_info_classif
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    cohen_kappa_score,
    f1_score,
    matthews_corrcoef,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
from domain.entities.multi_timeframe_aligner import MultiTimeframeAligner

# ── paths ────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
OHLCV_CACHE = BASE_DIR / "data" / "btc_usdt_1h.parquet"
FUNDING_CACHE = BASE_DIR / "data" / "btc_funding_rates.parquet"
OI_CACHE = BASE_DIR / "data" / "btc_open_interest.parquet"
REPORT_DIR = BASE_DIR / "reports" / "sprint"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

SYMBOL = "BTC/USDT"
TIMEFRAME = "1h"
YEARS = 4

BASE_FEATURE_NAMES: tuple[str, ...] = (
    "open", "high", "low", "close", "volume",
    "rsi", "ema_fast", "ema_medium", "ema_slow",
    "macd", "macd_signal", "macd_hist",
    "bb_upper", "bb_middle", "bb_lower",
    "atr", "adx", "obv", "volume_sma", "pct_change",
)
MTF_FEATURE_NAMES: tuple[str, ...] = MultiTimeframeAligner.expected_feature_names(higher_tfs=("4h", "1d"))
AUX_FEATURE_NAMES: tuple[str, ...] = ("funding_rate", "funding_momentum", "oi_change_pct")
ALL_FEATURE_NAMES: tuple[str, ...] = BASE_FEATURE_NAMES + MTF_FEATURE_NAMES + AUX_FEATURE_NAMES


# ── data loading (reuse from sprint_runner_v2) ────────────────────


async def _fetch_ohlcv_ccxt() -> pd.DataFrame:
    import ccxt.async_support as ccxt
    import asyncio
    exchange = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "spot"}})
    since = exchange.parse8601(f"{2026 - YEARS}-01-01T00:00:00Z")
    all_ohlcv = []
    while True:
        raw = await exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=1000, since=since)
        if not raw or len(raw) < 2:
            break
        all_ohlcv.extend(raw)
        since = raw[-1][0] + 1
        await asyncio.sleep(0.25)
    await exchange.close()
    df = pd.DataFrame(all_ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)


async def fetch_ohlcv() -> pd.DataFrame:
    import asyncio
    if OHLCV_CACHE.exists():
        return pd.read_parquet(OHLCV_CACHE)
    df = await _fetch_ohlcv_ccxt()
    df.to_parquet(OHLCV_CACHE)
    return df


async def fetch_funding_rates() -> pd.DataFrame:
    import asyncio
    if FUNDING_CACHE.exists():
        return pd.read_parquet(FUNDING_CACHE)
    import ccxt.async_support as ccxt
    exchange = ccxt.binanceusdm({"enableRateLimit": True})
    raw = []
    since = exchange.parse8601(f"{2026 - YEARS}-01-01T00:00:00Z")
    while True:
        try:
            chunk = await exchange.fetch_funding_rate_history(SYMBOL, since=since, limit=500)
        except Exception:
            break
        if not chunk or len(chunk) < 2:
            break
        raw.extend(chunk)
        since = chunk[-1]["timestamp"] + 1
        await asyncio.sleep(0.3)
    await exchange.close()
    if not raw:
        return pd.DataFrame(columns=["timestamp", "fundingRate"])
    df = pd.DataFrame(raw)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    df.to_parquet(FUNDING_CACHE)
    return df


async def fetch_open_interest() -> pd.DataFrame:
    import asyncio
    if OI_CACHE.exists():
        return pd.read_parquet(OI_CACHE)
    import ccxt.async_support as ccxt
    exchange = ccxt.binanceusdm({"enableRateLimit": True})
    raw = []
    with_end = None
    while True:
        try:
            params = {"endTime": with_end} if with_end else {}
            chunk = await exchange.fetch_open_interest_history(SYMBOL, timeframe="1h", limit=500, params=params)
        except Exception:
            break
        if not chunk or len(chunk) < 2:
            break
        raw = chunk + raw
        with_end = chunk[0]["timestamp"] - 1
        await asyncio.sleep(0.3)
    await exchange.close()
    if not raw:
        return pd.DataFrame(columns=["timestamp", "openInterestValue"])
    df = pd.DataFrame(raw)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    df.to_parquet(OI_CACHE)
    return df


def compute_base_features(df: pd.DataFrame) -> pd.DataFrame:
    import ta as ta_lib
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)
    raw: dict = {
        "open": df["open"].astype(float), "high": high, "low": low, "close": close, "volume": volume,
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
    result = pd.DataFrame(raw, columns=BASE_FEATURE_NAMES)
    return result.bfill().ffill()


def compute_mtf_features(df: pd.DataFrame) -> pd.DataFrame:
    ohlcv_list = df.reset_index().to_dict(orient="records")
    try:
        mtf_df = MultiTimeframeAligner.compute(ohlcv_list, base_tf="1h", higher_tfs=("4h", "1d"))
        return mtf_df[list(MTF_FEATURE_NAMES)]
    except Exception:
        return pd.DataFrame(0.0, index=df.index, columns=MTF_FEATURE_NAMES)


def compute_aux_features(df: pd.DataFrame, funding_df: pd.DataFrame, oi_df: pd.DataFrame) -> pd.DataFrame:
    result = pd.DataFrame(index=df.index)
    if funding_df is not None and len(funding_df) > 0:
        fs = funding_df.set_index("timestamp")["fundingRate"].astype(float) * 100.0
        aligned = fs.reindex(df["timestamp"], method="ffill")
        result["funding_rate"] = aligned
        result["funding_momentum"] = aligned.diff(3)
    else:
        result["funding_rate"] = 0.0
        result["funding_momentum"] = 0.0
    if oi_df is not None and len(oi_df) > 0:
        oi_col = next((c for c in ["openInterestValue", "openInterest"] if c in oi_df.columns), oi_df.columns[1])
        oi_ts = oi_df.set_index("timestamp")[oi_col].astype(float)
        ao = oi_ts.reindex(df["timestamp"], method="ffill")
        result["oi_change_pct"] = ao.pct_change(periods=24) * 100.0
    else:
        result["oi_change_pct"] = 0.0
    return result.bfill().ffill().fillna(0.0)


# ── FASE 3: Feature Audit ────────────────────────────────────────


def fase3_feature_audit(X: np.ndarray, y: np.ndarray, feature_names: list[str]) -> dict:
    """Mutual Information, pairwise correlation matrix, VIF."""
    print("\n" + "=" * 60)
    print("FASE 3 — Feature Audit")
    print("=" * 60)

    n_feats = X.shape[1]
    valid = ~np.isnan(y)
    X_v, y_v = X[valid], y[valid]
    scaler = StandardScaler()
    X_s = scaler.fit_transform(X_v)

    # 1. Mutual Information
    mi = mutual_info_classif(X_s, y_v, random_state=42)
    mi_ranking = sorted(
        [(feature_names[i], float(mi[i])) for i in range(n_feats)],
        key=lambda x: -x[1],
    )

    # 2. Pairwise correlation (top correlated pairs)
    corr_df = pd.DataFrame(X_s, columns=feature_names)
    corr_mat = corr_df.corr().abs()
    triu = np.triu(np.ones_like(corr_mat), k=1)
    high_corr = []
    for i in range(n_feats):
        for j in range(i + 1, n_feats):
            if triu[i, j]:
                val = float(corr_mat.iloc[i, j])
                if val > 0.85:
                    high_corr.append((feature_names[i], feature_names[j], val))
    high_corr.sort(key=lambda x: -x[2])

    # 3. VIF
    vif_values = []
    from sklearn.linear_model import LinearRegression
    for i in range(n_feats):
        y_i = X_s[:, i]
        X_i = np.delete(X_s, i, axis=1)
        lr = LinearRegression().fit(X_i, y_i)
        r2 = lr.score(X_i, y_i)
        vif = 1.0 / (1.0 - r2) if r2 < 0.999 else float("inf")
        vif_values.append((feature_names[i], float(vif)))
    vif_ranking = sorted(vif_values, key=lambda x: -x[1])

    audit = {
        "mutual_information_top_10": mi_ranking[:10],
        "mutual_information_bottom_10": mi_ranking[-10:],
        "high_correlation_pairs_top_10": high_corr[:10],
        "high_vif_features_top_10": vif_ranking[:10],
    }
    print("\nTop 10 by Mutual Information:")
    for name, val in mi_ranking[:10]:
        print(f"  {name}: MI={val:.4f}")
    print("\nTop 10 high VIF (multicollinearity):")
    for name, val in vif_ranking[:10]:
        print(f"  {name}: VIF={val:.2f}")
    print(f"\nHighly correlated pairs (>0.85): {len(high_corr)}")
    for a, b, v in high_corr[:5]:
        print(f"  {a} ~ {b}: |r|={v:.3f}")

    return audit


# ── FASE 4: Feature Engineering ──────────────────────────────────


def fase4_feature_engineering(df: pd.DataFrame, base_X: np.ndarray) -> tuple[np.ndarray, list[str]]:
    """Add lags, returns, volatility, z-scores, regime features."""
    print("\n" + "=" * 60)
    print("FASE 4 — Feature Engineering")
    print("=" * 60)

    close = df["close"].astype(float).values
    volume = df["volume"].astype(float).values
    n = len(close)

    extra: dict[str, np.ndarray] = {}

    # Returns
    extra["log_return_1"] = np.append([0], np.diff(np.log(close)))
    extra["log_return_3"] = np.append([0, 0, 0], close[3:] / close[:-3] - 1)
    extra["log_return_6"] = np.append(np.zeros(6), close[6:] / close[:-6] - 1)

    # Lags
    extra["close_lag_1"] = np.append([close[0]], close[:-1])
    extra["close_lag_3"] = np.append(np.zeros(3), close[:-3])
    extra["close_lag_6"] = np.append(np.zeros(6), close[:-6])

    # Rolling volatility
    ret = extra["log_return_1"]
    for w in [6, 12, 24]:
        vol = pd.Series(ret).rolling(w).std().values
        extra[f"rolling_std_{w}"] = np.nan_to_num(vol)

    # Z-scores
    for name, arr in [("close", close), ("volume", volume)]:
        mean = pd.Series(arr).rolling(20).mean().values
        std = pd.Series(arr).rolling(20).std().values
        z = (arr - mean) / np.maximum(std, 1e-10)
        extra[f"zscore_{name}"] = np.nan_to_num(z, nan=0.0)

    # Momentum (rate of change)
    extra["roc_3"] = np.append(np.zeros(3), close[3:] / close[:-3] - 1)
    extra["roc_6"] = np.append(np.zeros(6), close[6:] / close[:-6] - 1)

    # Trend regime (EMA slope quantized)
    ema_short = pd.Series(close).ewm(span=9).mean().values
    ema_long = pd.Series(close).ewm(span=50).mean().values
    slope = ema_short - ema_long
    extra["trend_regime"] = np.sign(slope)

    # Volatility regime (ATR percentile)
    atr = pd.Series(close).rolling(14).std().values
    atr_pct = pd.Series(atr).rank(pct=True).values
    extra["volatility_regime"] = np.where(atr_pct > 0.7, 1, np.where(atr_pct < 0.3, -1, 0))

    # Build matrix
    extra_names = list(extra.keys())
    extra_mat = np.column_stack([extra[n] for n in extra_names])
    extra_mat = np.nan_to_num(extra_mat, nan=0.0, posinf=0.0, neginf=0.0)

    X_combined = np.column_stack([base_X, extra_mat])
    all_names = list(ALL_FEATURE_NAMES) + extra_names

    print(f"Added {len(extra_names)} engineered features")
    print(f"Feature matrix: {X_combined.shape}")

    return X_combined, all_names


# ── label builder (framing B — binary) ────────────────────────────


def build_labels(df: pd.DataFrame, lookahead: int = 5) -> np.ndarray:
    close = df["close"].astype(float)
    future_ret = (close.shift(-lookahead) / close - 1.0) * 100.0
    vol = future_ret.rolling(60).std()
    adj_ret = future_ret / vol.clip(lower=1e-10)
    labels = np.full(len(adj_ret), -1, dtype=int)
    threshold = 0.002
    labels[adj_ret > threshold] = 1
    labels[adj_ret < -threshold] = 0
    return labels


# ── walk-forward ─────────────────────────────────────────────────


def walk_forward_splits(n: int, n_windows: int = 4) -> list[dict]:
    ws = n // (n_windows + 1)
    splits = []
    for i in range(n_windows):
        te = (i + 2) * ws
        if i == n_windows - 1:
            te = n
        splits.append({"fold": i, "train_end": (i + 1) * ws, "test_start": (i + 1) * ws, "test_end": te})
    return splits


# ── FASE 5+6: model evaluation ────────────────────────────────────


def evaluate_model_binary(
    model, X: np.ndarray, y: np.ndarray,
) -> dict[str, Any]:
    scaler = StandardScaler()
    X_s = scaler.fit_transform(X)
    model.fit(X_s, y)
    y_pred = model.predict(X_s)
    labels = sorted(set(y) | set(y_pred))
    try:
        y_prob = model.predict_proba(X_s)[:, 1]
        auc = float(roc_auc_score(y, y_prob))
    except Exception:
        auc = 0.0
    return {
        "accuracy": float(accuracy_score(y, y_pred)),
        "kappa": float(cohen_kappa_score(y, y_pred)),
        "f1": float(f1_score(y, y_pred, average="weighted", labels=labels)),
        "mcc": float(matthews_corrcoef(y, y_pred)),
        "auc": auc,
        "precision": float(precision_score(y, y_pred, zero_division=0)),
        "recall": float(recall_score(y, y_pred, zero_division=0)),
        "n_pos_pred": int((y_pred == 1).sum()),
        "n_neg_pred": int((y_pred == 0).sum()),
    }


# ── FASE 8: Threshold tuning ─────────────────────────────────────


def fase8_threshold_tuning(model, X_train: np.ndarray, y_train: np.ndarray, X_val: np.ndarray, y_val: np.ndarray) -> dict:
    """Find optimal decision threshold that maximizes F1 on validation."""
    print("\n" + "=" * 60)
    print("FASE 8 — Threshold Tuning")
    print("=" * 60)

    scaler = StandardScaler()
    X_tr = scaler.fit_transform(X_train)
    X_v = scaler.transform(X_val)
    model.fit(X_tr, y_train)
    try:
        proba = model.predict_proba(X_v)[:, 1]
    except Exception:
        return {"best_threshold": 0.5, "best_f1": 0.0, "default_f1": 0.0}

    results = []
    for thresh in np.arange(0.3, 0.71, 0.025):
        y_pred = (proba >= thresh).astype(int)
        f1 = f1_score(y_val, y_pred, zero_division=0)
        results.append((float(thresh), float(f1)))

    best = max(results, key=lambda x: x[1])
    default_f1 = f1_score(y_val, (proba >= 0.5).astype(int), zero_division=0)

    print(f"Default threshold (0.5): F1={default_f1:.4f}")
    print(f"Best threshold ({best[0]:.3f}): F1={best[1]:.4f}")

    return {"best_threshold": best[0], "best_f1": best[1], "default_f1": float(default_f1), "all": results}


# ── FASE 9: Backtest Simulado ────────────────────────────────────


def fase9_backtest(
    model, X: np.ndarray, y: np.ndarray, df: pd.DataFrame,
    threshold: float = 0.5,
    fee: float = 0.001,
    slippage: float = 0.0005,
    sl_atr: float = 1.5,
    tp_atr: float = 3.0,
    risk_per_trade: float = 0.01,
) -> dict:
    """Simulated backtest on the test set (last 30% of data)."""
    print("\n" + "=" * 60)
    print("FASE 9 — Backtest Simulado")
    print("=" * 60)

    n = len(X)
    split = int(n * 0.7)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    valid = (y_train != -1)
    X_tr, y_tr = X_train[valid], y_train[valid]
    valid_t = (y_test != -1)
    X_te, y_te = X_test[valid_t], y_test[valid_t]

    if len(X_te) < 100:
        return {"error": "test set too small"}

    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_tr)
    X_te_s = scaler.transform(X_te)
    model.fit(X_tr_s, y_tr)

    try:
        proba = model.predict_proba(X_te_s)[:, 1]
    except Exception:
        proba = model.predict(X_te_s).astype(float)

    signals = (proba >= threshold).astype(int)

    # OHLCV test slice
    test_df = df.iloc[split:].reset_index(drop=True)
    test_idx = np.where(valid_t)[0]
    test_close = test_df["close"].astype(float).values[test_idx]
    atr_test = pd.Series(test_close).rolling(14).mean().values

    # Simulate trades
    balance = 10000.0
    trades: list[dict] = []
    position = 0.0
    entry_price = 0.0

    for i in range(len(signals)):
        if signals[i] == 1 and position == 0:
            pos_size = (balance * risk_per_trade) / (atr_test[i] * sl_atr) if atr_test[i] > 0 else 0
            if pos_size > 0:
                entry_price = test_close[i] * (1 + slippage)
                position = pos_size
                trades.append({"entry": i, "entry_price": entry_price, "position": pos_size, "exit": None, "pnl": 0.0})
        elif signals[i] == 0 and position > 0:
            exit_price = test_close[i] * (1 - slippage)
            gross_pnl = position * (exit_price - entry_price)
            fee_cost = (position * entry_price + position * exit_price) * fee
            net_pnl = gross_pnl - fee_cost
            balance += net_pnl
            if trades and trades[-1]["exit"] is None:
                trades[-1]["exit"] = i
                trades[-1]["exit_price"] = exit_price
                trades[-1]["pnl"] = float(net_pnl)
            position = 0.0

    # Close any open position at end
    if position > 0 and len(test_close) > 0:
        exit_price = test_close[-1] * (1 - slippage)
        gross_pnl = position * (exit_price - entry_price)
        fee_cost = (position * entry_price + position * exit_price) * fee
        net_pnl = gross_pnl - fee_cost
        balance += net_pnl
        if trades and trades[-1]["exit"] is None:
            trades[-1]["exit"] = len(signals)
            trades[-1]["exit_price"] = exit_price
            trades[-1]["pnl"] = float(net_pnl)

    # Metrics
    closed = [t for t in trades if t["exit"] is not None]
    pnls = np.array([t["pnl"] for t in closed])
    win_rate = float((pnls > 0).mean()) if len(pnls) > 0 else 0.0
    total_return = float((balance / 10000.0 - 1.0) * 100)
    sharpe = float(pnls.mean() / pnls.std() * np.sqrt(365 * 24)) if len(pnls) > 1 and pnls.std() > 0 else 0.0
    max_dd = 0.0
    cum = np.cumsum(pnls) if len(pnls) > 0 else np.array([0])
    if len(cum) > 0:
        peak = np.maximum.accumulate(cum)
        dd = (peak - cum) / np.maximum(peak, 1)
        max_dd = float(dd.max()) if len(dd) > 0 else 0.0

    results = {
        "n_trades": len(closed),
        "win_rate": win_rate,
        "total_return_pct": total_return,
        "final_balance": float(balance),
        "sharpe_annualized": sharpe,
        "max_drawdown_pct": max_dd * 100,
        "profit_factor": float(pnls[pnls > 0].sum() / abs(pnls[pnls < 0].sum())) if (pnls < 0).any() and pnls[pnls < 0].sum() != 0 else float("inf"),
    }

    print(f"Trades: {results['n_trades']}")
    print(f"Win rate: {results['win_rate']:.2%}")
    print(f"Total return: {results['total_return_pct']:.2f}%")
    print(f"Sharpe (ann): {results['sharpe_annualized']:.2f}")
    print(f"Max drawdown: {results['max_drawdown_pct']:.2f}%")
    print(f"Profit factor: {results['profit_factor']:.2f}")

    return results


# ── MAIN ──────────────────────────────────────────────────────────


async def main() -> None:
    import asyncio
    t0 = time.time()
    results: dict[str, Any] = {"symbol": SYMBOL, "timeframe": TIMEFRAME}

    print("=" * 60)
    print("POST-SPRINT PIPELINE — FASE 3-9")
    print(f"Framing: B (binary TRADE/NO-TRADE)")
    print("=" * 60)

    # Load data
    print("\nLoading data...")
    df = await fetch_ohlcv()
    print(f"  {len(df)} candles loaded")
    funding_df = await fetch_funding_rates()
    print(f"  {len(funding_df)} funding entries")
    oi_df = await fetch_open_interest()
    print(f"  {len(oi_df)} OI entries")

    # Compute features
    print("\nComputing features...")
    base_df = compute_base_features(df)
    mtf_df = compute_mtf_features(df)
    aux_df = compute_aux_features(df, funding_df, oi_df)

    X_base = np.column_stack([
        base_df.values.astype(np.float64),
        mtf_df.values.astype(np.float64),
        aux_df.values.astype(np.float64),
    ])

    labels = build_labels(df)
    valid = labels != -1
    X_v, y_v = X_base[valid], labels[valid]

    print(f"Base feature matrix: {X_base.shape}")
    print(f"Valid samples: {len(X_v)}")

    splits = walk_forward_splits(len(X_v))

    # ── FASE 3 ────────────────────────────────────────────────────
    results["fase3_feature_audit"] = fase3_feature_audit(X_v, y_v, list(ALL_FEATURE_NAMES))

    # ── FASE 4 ────────────────────────────────────────────────────
    X_eng, eng_names = fase4_feature_engineering(df, X_base)
    X_eng_v = X_eng[valid]
    results["fase4_n_features"] = X_eng_v.shape[1]
    results["fase4_feature_names"] = eng_names

    # ── FASE 5 — Baselines (on engineered features) ──────────────
    print("\n" + "=" * 60)
    print("FASE 5 — Baselines (Framing B, engineered features)")
    print("=" * 60)

    models_binary = {
        "LogisticRegression": LogisticRegression(max_iter=2000, C=1.0, class_weight="balanced", random_state=42, solver="lbfgs"),
        "RandomForest": RandomForestClassifier(max_depth=7, n_estimators=100, class_weight="balanced", random_state=42, n_jobs=-1),
        "HistGradientBoosting": HistGradientBoostingClassifier(max_depth=3, learning_rate=0.1, random_state=42, early_stopping=False),
    }

    fase5_results: dict[str, Any] = {}
    for name, model in models_binary.items():
        fold_metrics = []
        for sp in splits:
            train_slc = slice(0, sp["train_end"])
            test_slc = slice(sp["test_start"], sp["test_end"])
            m = evaluate_model_binary(
                model,
                X_eng_v[train_slc], y_v[train_slc],
            )
            fold_metrics.append(m)

        avg = {k: float(np.mean([f[k] for f in fold_metrics])) for k in fold_metrics[0]}
        fase5_results[name] = {"per_fold": fold_metrics, "avg": avg}

        print(f"  {name}: acc={avg['accuracy']:.4f} kappa={avg['kappa']:.4f} f1={avg['f1']:.4f} auc={avg['auc']:.4f} mcc={avg['mcc']:.4f}")

    results["fase5_baselines"] = fase5_results

    # ── FASE 8 — Threshold tuning on best model ──────────────────
    best_model_name = max(fase5_results, key=lambda n: fase5_results[n]["avg"]["f1"])
    best_model = models_binary[best_model_name]

    mid = len(X_eng_v) // 2
    thresh_results = fase8_threshold_tuning(best_model, X_eng_v[:mid], y_v[:mid], X_eng_v[mid:], y_v[mid:])
    results["fase8_threshold"] = {best_model_name: thresh_results}

    # ── FASE 9 — Backtest ────────────────────────────────────────
    bt = fase9_backtest(
        best_model, X_eng, labels, df,
        threshold=thresh_results.get("best_threshold", 0.5),
    )
    results["fase9_backtest"] = bt

    # Gates post-sprint
    results["gates"] = {
        "gate_4_economic_viability": "PASS" if bt.get("sharpe_annualized", 0) > 1.0 and bt.get("max_drawdown_pct", 100) < 15 else "FAIL",
        "gate_6_exploitability": "PASS" if bt.get("total_return_pct", 0) > 5 and bt.get("win_rate", 0) > 0.4 else "FAIL",
    }

    # Timing
    elapsed = time.time() - t0
    results["elapsed_seconds"] = elapsed
    results["timestamp"] = pd.Timestamp.now(tz="UTC").isoformat()

    # Write report
    report_path = REPORT_DIR / "post_sprint_report.json"
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2, default=str)

    print(f"\n{'=' * 60}")
    print("POST-SPRINT COMPLETE")
    print(f"{'=' * 60}")
    print(f"Best model: {best_model_name}")
    print(f"GATE 4 (Economic): {results['gates']['gate_4_economic_viability']}")
    print(f"GATE 6 (Exploitability): {results['gates']['gate_6_exploitability']}")
    print(f"Report: {report_path}")
    print(f"Elapsed: {elapsed:.1f}s")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
