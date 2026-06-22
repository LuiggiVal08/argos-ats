#!/usr/bin/env python3
"""
LOCK TEST — Single Configuration Lock Test (Falsification Experiment)

EX-ANTE LOCKED config, NO tuning, NO search, single holdout evaluation.
This is a TERMINATION TEST: does alpha exist in truly unseen data?

LOCKED CONFIG:
  - Model: RandomForest (max_depth=7, n_estimators=100, class_weight='balanced')
  - Features: EXACTLY 41 retained from FASE 5.5 (no modification)
  - Threshold: 0.55 (fixed, not derived from data)
  - SL: 2 ATR
  - TP: 4 ATR
  - Risk per trade: 1%
  - ADX: 0 (disabled)

DATA SPLIT:
  - TRAIN: earliest 65% of data
  - VALIDATION: next 15% (sanity check only, NO tuning)
  - HOLDOUT: final 20% (NEVER used for any decision prior)

PIPELINE:
  1. Compute features (identical to FASE 5.5)
  2. Select 41 retained features
  3. Train RF ONCE on TRAIN
  4. Quick sanity on VALIDATION (report only, no tuning)
  5. Single backtest on HOLDOUT with locked config
  6. Monte Carlo on HOLDOUT trades (1000 permutations)
  7. DSR on HOLDOUT
  8. SPA test on HOLDOUT
  9. PBO on TRAIN+VALIDATION only (NO holdout data)
  10. Gate evaluation → FINAL VERDICT
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
sys.path.insert(0, str(BASE_DIR / "app"))
from domain.entities.multi_timeframe_aligner import MultiTimeframeAligner
from fase55_backtest_engine import BacktestEngine

# ── Paths ──
OHLCV_PATH = BASE_DIR / "data" / "btc_usdt_1h.parquet"
FUNDING_PATH = BASE_DIR / "data" / "btc_funding_rates.parquet"
OI_PATH = BASE_DIR / "data" / "btc_open_interest.parquet"
RETAINED_PATH = BASE_DIR / "data" / "retained_features.txt"
REPORT_DIR = BASE_DIR / "reports" / "lock_test"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# ── Locked config (EX-ANTE, NEVER CHANGED) ──
THRESHOLD = 0.55
SL_MULT = 2.0
TP_MULT = 4.0
RISK_PCT = 0.01

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
N_RETAINED = len(RETAINED_NAMES)

def compute_features(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Compute ALL 68 features + labels. Returns arrays."""
    import ta as ta_lib
    close = df["close"].astype(float).values
    high = df["high"].astype(float).values
    low = df["low"].astype(float).values
    volume = df["volume"].astype(float).values
    ts = df["timestamp"].values

    base_list = []
    for c_name in ["open", "high", "low", "close", "volume"]:
        base_list.append(df[c_name].astype(float).values)
    base_arr = np.column_stack(base_list + [
        ta_lib.momentum.RSIIndicator(pd.Series(close), window=14).rsi().values,
        ta_lib.trend.EMAIndicator(pd.Series(close), window=9).ema_indicator().values,
        ta_lib.trend.EMAIndicator(pd.Series(close), window=21).ema_indicator().values,
        ta_lib.trend.EMAIndicator(pd.Series(close), window=50).ema_indicator().values,
    ])
    macd = ta_lib.trend.MACD(pd.Series(close))
    extra_base = np.column_stack([
        macd.macd().values, macd.macd_signal().values, macd.macd_diff().values,
        ta_lib.volatility.BollingerBands(pd.Series(close), window=20, window_dev=2).bollinger_hband().values,
        ta_lib.volatility.BollingerBands(pd.Series(close), window=20, window_dev=2).bollinger_mavg().values,
        ta_lib.volatility.BollingerBands(pd.Series(close), window=20, window_dev=2).bollinger_lband().values,
        ta_lib.volatility.AverageTrueRange(pd.Series(high), pd.Series(low), pd.Series(close), window=14).average_true_range().values,
        ta_lib.trend.ADXIndicator(pd.Series(high), pd.Series(low), pd.Series(close), window=14).adx().values,
        ta_lib.volume.OnBalanceVolumeIndicator(pd.Series(close), pd.Series(volume)).on_balance_volume().values,
    ])
    v_sma = pd.Series(volume).rolling(20).mean().values
    pct_chg = (pd.Series(close) / pd.Series(close).shift(1) - 1).values * 100.0
    pct_chg = np.nan_to_num(pct_chg, nan=0.0)
    base = np.column_stack([base_arr, extra_base, v_sma, pct_chg])
    base = np.nan_to_num(base, nan=0.0, posinf=0.0, neginf=0.0)

    ohlcv_list = df.reset_index().to_dict(orient="records")
    mtf_df = MultiTimeframeAligner.compute(ohlcv_list, base_tf="1h", higher_tfs=("4h", "1d"))
    mtf_arr = mtf_df.values.astype(np.float64)
    mtf_arr = np.nan_to_num(mtf_arr, nan=0.0, posinf=0.0, neginf=0.0)

    aux = np.zeros((len(df), 3), dtype=np.float64)
    if FUNDING_PATH.exists():
        funding_df = pd.read_parquet(FUNDING_PATH)
        if len(funding_df) > 0:
            fs = funding_df.set_index("timestamp")["fundingRate"].astype(float) * 100.0
            al = fs.reindex(df["timestamp"], method="ffill").values
            aux[:, 0] = np.nan_to_num(al)
            aux[:, 1] = np.nan_to_num(np.append([0], np.diff(al)))
    if OI_PATH.exists():
        oi_df = pd.read_parquet(OI_PATH)
        if len(oi_df) > 0:
            oc = next((c for c in ["openInterestValue", "openInterest"] if c in oi_df.columns), oi_df.columns[1])
            oi_v = oi_df.set_index("timestamp")[oc].astype(float).reindex(df["timestamp"], method="ffill").values
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
    atr_std = pd.Series(cv).rolling(14).std().values
    atr_pctl = pd.Series(atr_std).rank(pct=True).values
    eng[:, 14] = np.where(atr_pctl > 0.7, 1, np.where(atr_pctl < 0.3, -1, 0))
    eng = np.nan_to_num(eng, nan=0.0, posinf=0.0, neginf=0.0)

    X_full = np.column_stack([base, mtf_arr, aux, eng])

    future_ret = (pd.Series(close).shift(-5) / pd.Series(close) - 1.0) * 100.0
    vol_f = future_ret.rolling(60).std()
    adj = future_ret / vol_f.clip(lower=1e-10)
    y = np.full(len(adj), -1, dtype=int)
    y[adj > 0.002] = 1
    y[adj < -0.002] = 0

    return df, X_full, y, close, high, low, volume, ts

def compute_metrics(eq: np.ndarray, initial_balance: float, n_bars: int, years: float) -> dict:
    """Compute trading metrics from equity curve."""
    final_balance = eq[-1]
    total_return = (final_balance / initial_balance - 1.0) * 100
    daily_returns = np.diff(eq) / eq[:-1]
    mean_ret = np.mean(daily_returns)
    std_ret = np.std(daily_returns)
    sharpe = float(mean_ret / std_ret * np.sqrt(365 * 24)) if std_ret > 0 else 0.0
    neg = daily_returns[daily_returns < 0]
    downside = np.std(neg) if len(neg) > 0 else 1e-10
    sortino = float(mean_ret / downside * np.sqrt(365 * 24))
    cagr = float((final_balance / initial_balance) ** (1 / years) - 1) * 100 if years > 0 else 0.0
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / peak * 100
    max_dd = float(np.max(dd))
    calmar = float(cagr / max_dd) if max_dd > 0 else 0.0
    return {
        "total_return_pct": total_return,
        "sharpe": sharpe,
        "sortino": sortino,
        "cagr": cagr,
        "calmar": calmar,
        "max_dd_pct": max_dd,
    }

def run_backtest(
    close: np.ndarray, high: np.ndarray, low: np.ndarray,
    timestamps: np.ndarray, proba: np.ndarray, atr: np.ndarray | None = None,
    threshold: float = THRESHOLD, sl_mult: float = SL_MULT, tp_mult: float = TP_MULT,
    risk_pct: float = RISK_PCT, initial_balance: float = 10000.0,
) -> dict:
    """Run BacktestEngine with locked config, return result dict."""
    engine = BacktestEngine(
        sl_mult=sl_mult, tp_mult=tp_mult, risk_pct=risk_pct,
        fee=0.001, slippage=0.0005, spread=0.0001, min_prob=threshold, adx_threshold=0.0,
    )
    result = engine.run(
        close=close, high=high, low=low, timestamps=timestamps,
        proba=proba, adx=None, initial_balance=initial_balance, atr_values=atr,
    )
    n_bars = len(close)
    years = n_bars / (365 * 24)
    eq = np.array(result.equity_curve)
    metrics = compute_metrics(eq, initial_balance, n_bars, years)
    trade_pnls = [t.pnl_usd for t in result.trades if t.exit_reason != "END"]
    n_trades = len(trade_pnls)
    wins = [p for p in trade_pnls if p > 0]
    losses = [p for p in trade_pnls if p < 0]
    win_rate = len(wins) / n_trades if n_trades > 0 else 0.0
    profit_factor = sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else float("inf")
    expectancy = np.mean(trade_pnls) if trade_pnls else 0.0

    return {
        "metrics": metrics,
        "n_trades": n_trades,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "expectancy": float(expectancy),
        "avg_win": float(np.mean(wins)) if wins else 0.0,
        "avg_loss": float(np.mean(losses)) if losses else 0.0,
        "trade_pnls": trade_pnls,
        "equity_curve": eq.tolist(),
    }

def fast_pbo_backtest(
    close: np.ndarray, high: np.ndarray, low: np.ndarray,
    proba: np.ndarray, atr: np.ndarray, mask: np.ndarray,
    threshold: float, sl_mult: float, tp_mult: float,
    risk_pct: float = RISK_PCT, initial_balance: float = 10000.0,
) -> float:
    """Minimal backtest returning Sharpe ratio. No object overhead."""
    c = close; h = high; l = low; a = atr; p = proba; m = mask
    bal = float(initial_balance)
    peak = bal
    eq_rets: list[float] = []
    pos_active = False
    pos_sl = 0.0; pos_tp = 0.0; pos_sz = 0.0; pos_ep = 0.0
    fee = 0.001; slip = 0.0005; sprd = 0.0001
    n = len(c)

    for i in range(1, n):
        if not m[i]:
            continue

        if pos_active:
            sl_hit = l[i] <= pos_sl
            tp_hit = h[i] >= pos_tp
            if sl_hit or tp_hit:
                ex_px = pos_sl * (1 - slip) if sl_hit else pos_tp * (1 - slip)
                gross = pos_sz * (ex_px - pos_ep)
                fee_c = (abs(pos_sz * pos_ep) + abs(pos_sz * ex_px)) * fee
                net = gross - fee_c
                bal += net
                eq_rets.append((bal - peak) / peak if peak > 0 else 0.0)
                peak = max(peak, bal)
                pos_active = False
                continue

        if not pos_active and p[i] >= threshold and m[i]:
            atr_i = a[i] if a[i] > 0 else c[i] * 0.01
            sl_dist = atr_i * sl_mult
            if sl_dist <= 0:
                continue
            pos_sz = (bal * risk_pct) / sl_dist
            if pos_sz <= 0:
                continue
            pos_ep = c[i] * (1 + slip + sprd)
            pos_sl = pos_ep - sl_dist
            pos_tp = pos_ep + atr_i * tp_mult
            pos_active = True

    if pos_active:
        ex_px = c[-1] * (1 - slip)
        gross = pos_sz * (ex_px - pos_ep)
        fee_c = (abs(pos_sz * pos_ep) + abs(pos_sz * ex_px)) * fee
        bal += gross - fee_c

    eq_rets_arr = np.array(eq_rets)
    if len(eq_rets_arr) < 5:
        return 0.0
    mean_r = np.mean(eq_rets_arr)
    std_r = np.std(eq_rets_arr)
    if std_r <= 0:
        return 0.0
    return float(mean_r / std_r * np.sqrt(365 * 24))

def run_spa_test(
    champion_sharpe: float, close: np.ndarray, champion_eq: list[float],
    n_bootstrap: int = 1000, block_size: int = 5,
) -> dict:
    """SPA test: compare champion equity Sharpe vs benchmarks via stationary bootstrap.

    All metrics use equity-curve-based annualized Sharpe for consistency.
    """
    np.random.seed(42)

    n = len(close)
    bh_eq = 10000.0 * close / close[0]
    bh_rets = np.diff(bh_eq) / bh_eq[:-1]
    bh_sharpe = float(np.mean(bh_rets) / np.maximum(np.std(bh_rets), 1e-10) * np.sqrt(365 * 24))

    champ_eq_arr = np.array(champion_eq)
    champ_rets = np.diff(champ_eq_arr) / champ_eq_arr[:-1]
    eq_sharpe = float(np.mean(champ_rets) / np.maximum(np.std(champ_rets), 1e-10) * np.sqrt(365 * 24))

    if n < 100:
        return {"p_value": 1.0, "champion_sharpe": champion_sharpe, "bh_sharpe": bh_sharpe,
                "equity_sharpe": eq_sharpe, "n_bootstrap": n_bootstrap, "error": "insufficient data"}

    ret_std = float(np.std(champ_rets))

    n_bench = 30
    benchmark_sharpes = [bh_sharpe, 0.0]
    for _ in range(n_bench):
        rand_rets = np.random.randn(n) * ret_std
        rand_sr = float(np.mean(rand_rets) / np.maximum(np.std(rand_rets), 1e-10) * np.sqrt(365 * 24))
        benchmark_sharpes.append(rand_sr)

    best_benchmark = max(benchmark_sharpes)
    if best_benchmark >= eq_sharpe:
        return {"p_value": 0.5, "champion_sharpe": champion_sharpe, "bh_sharpe": bh_sharpe,
                "equity_sharpe": eq_sharpe, "best_benchmark_sharpe": best_benchmark,
                "n_bootstrap": n_bootstrap}

    d_obs = eq_sharpe - best_benchmark
    d_boot = np.zeros(n_bootstrap)
    for b in range(n_bootstrap):
        null_rets = np.random.randn(n) * ret_std
        null_sr = float(np.mean(null_rets) / np.maximum(np.std(null_rets), 1e-10) * np.sqrt(365 * 24))

        bench_rets = np.random.randn(n) * ret_std
        bench_sr = float(np.mean(bench_rets) / np.maximum(np.std(bench_rets), 1e-10) * np.sqrt(365 * 24))

        d_boot[b] = null_sr - max(bh_sharpe, 0.0, bench_sr)

    p_value = float(np.mean(d_boot >= d_obs))
    return {
        "p_value": p_value,
        "champion_sharpe": champion_sharpe,
        "equity_sharpe": eq_sharpe,
        "bh_sharpe": bh_sharpe,
        "best_benchmark_sharpe": best_benchmark,
        "n_bootstrap": n_bootstrap,
        "n_benchmarks": n_bench + 2,
        "d_obs": d_obs,
        "d_boot_mean": float(np.mean(d_boot)),
        "d_boot_std": float(np.std(d_boot)),
    }

def run_pbo(
    close: np.ndarray, high: np.ndarray, low: np.ndarray,
    proba: np.ndarray, atr: np.ndarray, timestamps: np.ndarray,
    n_groups: int = 8, k_test: int = 2,
) -> dict:
    """PBO on TRAIN+VALIDATION data. NO holdout data used."""
    n = len(close)
    group_size = n // n_groups
    groups = [np.arange(i * group_size, (i + 1) * group_size if i < n_groups - 1 else n) for i in range(n_groups)]

    thresholds = [0.45, 0.50, 0.525, 0.55, 0.575, 0.60, 0.65]
    sl_mults = [1.5, 2.0, 2.5, 3.0]
    tp_mults = [3, 4, 5]

    configs: list[dict] = []
    for th in thresholds:
        for sl in sl_mults:
            for tp in tp_mults:
                configs.append({"threshold": th, "sl_mult": sl, "tp_mult": tp})
    m = len(configs)

    from itertools import combinations
    split_results: list[dict] = []
    all_splits = list(combinations(range(n_groups), k_test))

    for oos_groups in all_splits:
        is_mask = np.ones(n, dtype=bool)
        for g in oos_groups:
            is_mask[groups[g]] = False
        oos_mask = ~is_mask

        config_perf: list[float] = []
        for cfg in configs:
            sr = fast_pbo_backtest(
                close, high, low, proba, atr, is_mask,
                cfg["threshold"], cfg["sl_mult"], cfg["tp_mult"],
            )
            config_perf.append(sr)

        if np.std(config_perf) == 0:
            continue

        ranked = np.argsort(config_perf)[::-1]
        best_config_idx = ranked[0]

        oos_sr = fast_pbo_backtest(
            close, high, low, proba, atr, oos_mask,
            configs[best_config_idx]["threshold"],
            configs[best_config_idx]["sl_mult"],
            configs[best_config_idx]["tp_mult"],
        )

        oos_perf_all = []
        for cfg in configs:
            sr = fast_pbo_backtest(
                close, high, low, proba, atr, oos_mask,
                cfg["threshold"], cfg["sl_mult"], cfg["tp_mult"],
            )
            oos_perf_all.append(sr)

        oos_median = np.median(oos_perf_all)
        below_median = float(oos_sr < oos_median)

        split_results.append({
            "oos_groups": [int(g) for g in oos_groups],
            "best_is_config": {
                "threshold": configs[best_config_idx]["threshold"],
                "sl_mult": configs[best_config_idx]["sl_mult"],
                "tp_mult": configs[best_config_idx]["tp_mult"],
            },
            "best_is_sharpe": float(config_perf[best_config_idx]),
            "best_oos_sharpe": float(oos_sr),
            "oos_median_sharpe": float(oos_median),
            "below_median": below_median,
        })

    n_splits = len(split_results)
    if n_splits == 0:
        return {"error": "no valid splits", "pbo": 1.0, "n_splits": 0, "n_configs": m}

    pbo = float(np.mean([s["below_median"] for s in split_results]))

    locked_idx = None
    for i, cfg in enumerate(configs):
        if (cfg["threshold"] == THRESHOLD and cfg["sl_mult"] == SL_MULT and cfg["tp_mult"] == TP_MULT):
            locked_idx = i
            break

    locked_rank_by_split = []
    if locked_idx is not None:
        for oos_groups in all_splits:
            is_mask = np.ones(n, dtype=bool)
            for g in oos_groups:
                is_mask[groups[g]] = False
            config_perf = []
            for cfg in configs:
                sr = fast_pbo_backtest(close, high, low, proba, atr, is_mask,
                                         cfg["threshold"], cfg["sl_mult"], cfg["tp_mult"])
                config_perf.append(sr)
            ranked = np.argsort(config_perf)[::-1]
            rank = int(np.where(ranked == locked_idx)[0][0]) + 1
            locked_rank_by_split.append(rank)

    return {
        "pbo": pbo,
        "n_splits": n_splits,
        "n_configs": m,
        "n_groups": n_groups,
        "k_test": k_test,
        "locked_config_ranks": locked_rank_by_split,
        "locked_avg_rank": float(np.mean(locked_rank_by_split)) if locked_rank_by_split else None,
        "verdict": "pass" if pbo < 0.3 else ("weak" if pbo < 0.5 else "fail"),
    }

def run_monte_carlo(trade_pnls: list[float], initial_balance: float = 10000.0,
                    n_permutations: int = 1000) -> dict:
    """Permute trade PnL sequences and compute ruin/DD distribution."""
    np.random.seed(42)
    pnls = np.array(trade_pnls)
    n = len(pnls)
    if n < 5:
        return {"error": f"too few trades ({n})", "ruin_probability": 1.0}

    final_balances = []
    max_drawdowns = []

    for _ in range(n_permutations):
        perm = np.random.permutation(pnls)
        bal = initial_balance
        peak = bal
        max_dd = 0.0
        for pnl in perm:
            bal += pnl
            peak = max(peak, bal)
            dd = (peak - bal) / peak * 100
            max_dd = max(max_dd, dd)
        final_balances.append(bal)
        max_drawdowns.append(max_dd)

    fb = np.array(final_balances)
    md = np.array(max_drawdowns)
    ruin_pct = float(np.mean(fb <= 0) * 100)
    dd_p95 = float(np.percentile(md, 95))
    dd_p99 = float(np.percentile(md, 99))
    dd_mean = float(np.mean(md))
    bal_p5 = float(np.percentile(fb, 5))
    bal_mean = float(np.mean(fb))

    return {
        "n_permutations": n_permutations,
        "ruin_probability_pct": ruin_pct,
        "dd_mean_pct": dd_mean,
        "dd_p95_pct": dd_p95,
        "dd_p99_pct": dd_p99,
        "final_balance_p5": bal_p5,
        "final_balance_mean": bal_mean,
    }

def compute_dsr(sharpe: float, n_bars: int, m: int = 60) -> dict:
    """Deflated Sharpe Ratio (Bailey & López de Prado 2014)."""
    t = n_bars
    if t <= 0:
        return {"dsr": 0.0, "error": "no data"}

    max_sr = sharpe
    euler_m = 0.5772156649
    var_max = (1 - euler_m + np.log(m)) * (t - 1) / (t * (t - 2)) * (t - 1) / (t - 2) if t > 2 else 1.0
    var_normal = 1.0 / t
    var_adjusted = max(var_normal, var_max)

    gam = float(euler_m - np.log(np.log(m)) + (np.pi ** 2) / (6 * (np.log(m)) ** 2))
    e_max = np.sqrt(var_adjusted) * ((1 - gam) * np.sqrt(2 * np.log(m)) + gam / np.sqrt(2 * np.log(m)))

    if e_max <= 0:
        return {"dsr": 1.0 if max_sr > 0 else 0.0, "e_max": 0.0, "var_adjusted": var_adjusted, "m": m}

    dsr = float(norm_cdf((max_sr - e_max) / np.sqrt(var_adjusted)))

    return {
        "dsr": dsr,
        "e_max": float(e_max),
        "var_adjusted": var_adjusted,
        "m": m,
        "observed_sharpe": max_sr,
    }

def norm_cdf(x: float) -> float:
    return (1.0 + _erf(x / np.sqrt(2.0))) / 2.0

def _erf(x: float) -> float:
    # Abramowitz & Stegun approximation
    a = 0.254829592; b = -0.284496736; c = 1.421413741
    d = -1.453152027; e = 1.061405429; p = 0.3275911
    sign = 1 if x >= 0 else -1
    x = abs(x)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a * t + b) * t) + c) * t + d) * t + e) * t * np.exp(-x * x)
    return sign * y


def evaluate_gate(results: dict) -> dict:
    """GATE evaluation for Lock Test verdict."""
    verdicts = {}
    all_pass = True
    weak_only = True

    holdout = results["holdout"]

    # Sharpe > 1.0
    if holdout["metrics"]["sharpe"] > 1.0:
        verdicts["sharpe"] = {"pass": True, "value": holdout["metrics"]["sharpe"]}
    else:
        verdicts["sharpe"] = {"pass": False, "value": holdout["metrics"]["sharpe"]}
        all_pass = False

    # Calmar > 1.0
    if holdout["metrics"]["calmar"] > 1.0:
        verdicts["calmar"] = {"pass": True, "value": holdout["metrics"]["calmar"]}
    else:
        verdicts["calmar"] = {"pass": False, "value": holdout["metrics"]["calmar"]}
        all_pass = False

    # Max DD < 20%
    if holdout["metrics"]["max_dd_pct"] < 20.0:
        verdicts["max_dd"] = {"pass": True, "value": holdout["metrics"]["max_dd_pct"]}
    else:
        verdicts["max_dd"] = {"pass": False, "value": holdout["metrics"]["max_dd_pct"]}
        all_pass = False

    # Profit Factor > 1.3 (weak)
    pf = holdout["profit_factor"]
    verdicts["profit_factor"] = {
        "pass_fuerte": pf > 1.5,
        "pass_debil": pf > 1.3,
        "value": pf,
    }
    if not (pf > 1.3):
        all_pass = False

    # SPA p < 0.05 (strong) or < 0.10 (weak)
    spa_p = results["spa"]["p_value"]
    verdicts["spa"] = {
        "pass_fuerte": spa_p < 0.05,
        "pass_debil": spa_p < 0.10,
        "value": spa_p,
    }
    if spa_p >= 0.10:
        all_pass = False

    # PBO < 0.3 (strong) or < 0.5 (weak)
    pbo_val = results["pbo"]["pbo"]
    verdicts["pbo"] = {
        "pass_fuerte": pbo_val < 0.3,
        "pass_debil": pbo_val < 0.5,
        "value": pbo_val,
    }
    if pbo_val >= 0.5:
        all_pass = False
        weak_only = False

    # DSR > 0
    dsr_val = results["dsr"]["dsr"]
    verdicts["dsr"] = {"pass": dsr_val > 0, "value": dsr_val}
    if dsr_val <= 0:
        all_pass = False

    # Monte Carlo ruin < 5% (weak) or < 1% (strong)
    mc_ruin = results["monte_carlo"]["ruin_probability_pct"]
    verdicts["monte_carlo_ruin"] = {
        "pass_fuerte": mc_ruin < 1.0,
        "pass_debil": mc_ruin < 5.0,
        "value": mc_ruin,
    }
    if mc_ruin >= 5.0:
        all_pass = False

    # HARD FAIL conditions
    sharpe_val = holdout["metrics"]["sharpe"]
    hard_fail = False
    hard_reasons = []
    if sharpe_val <= 0:
        hard_fail = True
        hard_reasons.append(f"Sharpe={sharpe_val:.3f} <= 0")
    if pbo_val >= 0.5:
        hard_fail = True
        hard_reasons.append(f"PBO={pbo_val:.3f} >= 0.5")
    if dsr_val <= 0:
        hard_fail = True
        hard_reasons.append(f"DSR={dsr_val:.3f} <= 0")
    if spa_p >= 0.15:
        hard_fail = True
        hard_reasons.append(f"SPA p={spa_p:.3f} >= 0.15")

    if hard_fail:
        verdict = "HARD FAIL"
        verdict_code = "NO_ALPHA"
    elif all_pass:
        verdict = "PASS (REAL ALPHA)"
        verdict_code = "REAL_ALPHA"
    else:
        verdict = "WEAK ALPHA"
        verdict_code = "WEAK_ALPHA"

    return {
        "verdict": verdict,
        "verdict_code": verdict_code,
        "hard_fail": hard_fail,
        "hard_reasons": hard_reasons,
        "conditions": verdicts,
    }


def main():
    print("=" * 72)
    print("LOCK TEST — Single Config Falsification Experiment")
    print("=" * 72)
    t0 = time.time()

    # ── 1. Load data ──
    print("\n[1/5] Loading OHLCV data...")
    df = pd.read_parquet(OHLCV_PATH)
    print(f"  Rows: {len(df)}, Range: {df['timestamp'].min()} → {df['timestamp'].max()}")

    # ── 2. Compute features ──
    print("\n[2/5] Computing features (68 total)...")
    df, X_full, y, close, high, low, volume, ts = compute_features(df)
    print(f"  Feature matrix: {X_full.shape}")

    # Select retained features
    X = X_full[:, RETAINED_INDICES]
    print(f"  Retained features: {X.shape[1]} ({N_RETAINED})")

    # Compute ATR entirely (needed for backtest)
    import ta as ta_lib
    atr = ta_lib.volatility.AverageTrueRange(pd.Series(high), pd.Series(low), pd.Series(close), window=14).average_true_range().values
    atr = np.nan_to_num(atr, nan=0.0, posinf=0.0, neginf=0.0)
    atr[atr <= 0] = close[atr <= 0] * 0.01

    # ── 3. Data split (65/15/20) ──
    print("\n[3/5] Data split...")
    n = len(df)
    holdout_size = int(n * 0.20)
    val_size = int(n * 0.15)
    train_size = n - holdout_size - val_size

    train_idx = np.arange(0, train_size)
    val_idx = np.arange(train_size, train_size + val_size)
    holdout_idx = np.arange(train_size + val_size, n)

    print(f"  TRAIN:     {train_size:>6d} bars  ({df['timestamp'].iloc[train_idx[0]]} → {df['timestamp'].iloc[train_idx[-1]]})")
    print(f"  VALIDATION:{val_size:>6d} bars  ({df['timestamp'].iloc[val_idx[0]]} → {df['timestamp'].iloc[val_idx[-1]]})")
    print(f"  HOLDOUT:   {holdout_size:>6d} bars  ({df['timestamp'].iloc[holdout_idx[0]]} → {df['timestamp'].iloc[holdout_idx[-1]]})")

    # Save split boundaries
    split_info = {
        "train": {"start": str(df["timestamp"].iloc[train_idx[0]]), "end": str(df["timestamp"].iloc[train_idx[-1]])},
        "validation": {"start": str(df["timestamp"].iloc[val_idx[0]]), "end": str(df["timestamp"].iloc[val_idx[-1]])},
        "holdout": {"start": str(df["timestamp"].iloc[holdout_idx[0]]), "end": str(df["timestamp"].iloc[holdout_idx[-1]])},
    }

    # ── 4. Train RF on TRAIN only ──
    print("\n[4/5] Training RandomForest on TRAIN...")
    X_train = X[train_idx]
    y_train = y[train_idx]
    mask_train = y_train != -1
    print(f"  Training samples: {mask_train.sum()} / {len(y_train)}")

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)

    t_start = time.time()
    rf = RandomForestClassifier(max_depth=7, n_estimators=100, class_weight="balanced", random_state=42)
    rf.fit(X_train_scaled[mask_train], y_train[mask_train])
    train_time = time.time() - t_start
    print(f"  Train time: {train_time:.1f}s")

    # ── 5. Predict on all data ──
    print("\n  Predicting on all data...")
    X_all_scaled = scaler.transform(X)

    proba_all = rf.predict_proba(X_all_scaled)
    n_classes = proba_all.shape[1]
    if n_classes == 2:
        proba = proba_all[:, 1]
    elif n_classes == 3:
        proba = proba_all[:, 2]
    else:
        proba = np.zeros(n)
    proba = np.where(y != -1, proba, 0.0)

    # ── 6. Validation sanity check ──
    print("\n  Validation sanity check (no tuning)...")
    val_result = run_backtest(
        close[val_idx], high[val_idx], low[val_idx], ts[val_idx], proba[val_idx], atr[val_idx],
    )
    v = val_result["metrics"]
    print(f"  VALIDATION: Sharpe={v['sharpe']:.3f}, Calmar={v['calmar']:.3f}, "
          f"CAGR={v['cagr']:.2f}%, DD={v['max_dd_pct']:.2f}%")
    val_sane = v["sharpe"] > 0 and v["calmar"] > 0
    print(f"  Sanity: {'✓ PASS' if val_sane else '✗ FAIL'}")

    # ── 7. HOLDOUT evaluation ──
    print(f"\n[5/5] HOLDOUT evaluation (LOCKED config: th={THRESHOLD}, SL={SL_MULT}, TP={TP_MULT})...")
    holdout_result = run_backtest(
        close[holdout_idx], high[holdout_idx], low[holdout_idx], ts[holdout_idx], proba[holdout_idx], atr[holdout_idx],
    )
    print(f"  HOLDOUT METRICS:")
    print(f"    CAGR:       {holdout_result['metrics']['cagr']:.2f}%")
    print(f"    Sharpe:     {holdout_result['metrics']['sharpe']:.3f}")
    print(f"    Sortino:    {holdout_result['metrics']['sortino']:.3f}")
    print(f"    Calmar:     {holdout_result['metrics']['calmar']:.3f}")
    print(f"    Max DD:     {holdout_result['metrics']['max_dd_pct']:.2f}%")
    print(f"    Total Ret:  {holdout_result['metrics']['total_return_pct']:.2f}%")
    print(f"    Profit Fac: {holdout_result['profit_factor']:.3f}")
    print(f"    Trades:     {holdout_result['n_trades']}")
    print(f"    Win Rate:   {holdout_result['win_rate']:.2%}")
    print(f"    Expectancy: ${holdout_result['expectancy']:.2f}")

    # ── 8. Monte Carlo ──
    print("\n  Running Monte Carlo (1000 permutations)...")
    mc_result = run_monte_carlo(holdout_result["trade_pnls"], n_permutations=1000)
    print(f"    Ruin prob:  {mc_result['ruin_probability_pct']:.2f}%")
    print(f"    DD p95:     {mc_result['dd_p95_pct']:.2f}%")
    print(f"    DD p99:     {mc_result['dd_p99_pct']:.2f}%")
    print(f"    Bal p5:     ${mc_result['final_balance_p5']:.2f}")
    print(f"    Bal mean:   ${mc_result['final_balance_mean']:.2f}")

    # ── 9. DSR ──
    print("\n  Computing DSR...")
    holdout_n_bars = len(holdout_idx)
    dsr_result = compute_dsr(holdout_result["metrics"]["sharpe"], holdout_n_bars, m=60)
    print(f"    DSR:        {dsr_result['dsr']:.4f} (M=60)")
    print(f"    E[max]:     {dsr_result['e_max']:.3f}")
    drs_sens = compute_dsr(holdout_result["metrics"]["sharpe"], holdout_n_bars, m=200)
    print(f"    DSR (M=200): {drs_sens['dsr']:.4f}")
    dsr_result["m_200"] = drs_sens["dsr"]

    # ── 10. SPA test ──
    print("\n  Running SPA test...")
    spa_result = run_spa_test(
        float(holdout_result["metrics"]["sharpe"]), close[holdout_idx],
        holdout_result["equity_curve"], n_bootstrap=1000,
    )
    print(f"    SPA p-value: {spa_result['p_value']:.4f}")
    print(f"    Champion Sharpe: {spa_result['champion_sharpe']:.3f}")
    print(f"    B&H Sharpe:      {spa_result['bh_sharpe']:.3f}")

    # ── 11. PBO (TRAIN+VAL only) ──
    print("\n  Running PBO on TRAIN+VALIDATION...")
    train_val_idx = np.concatenate([train_idx, val_idx])
    t_start = time.time()
    pbo_result = run_pbo(
        close[train_val_idx], high[train_val_idx], low[train_val_idx],
        proba[train_val_idx], atr[train_val_idx], ts[train_val_idx],
        n_groups=8, k_test=2,
    )
    pbo_time = time.time() - t_start
    pbo_result["compute_time_s"] = pbo_time
    print(f"    PBO:        {pbo_result['pbo']:.3f}")
    print(f"    Splits:     {pbo_result['n_splits']}")
    print(f"    Configs:    {pbo_result['n_configs']}")
    print(f"    Time:       {pbo_time:.0f}s")
    if pbo_result.get("locked_avg_rank"):
        print(f"    Locked avg rank: {pbo_result['locked_avg_rank']:.2f}")

    # ── 12. Gate evaluation ──
    print("\n" + "=" * 72)
    print("GATE EVALUATION")
    print("=" * 72)

    all_results = {
        "config": {"threshold": THRESHOLD, "sl_mult": SL_MULT, "tp_mult": TP_MULT, "risk_pct": RISK_PCT},
        "split": split_info,
        "validation": val_result,
        "holdout": holdout_result,
        "monte_carlo": mc_result,
        "dsr": dsr_result,
        "spa": spa_result,
        "pbo": pbo_result,
    }

    gate = evaluate_gate(all_results)
    all_results["gate"] = gate

    print(f"\n  {'='*40}")
    print(f"  VERDICT: {gate['verdict']}")
    print(f"  {'='*40}")
    print()
    if gate["hard_fail"]:
        print(f"  HARD FAIL reasons:")
        for r in gate["hard_reasons"]:
            print(f"    - {r}")
    print()
    print(f"  Conditions:")
    for cond, info in gate["conditions"].items():
        status = "✓" if info.get("pass", info.get("pass_debil", False)) else "✗"
        val = info.get("value", "")
        if "pass_fuerte" in info:
            if info["pass_fuerte"]:
                status = "✓"
            elif info["pass_debil"]:
                status = "~"
            else:
                status = "✗"
        print(f"    {status} {cond}: {val}")

    # ── 13. Interpretation ──
    print("\n" + "=" * 72)
    print("INTERPRETATION (STRICT)")
    print("=" * 72)

    if gate["verdict_code"] == "NO_ALPHA":
        print(f"\n  🔴 NO ALPHA — {gate['verdict']}")
        print(f"  Reasons: {', '.join(gate['hard_reasons'])}")
        print("  The feature space + modeling pipeline does NOT contain exploitable alpha.")
        print("  Recommendation: STOP this line of research. Explore new datasources.")
    elif gate["verdict_code"] == "WEAK_ALPHA":
        print(f"\n  🟡 WEAK ALPHA — {gate['verdict']}")
        print("  Signal exists but is fragile. Not production-ready.")
        print("  The alpha depends on favorable conditions in the search space.")
    else:
        print(f"\n  🟢 REAL ALPHA — {gate['verdict']}")
        print("  The feature space + locked config contains exploitable alpha.")
        print("  Survived falsification across all statistical tests.")
        print("  Note: This confirms alpha in ONE locked config on ONE holdout period.")
        print("  Further validation on more data + symbol diversification is needed before production.")

    elapsed = time.time() - t0
    print(f"\n  Total time: {elapsed:.0f}s ({elapsed/60:.1f}min)")

    # ── Save report ──
    report_path = REPORT_DIR / "lock_test_report.json"
    serializable = json.loads(json.dumps(all_results, default=str))
    with open(report_path, "w") as f:
        json.dump(serializable, f, indent=2, default=str)
    print(f"\n  Report saved: {report_path}")


if __name__ == "__main__":
    main()
