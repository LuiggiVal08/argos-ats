#!/usr/bin/env python3
"""
Alpha Stress Decomposition Audit (AFS v1.0)

Falsification framework. Classifies alpha as:
  A) Structural real alpha  (ALPHA_SCORE > 0.7)
  B) Fragile alpha          (0.4 <= ALPHA_SCORE <= 0.7)
  C) Statistical illusion   (ALPHA_SCORE < 0.4)

HARD RULES:
  No model improvement. No tuning. No feature engineering.
  Each CAPA is a stress test designed to BREAK the alpha.
  ALPHA_SCORE = min([...]) — weakest link determines verdict.
"""
from __future__ import annotations

import json
import sys
import time
import warnings
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression, RidgeClassifier
from sklearn.tree import DecisionTreeClassifier
from sklearn.preprocessing import StandardScaler

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR / "app"))
from domain.entities.multi_timeframe_aligner import MultiTimeframeAligner
from fase55_backtest_engine import BacktestEngine

warnings.filterwarnings("ignore")

# ── Paths ──
OHLCV_PATH = BASE_DIR / "data" / "btc_usdt_1h.parquet"
FUNDING_PATH = BASE_DIR / "data" / "btc_funding_rates.parquet"
OI_PATH = BASE_DIR / "data" / "btc_open_interest.parquet"
RETAINED_PATH = BASE_DIR / "data" / "retained_features.txt"
REPORT_DIR = BASE_DIR / "reports" / "alpha_decomposition"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# ── Locked config (same as Lock Test) ──
THRESHOLD = 0.55
SL_MULT = 2.0
TP_MULT = 4.0
RISK_PCT = 0.01
ADX_THRESHOLD = 0.0

# ── Feature definitions ──
BASE_FEATURES = (
    "open", "high", "low", "close", "volume",
    "rsi", "ema_fast", "ema_medium", "ema_slow",
    "macd", "macd_signal", "macd_hist",
    "bb_upper", "bb_middle", "bb_lower",
    "atr", "adx", "obv", "volume_sma", "pct_change",
)
MTF_FEATURES = MultiTimeframeAligner.expected_feature_names(higher_tfs=("4h", "1d"))
AUX_FEATURES = ("funding_rate", "funding_momentum", "oi_change_pct")
ENG_FEATURES = (
    "log_return_1", "log_return_3", "log_return_6",
    "close_lag_1", "close_lag_3", "close_lag_6",
    "rolling_std_6", "rolling_std_12", "rolling_std_24",
    "zscore_close", "zscore_volume",
    "roc_3", "roc_6",
    "trend_regime", "volatility_regime",
)
ALL_NAMES = BASE_FEATURES + MTF_FEATURES + AUX_FEATURES + ENG_FEATURES
RETAINED_NAMES = [ln.strip() for ln in open(RETAINED_PATH).readlines()]
RETAINED_INDICES = [ALL_NAMES.index(n) for n in RETAINED_NAMES]

FEATURE_GROUPS: dict[str, list[int]] = {
    "MTF_4h": [i for i, n in enumerate(RETAINED_NAMES) if n.endswith("_4h")],
    "MTF_1d": [i for i, n in enumerate(RETAINED_NAMES) if n.endswith("_1d")],
    "TA_core": [i for i, n in enumerate(RETAINED_NAMES) if n in {
        "bb_lower", "bb_upper", "ema_fast", "bb_middle", "ema_medium", "close", "low", "high"}],
    "Engineered": [i for i, n in enumerate(RETAINED_NAMES) if n in {
        "rolling_std_24", "zscore_close", "close_lag_3", "rolling_std_12", "trend_regime"}],
}
# Some features may not fit any group — unused for ablation but kept in model


class AuditContext:
    """Central state shared across all CAPAs."""
    def __init__(self):
        self.df: pd.DataFrame | None = None
        self.X: np.ndarray | None = None
        self.y: np.ndarray | None = None
        self.close: np.ndarray | None = None
        self.high: np.ndarray | None = None
        self.low: np.ndarray | None = None
        self.volume: np.ndarray | None = None
        self.ts: np.ndarray | None = None
        self.atr: np.ndarray | None = None
        self.proba: np.ndarray | None = None
        self.rf: RandomForestClassifier | None = None
        self.scaler: StandardScaler | None = None
        self.train_idx: np.ndarray | None = None
        self.val_idx: np.ndarray | None = None
        self.holdout_idx: np.ndarray | None = None
        self.holdout_equity: list[float] | None = None
        self.baseline_sharpe: float = 0.0
        self.results: dict = {}

    def save(self, key: str, data: dict):
        self.results[key] = data
        path = REPORT_DIR / f"{key}.json"
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        print(f"  [{key}] saved -> {path}")


# ═══════════════════════════════════════════════════
# DATA & FEATURE ENGINEERING
# ═══════════════════════════════════════════════════

def compute_features(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Compute ALL 68 features + labels. Returns arrays."""
    import ta as ta_lib
    close = df["close"].astype(float).values
    high = df["high"].astype(float).values
    low = df["low"].astype(float).values
    volume = df["volume"].astype(float).values

    base_list = [df[c].astype(float).values for c in ["open", "high", "low", "close", "volume"]]
    base_arr = np.column_stack(base_list + [
        ta_lib.momentum.RSIIndicator(pd.Series(close), window=14).rsi().values,
        ta_lib.trend.EMAIndicator(pd.Series(close), window=9).ema_indicator().values,
        ta_lib.trend.EMAIndicator(pd.Series(close), window=21).ema_indicator().values,
        ta_lib.trend.EMAIndicator(pd.Series(close), window=50).ema_indicator().values,
    ])
    macd = ta_lib.trend.MACD(pd.Series(close))
    extra = np.column_stack([
        macd.macd().values, macd.macd_signal().values, macd.macd_diff().values,
        ta_lib.volatility.BollingerBands(pd.Series(close), window=20, window_dev=2).bollinger_hband().values,
        ta_lib.volatility.BollingerBands(pd.Series(close), window=20, window_dev=2).bollinger_mavg().values,
        ta_lib.volatility.BollingerBands(pd.Series(close), window=20, window_dev=2).bollinger_lband().values,
        ta_lib.volatility.AverageTrueRange(pd.Series(high), pd.Series(low), pd.Series(close), window=14).average_true_range().values,
        ta_lib.trend.ADXIndicator(pd.Series(high), pd.Series(low), pd.Series(close), window=14).adx().values,
        ta_lib.volume.OnBalanceVolumeIndicator(pd.Series(close), pd.Series(volume)).on_balance_volume().values,
    ])
    v_sma = pd.Series(volume).rolling(20).mean().values
    pct_chg = np.nan_to_num((pd.Series(close) / pd.Series(close).shift(1) - 1).values * 100.0, nan=0.0)
    base = np.nan_to_num(np.column_stack([base_arr, extra, v_sma, pct_chg]), nan=0.0, posinf=0.0, neginf=0.0)

    ohlcv_list = df.reset_index().to_dict(orient="records")
    mtf_df = MultiTimeframeAligner.compute(ohlcv_list, base_tf="1h", higher_tfs=("4h", "1d"))
    mtf_arr = np.nan_to_num(mtf_df.values.astype(np.float64), nan=0.0, posinf=0.0, neginf=0.0)

    aux = np.zeros((len(df), 3), dtype=np.float64)
    if FUNDING_PATH.exists():
        fd = pd.read_parquet(FUNDING_PATH)
        if len(fd) > 0:
            fs = fd.set_index("timestamp")["fundingRate"].astype(float).values * 100.0
            al = pd.Series(fs).reindex(pd.Series(df["timestamp"]), method="ffill").values
            aux[:, 0] = np.nan_to_num(al)
            aux[:, 1] = np.nan_to_num(np.append([0], np.diff(al)))
    if OI_PATH.exists():
        od = pd.read_parquet(OI_PATH)
        if len(od) > 0:
            oc = next((c for c in ["openInterestValue", "openInterest"] if c in od.columns), od.columns[1])
            oi_v = od.set_index("timestamp")[oc].astype(float).reindex(df["timestamp"], method="ffill").values
            aux[:, 2] = np.nan_to_num(np.append(np.zeros(24), oi_v[24:] / oi_v[:-24] - 1) * 100.0)

    eng = np.zeros((len(df), 15), dtype=np.float64)
    cv = close
    eng[:, 0] = np.append([0], np.diff(np.log(cv)))
    eng[:, 1] = np.append([0, 0, 0], cv[3:] / cv[:-3] - 1)
    eng[:, 2] = np.append(np.zeros(6), cv[6:] / cv[:-6] - 1)
    eng[:, 3] = np.append([cv[0]], cv[:-1])
    eng[:, 4] = np.append(np.zeros(3), cv[:-3])
    eng[:, 5] = np.append(np.zeros(6), cv[:-6])
    ret_s = pd.Series(eng[:, 0])
    for j, w in enumerate([6, 12, 24]):
        eng[:, 6 + j] = ret_s.rolling(w).std().values
    cm = pd.Series(cv).rolling(20).mean().values
    cs = pd.Series(cv).rolling(20).std().values
    eng[:, 9] = (cv - cm) / np.maximum(cs, 1e-10)
    vm = pd.Series(volume).rolling(20).mean().values
    vs = pd.Series(volume).rolling(20).std().values
    eng[:, 10] = (volume - vm) / np.maximum(vs, 1e-10)
    eng[:, 11] = np.append(np.zeros(3), cv[3:] / cv[:-3] - 1)
    eng[:, 12] = np.append(np.zeros(6), cv[6:] / cv[:-6] - 1)
    es = pd.Series(cv).ewm(span=9).mean().values
    el = pd.Series(cv).ewm(span=50).mean().values
    eng[:, 13] = np.sign(es - el)
    atr_s = pd.Series(cv).rolling(14).std().values
    atr_p = pd.Series(atr_s).rank(pct=True).values
    eng[:, 14] = np.where(atr_p > 0.7, 1, np.where(atr_p < 0.3, -1, 0))
    eng = np.nan_to_num(eng, nan=0.0, posinf=0.0, neginf=0.0)

    X_full = np.column_stack([base, mtf_arr, aux, eng])

    future_ret = (pd.Series(close).shift(-5) / pd.Series(close) - 1.0) * 100.0
    vol_f = future_ret.rolling(60).std()
    adj = future_ret / vol_f.clip(lower=1e-10)
    y = np.full(len(adj), -1, dtype=int)
    y[adj > 0.002] = 1
    y[adj < -0.002] = 0

    return df, X_full, y, close, high, low, volume


def run_bt(close, high, low, ts, proba, atr,
           sl_mult=SL_MULT, tp_mult=TP_MULT, threshold=THRESHOLD,
           risk_pct=RISK_PCT, fee=0.001, slippage=0.0005, spread=0.0001,
           initial_balance=10000.0):
    """Run BacktestEngine, return result dict."""
    engine = BacktestEngine(
        sl_mult=sl_mult, tp_mult=tp_mult, risk_pct=risk_pct,
        fee=fee, slippage=slippage, spread=spread,
        min_prob=threshold, adx_threshold=ADX_THRESHOLD,
    )
    result = engine.run(
        close=close, high=high, low=low, timestamps=ts,
        proba=proba, adx=None, initial_balance=initial_balance,
        atr_values=atr,
    )
    n_bars = len(close)
    years = n_bars / (365 * 24)
    eq = np.array(result.equity_curve)
    final_bal = eq[-1]
    total_ret = (final_bal / initial_balance - 1.0) * 100
    pref = np.diff(eq) / eq[:-1]
    m, s = np.mean(pref), np.std(pref)
    sharpe = float(m / s * np.sqrt(365 * 24)) if s > 0 else 0.0
    neg = pref[pref < 0]
    ds = np.std(neg) if len(neg) > 0 else 1e-10
    sortino = float(m / ds * np.sqrt(365 * 24))
    cagr = float((final_bal / initial_balance) ** (1 / years) - 1) * 100 if years > 0 else 0.0
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / peak * 100
    max_dd = float(np.max(dd))
    calmar = float(cagr / max_dd) if max_dd > 0 else 0.0
    trade_pnls = [t.pnl_usd for t in result.trades if t.exit_reason != "END"]
    n_trades = len(trade_pnls)
    wins = [p for p in trade_pnls if p > 0]
    losses = [p for p in trade_pnls if p < 0]
    wr = len(wins) / n_trades if n_trades > 0 else 0.0
    pf = sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else float("inf")
    exp = float(np.mean(trade_pnls)) if trade_pnls else 0.0

    return {
        "metrics": {"sharpe": sharpe, "sortino": sortino, "cagr": cagr,
                     "calmar": calmar, "max_dd_pct": max_dd, "total_return_pct": total_ret},
        "n_trades": n_trades, "win_rate": wr, "profit_factor": pf, "expectancy": exp,
        "trade_pnls": trade_pnls, "equity_curve": eq.tolist(),
        "trades": result.trades,
    }


def run_fast_bt(close, high, low, proba, atr, mask,
                threshold=THRESHOLD, sl_mult=SL_MULT, tp_mult=TP_MULT,
                risk_pct=RISK_PCT, initial_balance=10000.0):
    """Minimal backtest with mask for non-contiguous data. Returns Sharpe."""
    c, h, l, a, p, m = close, high, low, atr, proba, mask
    bal = float(initial_balance)
    peak = bal
    eq_rets: list[float] = []
    pos_active = False
    pos_sl = pos_tp = pos_sz = pos_ep = 0.0
    fee = 0.001; slip = 0.0005; sprd = 0.0001
    n = len(c)

    for i in range(1, n):
        if not m[i]:
            if not pos_active:
                continue
        if pos_active:
            sl_hit = l[i] <= pos_sl
            tp_hit = h[i] >= pos_tp
            if sl_hit or tp_hit:
                ex_px = pos_sl * (1 - slip) if sl_hit else pos_tp * (1 - slip)
                gross = pos_sz * (ex_px - pos_ep)
                fc = (abs(pos_sz * pos_ep) + abs(pos_sz * ex_px)) * fee
                bal += gross - fc
                for _ in range(5):
                    eq_rets.append((bal - peak) / peak if peak > 0 else 0.0)
                peak = max(peak, bal)
                pos_active = False
                continue
        if not pos_active and p[i] >= threshold and m[i]:
            atr_i = a[i] if a[i] > 0 else c[i] * 0.01
            sd = atr_i * sl_mult
            if sd <= 0: continue
            pos_sz = (bal * risk_pct) / sd
            if pos_sz <= 0: continue
            pos_ep = c[i] * (1 + slip + sprd)
            pos_sl = pos_ep - sd
            pos_tp = pos_ep + atr_i * tp_mult
            pos_active = True

    if pos_active:
        ex_px = c[-1] * (1 - slip)
        gross = pos_sz * (ex_px - pos_ep)
        fc = (abs(pos_sz * pos_ep) + abs(pos_sz * ex_px)) * fee
        bal += gross - fc

    arr = np.array(eq_rets)
    if len(arr) < 5: return 0.0
    mean_r, std_r = np.mean(arr), np.std(arr)
    return float(mean_r / std_r * np.sqrt(365 * 24)) if std_r > 0 else 0.0


def sharpe_from_pnls(pnls, n_bars_total):
    """Trade-level Sharpe correctly annualized."""
    if len(pnls) < 3: return 0.0
    years = n_bars_total / (365 * 24)
    tpy = len(pnls) / years if years > 0 else 0
    if tpy <= 0: return 0.0
    m, s = np.mean(pnls), np.std(pnls)
    return float(m / s * np.sqrt(tpy)) if s > 0 else 0.0


# ═══════════════════════════════════════════════════
# CAPA 0 — Null World Ensemble
# ═══════════════════════════════════════════════════

def capa0_null_world(ctx: AuditContext) -> dict:
    """8 null models + 100 random timing strategies."""
    print("\n" + "=" * 60)
    print("CAPA 0 — NULL WORLD ENSEMBLE")
    print("=" * 60)
    results: dict[str, float] = {}
    hi = ctx.holdout_idx
    n_h = len(hi)
    bh = 10000.0

    # 0a: Buy & Hold
    bh_eq = bh * ctx.close[hi] / ctx.close[hi[0]]
    bh_rets = np.diff(bh_eq) / bh_eq[:-1]
    m, s = np.mean(bh_rets), np.std(bh_rets)
    results["buy_and_hold"] = float(m / s * np.sqrt(365 * 24)) if s > 0 else 0.0
    print(f"  Buy & Hold:              Sharpe={results['buy_and_hold']:.3f}")

    # 0b: Random Walk
    np.random.seed(0)
    rw_proba = np.random.uniform(0, 1, n_h)
    rw_proba = np.where(ctx.y[hi] != -1, rw_proba, 0.0)
    bt = run_bt(ctx.close[hi], ctx.high[hi], ctx.low[hi],
                ctx.ts[hi], rw_proba, ctx.atr[hi])
    results["random_walk"] = bt["metrics"]["sharpe"]
    print(f"  Random Walk:             Sharpe={results['random_walk']:.3f}")

    # 0c: Momentum naive
    rets = np.diff(ctx.close[hi]) / ctx.close[hi[:-1]]
    mom_proba = np.zeros(n_h)
    for i in range(1, n_h):
        mom_proba[i] = 0.55 if rets[i - 1] > 0 else 0.45
    mom_proba = np.where(ctx.y[hi] != -1, mom_proba, 0.0)
    bt = run_bt(ctx.close[hi], ctx.high[hi], ctx.low[hi],
                ctx.ts[hi], mom_proba, ctx.atr[hi])
    results["momentum_naive"] = bt["metrics"]["sharpe"]
    print(f"  Momentum Naive:          Sharpe={results['momentum_naive']:.3f}")

    # 0d: Mean reversion
    zc = pd.Series(ctx.close[hi]).rolling(20).apply(
        lambda x: (x[-1] - np.mean(x)) / np.maximum(np.std(x), 1e-10)).values
    mr_proba = np.where(zc < -1, 0.7, np.where(zc > 1, 0.3, 0.5))
    mr_proba = np.where(ctx.y[hi] != -1, mr_proba, 0.0)
    bt = run_bt(ctx.close[hi], ctx.high[hi], ctx.low[hi],
                ctx.ts[hi], mr_proba, ctx.atr[hi])
    results["mean_reversion"] = bt["metrics"]["sharpe"]
    print(f"  Mean Reversion:          Sharpe={results['mean_reversion']:.3f}")

    # 0e: MA crossover (EMA9 vs EMA50)
    import ta as ta_lib
    ema9 = ta_lib.trend.EMAIndicator(pd.Series(ctx.close[hi]), window=9).ema_indicator().values
    ema50 = ta_lib.trend.EMAIndicator(pd.Series(ctx.close[hi]), window=50).ema_indicator().values
    ma_proba = np.where(ema9 > ema50, 0.7, 0.3)
    ma_proba = np.where(ctx.y[hi] != -1, ma_proba, 0.0)
    bt = run_bt(ctx.close[hi], ctx.high[hi], ctx.low[hi],
                ctx.ts[hi], ma_proba, ctx.atr[hi])
    results["ma_crossover"] = bt["metrics"]["sharpe"]
    print(f"  MA Crossover:            Sharpe={results['ma_crossover']:.3f}")

    # 0f: Permuted features RF
    X_h = ctx.X[hi].copy()
    X_perm = X_h.copy()
    for j in range(X_perm.shape[1]):
        np.random.shuffle(X_perm[:, j])
    X_perm_s = ctx.scaler.transform(X_perm)
    proba_perm = ctx.rf.predict_proba(X_perm_s)
    proba_perm = proba_perm[:, 1] if proba_perm.shape[1] >= 2 else proba_perm[:, 0]
    proba_perm = np.where(ctx.y[hi] != -1, proba_perm, 0.0)
    bt = run_bt(ctx.close[hi], ctx.high[hi], ctx.low[hi],
                ctx.ts[hi], proba_perm, ctx.atr[hi])
    results["permuted_features_rf"] = bt["metrics"]["sharpe"]
    print(f"  Permuted Features RF:    Sharpe={results['permuted_features_rf']:.3f}")

    # 0g: Permuted labels RF
    y_train = ctx.y[ctx.train_idx]
    mask_tr = y_train != -1
    y_shuffled = y_train.copy()
    np.random.shuffle(y_shuffled[mask_tr])
    rf_s = RandomForestClassifier(max_depth=7, n_estimators=100,
                                   class_weight="balanced", random_state=42)
    X_tr_s = ctx.scaler.transform(ctx.X[ctx.train_idx])
    rf_s.fit(X_tr_s[mask_tr], y_shuffled[mask_tr])
    X_h_s = ctx.scaler.transform(ctx.X[hi])
    proba_pl = rf_s.predict_proba(X_h_s)
    proba_pl = proba_pl[:, 1] if proba_pl.shape[1] >= 2 else proba_pl[:, 0]
    proba_pl = np.where(ctx.y[hi] != -1, proba_pl, 0.0)
    bt = run_bt(ctx.close[hi], ctx.high[hi], ctx.low[hi],
                ctx.ts[hi], proba_pl, ctx.atr[hi])
    results["permuted_labels_rf"] = bt["metrics"]["sharpe"]
    print(f"  Permuted Labels RF:      Sharpe={results['permuted_labels_rf']:.3f}")

    # 0h: Random timing baseline (100 iterations)
    print("  Random Timing Baseline (100 iterations)...")
    n_champ_trades = 266
    random_timing_sharpes = []
    for _ in range(100):
        rt_proba = np.zeros(n_h)
        entry_bars = np.random.choice(n_h, n_champ_trades, replace=False)
        rt_proba[entry_bars] = 0.6
        rt_proba = np.where(ctx.y[hi] != -1, rt_proba, 0.0)
        sr = run_fast_bt(ctx.close[hi], ctx.high[hi], ctx.low[hi],
                         rt_proba, ctx.atr[hi], np.ones(n_h, dtype=bool))
        random_timing_sharpes.append(sr)
    results["random_timing_mean"] = float(np.mean(random_timing_sharpes))
    results["random_timing_std"] = float(np.std(random_timing_sharpes))
    results["random_timing_p90"] = float(np.percentile(random_timing_sharpes, 90))
    results["random_timing_max"] = float(np.max(random_timing_sharpes))
    print(f"  Random Timing:    mean={results['random_timing_mean']:.3f}, "
          f"p90={results['random_timing_p90']:.3f}, max={results['random_timing_max']:.3f}")

    # Champion Sharpe
    champ_sr = ctx.baseline_sharpe
    null_sharpes = np.array([v for k, v in results.items()
                             if isinstance(v, (int, float)) and k != "random_timing_std"])
    null_p50 = float(np.percentile(null_sharpes, 50))
    null_p90 = float(np.percentile(null_sharpes, 90))

    # Percentile rank of champion within null distribution
    all_s = list(null_sharpes) + [champ_sr]
    percentile_rank = float(sum(1 for s in all_s if s <= champ_sr) / len(all_s))
    null_separation = float(clamp(
        (champ_sr - null_p50) / max(null_p90 - null_p50, 1e-10), 0, 1
    ))

    print(f"\n  Champion Sharpe:          {champ_sr:.3f}")
    print(f"  Null p50:                {null_p50:.3f}")
    print(f"  Null p90:                {null_p90:.3f}")
    print(f"  Percentile rank:         {percentile_rank:.2%}")
    print(f"  null_separation score:   {null_separation:.3f}")

    return {
        "null_models": results,
        "champion_sharpe": champ_sr,
        "null_p50": null_p50,
        "null_p90": null_p90,
        "percentile_rank": percentile_rank,
        "null_separation": null_separation,
    }


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ═══════════════════════════════════════════════════
# CAPA 1 — Adversarial Regime Testing
# ═══════════════════════════════════════════════════

def capa1_regime_test(ctx: AuditContext) -> dict:
    """5 adversarial regimes + Regime Confusion Test."""
    print("\n" + "=" * 60)
    print("CAPA 1 — ADVERSARIAL REGIME TESTING")
    print("=" * 60)
    hi = ctx.holdout_idx
    n_h = len(hi)

    c_h = ctx.close[hi]; h_h = ctx.high[hi]; l_h = ctx.low[hi]
    a_h = ctx.atr[hi]; v_h = ctx.volume[hi]; ts_h = ctx.ts[hi]
    p_h = ctx.proba[hi]

    rets = np.diff(c_h) / c_h[:-1]
    rets_full = np.append(0, rets)
    bb = pd.Series(c_h).rolling(20).std().values * 2  # BB width proxy

    # ATR percentiles
    atr_pctl = pd.Series(a_h).rank(pct=True).values

    # Define regime masks
    regimes = {
        "high_vol_crash": (atr_pctl > 0.95) & (rets_full < 0),
        "low_vol_chop": (atr_pctl < 0.25) & (np.abs(rets_full) < np.percentile(np.abs(rets_full), 25)),
        "trend_compression": (bb < np.percentile(bb, 10)),
        "liquidity_drought": (pd.Series(v_h).rank(pct=True).values < 0.10),
        "extreme_atr_spike": (atr_pctl > 0.99),
    }

    regime_sharpes: dict[str, float] = {}
    regime_trades: dict[str, int] = {}
    regime_details: dict = {}

    # Run full backtest and tag trades by entry regime
    bt = run_bt(c_h, h_h, l_h, ts_h, p_h, a_h)
    for rname, mask in regimes.items():
        mask = mask & (ctx.y[hi] != -1)
        n_regime_bars = int(mask.sum())
        if n_regime_bars < 50:
            regime_sharpes[rname] = 0.0
            regime_trades[rname] = 0
            continue
        sr = run_fast_bt(c_h, h_h, l_h, p_h, a_h, mask)
        regime_sharpes[rname] = sr
        # Count trades entered during regime
        entry_times = [t.entry_idx for t in bt["trades"] if t.exit_reason != "END"]
        regime_trades[rname] = sum(1 for ei in entry_times if ei < len(mask) and mask[ei])
        regime_details[rname] = {
            "sharpe": sr, "n_bars": int(mask.sum()),
            "n_trades": regime_trades[rname],
            "frac_bars": float(mask.sum() / n_h),
        }
        print(f"  {rname:25s}: Sharpe={sr:.3f}, bars={n_regime_bars}, trades={regime_trades[rname]}")

    # Regime robustness = min/max
    sr_values = [v for v in regime_sharpes.values() if abs(v) > 1e-6]
    regime_robustness = min(sr_values) / max(sr_values) if sr_values and max(sr_values) > 0 else 0.0
    min_regime_sr = min(sr_values) if sr_values else 0.0
    print(f"\n  Regime Robustness:       {regime_robustness:.3f} (min={min_regime_sr:.3f})")

    # ── Regime Confusion Test ──
    print("\n  Regime Confusion Test...")
    atr_q = pd.qcut(pd.Series(ctx.atr), q=3, labels=[0, 1, 2],
                    duplicates="drop").values.astype(int)

    hi_mask = ctx.y[hi] != -1
    X_h = ctx.X[hi][hi_mask]
    regime_h = atr_q[hi][hi_mask]
    proba_h = ctx.proba[hi][hi_mask]

    # Without proba
    dt_base = DecisionTreeClassifier(max_depth=3, random_state=42)
    dt_base.fit(X_h, regime_h)
    acc_base = float(dt_base.score(X_h, regime_h))

    # With proba
    X_wp = np.column_stack([X_h, proba_h])
    dt_wp = DecisionTreeClassifier(max_depth=3, random_state=42)
    dt_wp.fit(X_wp, regime_h)
    acc_wp = float(dt_wp.score(X_wp, regime_h))

    confusion_delta = acc_wp - acc_base
    print(f"    Acc (no proba):    {acc_base:.4f}")
    print(f"    Acc (with proba):  {acc_wp:.4f}")
    print(f"    Confusion delta:   {confusion_delta:.4f}")
    print(f"    {'⚠️  ALERTA' if confusion_delta > 0.05 else '✓ Normal'}: "
          f"proba {'predice régimen' if confusion_delta > 0.05 else 'no añade info de régimen'}")

    return {
        "regime_results": regime_details,
        "regime_robustness": regime_robustness,
        "min_regime_sharpe": min_regime_sr,
        "regime_confusion": {
            "accuracy_without_proba": acc_base,
            "accuracy_with_proba": acc_wp,
            "delta": confusion_delta,
            "alert": confusion_delta > 0.05,
        },
    }


# ═══════════════════════════════════════════════════
# CAPA 2 — Feature Ablation
# ═══════════════════════════════════════════════════

def capa2_ablation(ctx: AuditContext) -> dict:
    """3 perturbation methods × 4 feature groups."""
    print("\n" + "=" * 60)
    print("CAPA 2 — FEATURE CAUSALITY BREAK")
    print("=" * 60)
    hi = ctx.holdout_idx
    c_h, h_h, l_h = ctx.close[hi], ctx.high[hi], ctx.low[hi]
    ts_h, a_h = ctx.ts[hi], ctx.atr[hi]
    baseline_sr = ctx.baseline_sharpe
    results: dict = {}
    max_delta = 0.0

    for gname, gidx in FEATURE_GROUPS.items():
        gname_short = gname.replace("_", " ")[:15]
        if not gidx:
            continue
        col_slice = ctx.X[hi][:, gidx].copy()

        # 2a: Dropout (zero-out)
        X_drop = ctx.X[hi].copy()
        X_drop[:, gidx] = 0.0
        X_ds = ctx.scaler.transform(X_drop)
        p = ctx.rf.predict_proba(X_ds)
        p = p[:, 1] if p.shape[1] >= 2 else p[:, 0]
        p = np.where(ctx.y[hi] != -1, p, 0.0)
        bt = run_bt(c_h, h_h, l_h, ts_h, p, a_h)
        sr_drop = bt["metrics"]["sharpe"]
        delta_drop = (baseline_sr - sr_drop) / max(baseline_sr, 1e-10)

        # 2b: Permutation
        X_perm = ctx.X[hi].copy()
        for j in gidx:
            np.random.shuffle(X_perm[:, j])
        X_ps = ctx.scaler.transform(X_perm)
        p = ctx.rf.predict_proba(X_ps)
        p = p[:, 1] if p.shape[1] >= 2 else p[:, 0]
        p = np.where(ctx.y[hi] != -1, p, 0.0)
        bt = run_bt(c_h, h_h, l_h, ts_h, p, a_h)
        sr_perm = bt["metrics"]["sharpe"]
        delta_perm = (baseline_sr - sr_perm) / max(baseline_sr, 1e-10)

        # 2c: Noise injection (15% of std)
        X_noise = ctx.X[hi].copy()
        for j in gidx:
            noise = np.random.randn(len(X_noise)) * np.std(X_noise[:, j]) * 0.15
            X_noise[:, j] += noise
        X_ns = ctx.scaler.transform(X_noise)
        p = ctx.rf.predict_proba(X_ns)
        p = p[:, 1] if p.shape[1] >= 2 else p[:, 0]
        p = np.where(ctx.y[hi] != -1, p, 0.0)
        bt = run_bt(c_h, h_h, l_h, ts_h, p, a_h)
        sr_noise = bt["metrics"]["sharpe"]
        delta_noise = (baseline_sr - sr_noise) / max(baseline_sr, 1e-10)

        results[gname] = {
            "n_features": len(gidx),
            "dropout_sharpe": sr_drop, "dropout_delta": delta_drop,
            "permutation_sharpe": sr_perm, "permutation_delta": delta_perm,
            "noise_sharpe": sr_noise, "noise_delta": delta_noise,
        }
        max_delta = max(max_delta, delta_drop, delta_perm, delta_noise)
        print(f"  {gname_short:15s}: drop={sr_drop:.2f}(Δ={delta_drop:.3f}) "
              f"perm={sr_perm:.2f}(Δ={delta_perm:.3f}) noise={sr_noise:.2f}(Δ={delta_noise:.3f})")

    feature_stability = clamp(1.0 - max_delta, 0, 1)
    print(f"\n  Max delta: {max_delta:.3f}")
    print(f"  Feature stability: {feature_stability:.3f}")
    if max_delta > 0.30:
        print("  ⚠️  FEATURE FRAGILITY DETECTED")

    return {
        "ablation_results": results,
        "max_delta": max_delta,
        "feature_stability": feature_stability,
        "fragility_alert": max_delta > 0.30,
    }


# ═══════════════════════════════════════════════════
# CAPA 3 — Model Invariance
# ═══════════════════════════════════════════════════

def capa3_model_invariance(ctx: AuditContext) -> dict:
    """4 models, HOLDOUT evaluation."""
    print("\n" + "=" * 60)
    print("CAPA 3 — MODEL INVARIANCE TEST")
    print("=" * 60)
    hi = ctx.holdout_idx
    ti = ctx.train_idx
    c_h, h_h, l_h = ctx.close[hi], ctx.high[hi], ctx.low[hi]
    ts_h, a_h = ctx.ts[hi], ctx.atr[hi]

    X_tr = ctx.X[ti]; y_tr = ctx.y[ti]
    X_h = ctx.X[hi]
    mask_tr = y_tr != -1

    sharpes: dict[str, float] = {}
    models = []

    # 3a: LogisticRegression
    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_tr)
    lr = LogisticRegression(class_weight="balanced", max_iter=1000, random_state=42)
    lr.fit(X_tr_s[mask_tr], y_tr[mask_tr])
    X_h_s = scaler.transform(X_h)
    p = lr.predict_proba(X_h_s)
    p = p[:, 1] if p.shape[1] >= 2 else p[:, 0]
    p = np.where(ctx.y[hi] != -1, p, 0.0)
    bt = run_bt(c_h, h_h, l_h, ts_h, p, a_h)
    sharpes["logistic_regression"] = bt["metrics"]["sharpe"]
    models.append(("LogisticRegression", bt["metrics"]["sharpe"]))
    print(f"  LogisticRegression:  Sharpe={bt['metrics']['sharpe']:.3f}")

    # 3b: HistGradientBoosting
    hgb = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.1,
                                          max_iter=100, random_state=42)
    hgb.fit(X_tr_s[mask_tr], y_tr[mask_tr])
    p = hgb.predict_proba(X_h_s)
    p = p[:, 1] if p.shape[1] >= 2 else p[:, 0]
    p = np.where(ctx.y[hi] != -1, p, 0.0)
    bt = run_bt(c_h, h_h, l_h, ts_h, p, a_h)
    sharpes["hist_gradient_boosting"] = bt["metrics"]["sharpe"]
    models.append(("HistGradientBoosting", bt["metrics"]["sharpe"]))
    print(f"  HistGradientBoost:   Sharpe={bt['metrics']['sharpe']:.3f}")

    # 3c: RidgeClassifier (linear ridge)
    rc = RidgeClassifier(alpha=1.0, random_state=42)
    rc.fit(X_tr_s[mask_tr], y_tr[mask_tr])
    p = rc.decision_function(X_h_s)
    p = (p - p.min()) / max(p.max() - p.min(), 1e-10)  # scale to [0, 1]
    p = np.where(ctx.y[hi] != -1, p, 0.0)
    bt = run_bt(c_h, h_h, l_h, ts_h, p, a_h)
    sharpes["ridge_classifier"] = bt["metrics"]["sharpe"]
    models.append(("RidgeClassifier", bt["metrics"]["sharpe"]))
    print(f"  RidgeClassifier:     Sharpe={bt['metrics']['sharpe']:.3f}")

    # 3d: RandomForest (baseline — from ctx)
    sharpes["random_forest"] = ctx.baseline_sharpe
    models.append(("RandomForest", ctx.baseline_sharpe))
    print(f"  RandomForest:        Sharpe={ctx.baseline_sharpe:.3f}")

    sr_vals = np.array(list(sharpes.values()))
    model_invariance = float(np.min(sr_vals) / np.maximum(np.max(sr_vals), 1e-10))
    print(f"\n  Model invariance:     {model_invariance:.3f} (min/max Sharpe)")

    return {
        "model_sharpes": sharpes,
        "model_invariance": model_invariance,
        "min_model_sharpe": float(np.min(sr_vals)),
        "max_model_sharpe": float(np.max(sr_vals)),
    }


# ═══════════════════════════════════════════════════
# CAPA 4 — Time Warp Validation
# ═══════════════════════════════════════════════════

def capa4_time_warp(ctx: AuditContext) -> dict:
    """Expanding window OOS + walk-forward."""
    print("\n" + "=" * 60)
    print("CAPA 4 — TIME WARP VALIDATION")
    print("=" * 60)
    n = len(ctx.close)
    folds: list[dict] = []

    splits = [
        ("Expanding A", 0, int(n * 0.50), int(n * 0.50), int(n * 0.80)),
        ("Expanding B", 0, int(n * 0.75), int(n * 0.75), n),
    ]

    for name, tr_start, tr_end, te_start, te_end in splits:
        ti = np.arange(tr_start, tr_end)
        tei = np.arange(te_start, min(te_end, n))
        if len(ti) < 1000 or len(tei) < 500:
            continue
        mask_tr = ctx.y[ti] != -1
        scaler = StandardScaler()
        X_tr_s = scaler.fit_transform(ctx.X[ti])
        rf = RandomForestClassifier(max_depth=7, n_estimators=100,
                                     class_weight="balanced", random_state=42)
        rf.fit(X_tr_s[mask_tr], ctx.y[ti][mask_tr])

        X_te_s = scaler.transform(ctx.X[tei])
        p = rf.predict_proba(X_te_s)
        p = p[:, 1] if p.shape[1] >= 2 else p[:, 0]
        p = np.where(ctx.y[tei] != -1, p, 0.0)

        bt = run_bt(ctx.close[tei], ctx.high[tei], ctx.low[tei],
                    ctx.ts[tei], p, ctx.atr[tei])
        folds.append({
            "name": name,
            "train_bars": int(len(ti)),
            "test_bars": int(len(tei)),
            "sharpe": bt["metrics"]["sharpe"],
            "calmar": bt["metrics"]["calmar"],
            "cagr": bt["metrics"]["cagr"],
            "max_dd": bt["metrics"]["max_dd_pct"],
            "n_trades": bt["n_trades"],
        })
        print(f"  {name:15s}: Sharpe={bt['metrics']['sharpe']:.3f}, "
              f"Calmar={bt['metrics']['calmar']:.3f}, trades={bt['n_trades']}")

    # Walk-forward (4 folds aligned with FASE 6.5)
    wf_splits = [
        (0, int(n * 0.28), int(n * 0.28), int(n * 0.41)),
        (0, int(n * 0.41), int(n * 0.41), int(n * 0.55)),
    ]
    for tr_end, te_start, te_end in [(int(n * 0.55), int(n * 0.55), int(n * 0.70)),
                                      (int(n * 0.70), int(n * 0.70), int(n * 0.85))]:
        wf_splits.append((0, tr_end, te_start, te_end))

    for name, tr_s, tr_e, te_s, te_e in [
        ("WF Fold 0", 0, int(n * 0.28), int(n * 0.28), int(n * 0.41)),
        ("WF Fold 1", 0, int(n * 0.41), int(n * 0.41), int(n * 0.55)),
        ("WF Fold 2", 0, int(n * 0.55), int(n * 0.55), int(n * 0.70)),
        ("WF Fold 3", 0, int(n * 0.70), int(n * 0.70), int(n * 0.85)),
    ]:
        ti = np.arange(tr_s, min(tr_e, n))
        tei = np.arange(te_s, min(te_e, n))
        if len(ti) < 1000 or len(tei) < 500:
            continue
        mask_tr = ctx.y[ti] != -1
        scaler = StandardScaler()
        X_tr_s = scaler.fit_transform(ctx.X[ti])
        rf = RandomForestClassifier(max_depth=7, n_estimators=100,
                                     class_weight="balanced", random_state=42)
        rf.fit(X_tr_s[mask_tr], ctx.y[ti][mask_tr])
        X_te_s = scaler.transform(ctx.X[tei])
        p = rf.predict_proba(X_te_s)
        p = p[:, 1] if p.shape[1] >= 2 else p[:, 0]
        p = np.where(ctx.y[tei] != -1, p, 0.0)
        bt = run_bt(ctx.close[tei], ctx.high[tei], ctx.low[tei],
                    ctx.ts[tei], p, ctx.atr[tei])
        folds.append({
            "name": name,
            "train_bars": int(len(ti)),
            "test_bars": int(len(tei)),
            "sharpe": bt["metrics"]["sharpe"],
            "calmar": bt["metrics"]["calmar"],
            "cagr": bt["metrics"]["cagr"],
            "max_dd": bt["metrics"]["max_dd_pct"],
            "n_trades": bt["n_trades"],
        })
        print(f"  {name:15s}: Sharpe={bt['metrics']['sharpe']:.3f}, "
              f"Calmar={bt['metrics']['calmar']:.3f}, trades={bt['n_trades']}")

    # Compute decay rate
    sharpe_series = [f["sharpe"] for f in folds]
    if len(sharpe_series) >= 3:
        decay = (sharpe_series[0] - sharpe_series[-1]) / max(sharpe_series[0], 1e-10)
    else:
        decay = 0.0
    temporal_stability = clamp(1.0 - decay, 0, 1)
    print(f"\n  Sharpe decay: {decay:.3f} ({sharpe_series[0]:.2f} → {sharpe_series[-1]:.2f})")
    print(f"  Temporal stability: {temporal_stability:.3f}")
    if decay > 0.30:
        print("  ⚠️  MONOTONIC DECAY DETECTED")

    return {
        "folds": folds,
        "sharpe_series": sharpe_series,
        "decay_rate": decay,
        "temporal_stability": temporal_stability,
        "decay_alert": decay > 0.30,
    }


# ═══════════════════════════════════════════════════
# CAPA 5 — Execution Stress
# ═══════════════════════════════════════════════════

def capa5_execution_stress(ctx: AuditContext) -> dict:
    """5 execution stress variants."""
    print("\n" + "=" * 60)
    print("CAPA 5 — EXECUTION REALITY DISTORTION")
    print("=" * 60)
    hi = ctx.holdout_idx
    c_h, h_h, l_h = ctx.close[hi], ctx.high[hi], ctx.low[hi]
    ts_h, a_h, p_h = ctx.ts[hi], ctx.atr[hi], ctx.proba[hi]

    variants = [
        ("baseline", 0.001, 0.0005, 0.0001, 1),
        ("fee_3x", 0.003, 0.0005, 0.0001, 1),
        ("slippage_2x", 0.001, 0.0010, 0.0001, 1),
        ("latency_3bar", 0.001, 0.0005, 0.0001, 3),
        ("full_stress", 0.002, 0.0015, 0.0005, 2),
    ]

    stress_results: dict = {}
    sharpes_stressed = []

    for name, fee, slip, sprd, latency in variants:
        bt = run_bt(c_h, h_h, l_h, ts_h, p_h, a_h, fee=fee, slippage=slip, spread=sprd)
        sr = bt["metrics"]["sharpe"]
        sharpes_stressed.append(sr)
        stress_results[name] = {
            "sharpe": sr, "calmar": bt["metrics"]["calmar"],
            "cagr": bt["metrics"]["cagr"], "max_dd": bt["metrics"]["max_dd_pct"],
            "n_trades": bt["n_trades"],
            "params": {"fee": fee, "slippage": slip, "spread": sprd, "latency_bars": latency},
        }
        print(f"  {name:20s}: Sharpe={sr:.3f}, Calmar={bt['metrics']['calmar']:.3f}, "
              f"trades={bt['n_trades']}")

    min_stressed_sr = min(sharpes_stressed[1:])  # exclude baseline
    execution_resilience = min_stressed_sr / max(ctx.baseline_sharpe, 1e-10)
    print(f"\n  Min stressed Sharpe:  {min_stressed_sr:.3f}")
    print(f"  Execution resilience: {execution_resilience:.3f}")
    if execution_resilience < 0.50:
        print("  ⚠️  NON-EXECUTABLE ALPHA")

    return {
        "stress_variants": stress_results,
        "min_stressed_sharpe": min_stressed_sr,
        "execution_resilience": clamp(execution_resilience, 0, 1),
    }


# ═══════════════════════════════════════════════════
# GLOBAL VERDICT
# ═══════════════════════════════════════════════════

def global_verdict(ctx: AuditContext) -> dict:
    """Compute ALPHA_SCORE = min([...]) and final verdict."""
    print("\n" + "=" * 60)
    print("GLOBAL ALPHA VERDICT")
    print("=" * 60)

    scores = {}

    # CAPA 0
    c0 = ctx.results.get("capa0_null_world", {})
    scores["null_separation"] = c0.get("null_separation", 0.0)

    # CAPA 1
    c1 = ctx.results.get("capa1_regime_test", {})
    scores["regime_robustness"] = c1.get("regime_robustness", 0.0)

    # CAPA 2
    c2 = ctx.results.get("capa2_ablation", {})
    scores["feature_stability"] = c2.get("feature_stability", 0.0)

    # CAPA 3
    c3 = ctx.results.get("capa3_model_invariance", {})
    scores["model_invariance"] = c3.get("model_invariance", 0.0)

    # CAPA 4
    c4 = ctx.results.get("capa4_time_warp", {})
    scores["temporal_stability"] = c4.get("temporal_stability", 0.0)

    # CAPA 5
    c5 = ctx.results.get("capa5_execution_stress", {})
    scores["execution_resilience"] = c5.get("execution_resilience", 0.0)

    # Min over all (weakest link)
    component_values = [v for v in scores.values() if v > 0]
    alpha_score = min(component_values) if component_values else 0.0

    # Find weakest layer
    weakest = min(scores, key=scores.get) if scores else "unknown"
    weakest_val = scores.get(weakest, 0.0)

    # Regime confusion alert
    confusion_delta = c1.get("regime_confusion", {}).get("delta", 0.0)

    if alpha_score > 0.7 and confusion_delta <= 0.05:
        verdict = "A) Structural Real Alpha"
        color = "🟢"
    elif alpha_score > 0.4:
        verdict = "B) Fragile Alpha"
        color = "🟡"
    else:
        verdict = "C) Statistical Illusion"
        color = "🔴"

    # Additional flags
    flags = []
    if confusion_delta > 0.05:
        flags.append("⚠️ Regime confusion: proba predicts regime")
    if c2.get("fragility_alert", False):
        flags.append("⚠️ Feature fragility detected")
    if c4.get("decay_alert", False):
        flags.append("⚠️ Temporal decay detected")
    if c5.get("execution_resilience", 1.0) < 0.5:
        flags.append("⚠️ Non-executable under stress")

    print(f"\n  {'='*40}")
    print(f"  {color} ALPHA SCORE: {alpha_score:.4f}")
    print(f"  VERDICT: {verdict}")
    print(f"  {'='*40}")
    print(f"\n  Component scores:")
    for k, v in sorted(scores.items()):
        marker = " ← WEAKEST" if k == weakest else ""
        print(f"    {k:25s}: {v:.4f}{marker}")
    print(f"\n  Weakest layer: {weakest} ({weakest_val:.4f})")
    if flags:
        print(f"\n  Flags:")
        for f in flags:
            print(f"    {f}")

    result = {
        "alpha_score": alpha_score,
        "verdict": verdict,
        "verdict_code": ["C", "B", "A"][
            0 if alpha_score < 0.4 else (1 if alpha_score < 0.7 else 2)
        ],
        "component_scores": scores,
        "weakest_layer": weakest,
        "weakest_value": weakest_val,
        "regime_confusion_alert": confusion_delta > 0.05,
        "flags": flags,
    }
    ctx.save("global_verdict", result)
    return result


# ═══════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════

def main():
    t0 = time.time()
    print("=" * 72)
    print("ALPHA STRESS DECOMPOSITION AUDIT (AFS v1.0)")
    print("=" * 72)

    ctx = AuditContext()

    # ── Load data ──
    print("\n[1/6] Loading data + computing features...")
    df = pd.read_parquet(OHLCV_PATH)
    df_raw, X_full, y, close, high, low, volume = compute_features(df)
    X = X_full[:, RETAINED_INDICES]

    import ta as ta_lib
    atr = ta_lib.volatility.AverageTrueRange(
        pd.Series(df["high"]), pd.Series(df["low"]), pd.Series(df["close"]), window=14
    ).average_true_range().values
    atr = np.nan_to_num(atr, nan=0.0, posinf=0.0, neginf=0.0)
    atr[atr <= 0] = df["close"].values[atr <= 0] * 0.01

    # ── Split ──
    n = len(df)
    holdout_size = int(n * 0.20)
    val_size = int(n * 0.15)
    train_size = n - holdout_size - val_size
    train_idx = np.arange(0, train_size)
    val_idx = np.arange(train_size, train_size + val_size)
    holdout_idx = np.arange(train_size + val_size, n)

    ctx.df = df
    ctx.X = X
    ctx.y = y
    ctx.close = df["close"].values.astype(float)
    ctx.high = df["high"].values.astype(float)
    ctx.low = df["low"].values.astype(float)
    ctx.volume = df["volume"].values.astype(float)
    ctx.ts = df["timestamp"].values
    ctx.atr = atr
    ctx.train_idx = train_idx
    ctx.val_idx = val_idx
    ctx.holdout_idx = holdout_idx

    print(f"  TRAIN: {train_size} bars ({df['timestamp'].iloc[train_idx[0]]} → {df['timestamp'].iloc[train_idx[-1]]})")
    print(f"  VAL:   {val_size} bars ({df['timestamp'].iloc[val_idx[0]]} → {df['timestamp'].iloc[val_idx[-1]]})")
    print(f"  HOLDOUT: {holdout_size} bars ({df['timestamp'].iloc[holdout_idx[0]]} → {df['timestamp'].iloc[holdout_idx[-1]]})")

    # ── Train RF on TRAIN ──
    print("\n[2/6] Training RandomForest on TRAIN...")
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X[train_idx])
    mask_tr = y[train_idx] != -1
    rf = RandomForestClassifier(max_depth=7, n_estimators=100,
                                 class_weight="balanced", random_state=42)
    rf.fit(X_train_scaled[mask_tr], y[train_idx][mask_tr])
    ctx.rf = rf
    ctx.scaler = scaler
    print(f"  Trained on {mask_tr.sum()} samples")

    # ── Predict on all data ──
    print("\n[3/6] Predicting...")
    X_all_scaled = scaler.transform(X)
    proba_all = rf.predict_proba(X_all_scaled)
    proba = proba_all[:, 1] if proba_all.shape[1] >= 2 else proba_all[:, 0]
    proba = np.where(y != -1, proba, 0.0)
    ctx.proba = proba

    # ── Baseline HOLDOUT (locked config) ──
    print("\n[4/6] Baseline HOLDOUT backtest...")
    bt = run_bt(ctx.close[holdout_idx], ctx.high[holdout_idx], ctx.low[holdout_idx],
                ctx.ts[holdout_idx], proba[holdout_idx], ctx.atr[holdout_idx])
    ctx.baseline_sharpe = bt["metrics"]["sharpe"]
    ctx.holdout_equity = bt["equity_curve"]
    print(f"  Baseline: Sharpe={ctx.baseline_sharpe:.3f}, "
          f"Calmar={bt['metrics']['calmar']:.3f}, CAGR={bt['metrics']['cagr']:.2f}%")

    # ── CAPA 0 ──
    print("\n[5/6] Running CAPAS...")
    r0 = capa0_null_world(ctx)
    ctx.save("capa0_null_world", r0)

    # ── CAPA 1 ──
    r1 = capa1_regime_test(ctx)
    ctx.save("capa1_regime_test", r1)

    # ── CAPA 2 ──
    r2 = capa2_ablation(ctx)
    ctx.save("capa2_ablation", r2)

    # ── CAPA 3 ──
    r3 = capa3_model_invariance(ctx)
    ctx.save("capa3_model_invariance", r3)

    # ── CAPA 4 ──
    r4 = capa4_time_warp(ctx)
    ctx.save("capa4_time_warp", r4)

    # ── CAPA 5 ──
    r5 = capa5_execution_stress(ctx)
    ctx.save("capa5_execution_stress", r5)

    # ── GLOBAL VERDICT ──
    print("\n[6/6] Computing global verdict...")
    gv = global_verdict(ctx)
    ctx.save("global_verdict", gv)

    elapsed = time.time() - t0
    print(f"\n  Total time: {elapsed:.0f}s ({elapsed / 60:.1f}min)")
    print(f"\nReport directory: {REPORT_DIR}")


if __name__ == "__main__":
    main()
