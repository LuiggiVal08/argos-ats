"""FASE 6.75.5 — Superior Predictive Ability Test (Hansen SPA).

Tests whether champion RF strategy significantly outperforms:
- Buy & Hold
- 30 alternate configs
- Random signal baseline

Uses CPCV (28 splits) and stationary bootstrap (1000 samples).
"""
from __future__ import annotations

import itertools
import json
import random
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR / "experiments"))
from fase55_backtest_engine import BacktestEngine
from fase55_feature_selection import load_data, ALL_NAMES

REPORT_DIR = BASE_DIR / "reports" / "fase675"
REPORT_DIR.mkdir(parents=True, exist_ok=True)
RETAINED_FILE = BASE_DIR / "data" / "retained_features.txt"
LABEL_LOOKAHEAD = 5

N_GROUPS = 8
K_TEST = 2

GROUPS = [
    ("2022-01-01", "2022-08-31"),
    ("2022-09-01", "2023-04-30"),
    ("2023-05-01", "2023-12-31"),
    ("2024-01-01", "2024-05-31"),
    ("2024-06-01", "2024-10-31"),
    ("2024-11-01", "2025-04-30"),
    ("2025-05-01", "2025-10-31"),
    ("2025-11-01", "2026-06-16"),
]

CHAMPION_CONFIG = {"sl_mult": 2.0, "tp_mult": 4.0, "risk_pct": 0.01,
                   "min_prob": 0.6, "adx_threshold": 0,
                   "fee": 0.001, "slippage": 0.0005, "spread": 0.0001}

random.seed(42)
np.random.seed(42)


def load_retained_features() -> list[str]:
    with open(RETAINED_FILE) as f:
        return [line.strip() for line in f if line.strip()]


def get_date_range_mask(timestamps: pd.Series, start: str, end: str) -> np.ndarray:
    if isinstance(timestamps.iloc[0], (int, float, np.integer, np.floating)):
        ts_dt = pd.to_datetime(timestamps, unit="ns")
    else:
        ts_dt = pd.to_datetime(timestamps)
    return (ts_dt >= start) & (ts_dt <= end)


def get_mask_for_groups(timestamps: pd.Series, group_indices: list[int]) -> np.ndarray:
    mask = np.zeros(len(timestamps), dtype=bool)
    for gi in group_indices:
        g = GROUPS[gi]
        mask |= get_date_range_mask(timestamps, g[0], g[1])
    return mask


def compute_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, window: int = 14) -> np.ndarray:
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - close[:-1]),
                               np.abs(low[1:] - close[:-1])))
    tr_full = np.concatenate([[tr[0]], tr])
    atr = pd.Series(tr_full).rolling(window).mean().values
    return np.nan_to_num(atr, nan=0.0)


def purge_indices(train_idx: np.ndarray, test_idx: np.ndarray, timestamps: pd.Series) -> np.ndarray:
    ts = timestamps.values
    test_start = ts[test_idx[0]]
    if isinstance(test_start, (int, float, np.integer, np.floating)):
        test_start_dt = pd.Timestamp(test_start)
    else:
        test_start_dt = pd.Timestamp(test_start)
    purge_cutoff = test_start_dt - pd.Timedelta(hours=LABEL_LOOKAHEAD)
    train_dt = pd.to_datetime(ts[train_idx])
    return train_idx[train_dt < purge_cutoff]


def compute_bh_sharpe(close: np.ndarray, timestamps: np.ndarray) -> float:
    if len(close) < 2:
        return 0.0
    ret = (close[-1] - close[0]) / close[0]
    hourly_returns = np.diff(close) / close[:-1]
    if np.std(hourly_returns) == 0:
        return 0.0
    sharpe = float(np.mean(hourly_returns) / np.std(hourly_returns) * np.sqrt(365 * 24))
    return sharpe


def compute_bh_metrics(close: np.ndarray, timestamps: np.ndarray) -> dict:
    if len(close) < 2:
        return {"total_return_pct": 0.0, "sharpe": 0.0, "cagr": 0.0, "max_dd_pct": 0.0}
    n = len(close)
    total_ret = (close[-1] - close[0]) / close[0] * 100.0
    hourly_rets = np.diff(close) / close[:-1]
    sharpe = float(np.mean(hourly_rets) / np.maximum(np.std(hourly_rets), 1e-10) * np.sqrt(365 * 24))
    years = n / (365 * 24)
    cagr = ((close[-1] / close[0]) ** (1 / years) - 1) * 100 if years > 0 else 0.0
    peak = np.maximum.accumulate(close)
    dd = (peak - close) / peak * 100
    max_dd = float(np.max(dd))
    return {"total_return_pct": total_ret, "sharpe": sharpe, "cagr": cagr,
            "max_dd_pct": max_dd, "calmar": cagr / max_dd if max_dd > 0 else 0.0}


def stationary_bootstrap_indices(T: int, L: float, n_bootstrap: int, rng: np.random.Generator) -> np.ndarray:
    """Generate stationary bootstrap indices (Politis & Romano, 1994)."""
    indices = np.zeros((n_bootstrap, T), dtype=int)
    for b in range(n_bootstrap):
        pos = rng.integers(0, T)
        for t in range(T):
            indices[b, t] = pos
            if rng.random() < 1.0 / L:
                pos = rng.integers(0, T)
            else:
                pos = (pos + 1) % T
    return indices


def generate_random_configs(n: int) -> list[dict]:
    grid = {
        "sl_mult": [1.0, 1.5, 2.0, 2.5, 3.0],
        "tp_mult": [2.0, 3.0, 4.0, 5.0, 6.0],
        "risk_pct": [0.0025, 0.005, 0.01],
        "min_prob": [0.35, 0.425, 0.5, 0.55, 0.6, 0.65, 0.7, 0.8],
    }
    configs = []
    for _ in range(n):
        configs.append({
            "sl_mult": random.choice(grid["sl_mult"]),
            "tp_mult": random.choice(grid["tp_mult"]),
            "risk_pct": random.choice(grid["risk_pct"]),
            "min_prob": random.choice(grid["min_prob"]),
            "adx_threshold": 0,
        })
    return configs


def main():
    t0 = time.time()
    print("=" * 60)
    print("FASE 6.75.5 — Superior Predictive Ability Test (Hansen SPA)")
    print("=" * 60)

    retained = load_retained_features()
    retained_set = set(retained)
    names = list(ALL_NAMES)
    retained_idx = [i for i, name in enumerate(names) if name in retained_set]
    print(f"Retained features: {len(retained)}/{len(names)}")

    df, X, y, close, high, low, volume = load_data()
    timestamps = df["timestamp"]
    full_labels = y.astype(np.int32)
    print(f"Data shape: {X.shape}, range: {timestamps.iloc[0]} to {timestamps.iloc[-1]}")
    atr_full = compute_atr(high, low, close)

    combos = list(itertools.combinations(range(N_GROUPS), K_TEST))

    # Generate benchmark configs (30 alternate configs + 1 random baseline)
    benchmark_configs = generate_random_configs(30)

    # Store champion and benchmark Sharpe per split
    champion_sharpes = []
    benchmark_matrix = []  # (n_splits, n_benchmarks)
    bh_metrics_list = []
    random_sharpes = []

    for combo_id, test_groups in enumerate(combos):
        test_mask = get_mask_for_groups(timestamps, list(test_groups))
        test_idx = np.where(test_mask)[0]
        all_indices = set(range(len(timestamps)))
        train_candidates = sorted(all_indices - set(test_idx))
        train_idx = purge_indices(np.array(train_candidates), test_idx, timestamps)

        if len(train_idx) < 200 or len(test_idx) < 100:
            continue

        X_r = X[:, retained_idx]
        scaler = StandardScaler()
        X_train = scaler.fit_transform(X_r[train_idx])
        y_train = full_labels[train_idx]
        train_valid = y_train != -1

        if train_valid.sum() < 50:
            continue

        rf = RandomForestClassifier(
            max_depth=7, n_estimators=100, class_weight="balanced",
            random_state=42, n_jobs=-1,
        )
        rf.fit(X_train[train_valid], y_train[train_valid])

        X_test = scaler.transform(X_r[test_idx])
        test_valid_mask = full_labels[test_idx] != -1
        proba_test = np.zeros(len(test_idx))
        if test_valid_mask.sum() > 0:
            proba_test[test_valid_mask] = rf.predict_proba(X_test[test_valid_mask])[:, 1]

        ts_oos = timestamps.iloc[test_idx]
        close_oos = close[test_idx]
        high_oos = high[test_idx]
        low_oos = low[test_idx]
        atr_oos = atr_full[test_idx]

        # Champion
        champion_engine = BacktestEngine(**CHAMPION_CONFIG)
        champion_res = champion_engine.run(close_oos, high_oos, low_oos,
                                           ts_oos.values, proba_test, atr_values=atr_oos)
        champion_sharpes.append(champion_res.sharpe)

        # B&H
        bh = compute_bh_metrics(close_oos, ts_oos.values)
        bh_metrics_list.append(bh)

        # Random signal baseline: shuffle proba
        shuffled_proba = proba_test.copy()
        np.random.shuffle(shuffled_proba)
        rand_engine = BacktestEngine(**CHAMPION_CONFIG)
        rand_res = rand_engine.run(close_oos, high_oos, low_oos,
                                   ts_oos.values, shuffled_proba, atr_values=atr_oos)
        random_sharpes.append(rand_res.sharpe)

        # Alternate configs
        split_benchmarks = []
        for bcfg in benchmark_configs:
            bengine = BacktestEngine(
                sl_mult=bcfg["sl_mult"], tp_mult=bcfg["tp_mult"],
                risk_pct=bcfg["risk_pct"],
                min_prob=bcfg["min_prob"], adx_threshold=0,
                fee=0.001, slippage=0.0005, spread=0.0001,
            )
            bres = bengine.run(close_oos, high_oos, low_oos,
                               ts_oos.values, proba_test, atr_values=atr_oos)
            split_benchmarks.append(bres.sharpe)
        benchmark_matrix.append(split_benchmarks)

        g_labels = "; ".join([f"G{g}" for g in test_groups])
        print(f"  Combo {combo_id + 1}/{len(combos)} ({g_labels}): "
              f"Champion Sharpe={champion_res.sharpe:.2f}, "
              f"B&H Sharpe={bh['sharpe']:.2f}, "
              f"Random Sharpe={rand_res.sharpe:.2f}")

    n_splits = len(champion_sharpes)
    if n_splits < 5:
        print(f"\nInsufficient splits: {n_splits}")
        return

    champion_arr = np.array(champion_sharpes)
    bh_arr = np.array([m["sharpe"] for m in bh_metrics_list])
    random_arr = np.array(random_sharpes)
    benchmark_arr = np.array(benchmark_matrix)  # (n_splits, n_benchmarks)

    print(f"\n{'=' * 50}")
    print(f"SPA TEST — PER-SPLIT STATS")
    print(f"{'=' * 50}")
    print(f"  Champion Sharpe:   mean={champion_arr.mean():.2f}, std={champion_arr.std():.2f}")
    print(f"  B&H Sharpe:        mean={bh_arr.mean():.2f}, std={bh_arr.std():.2f}")
    print(f"  Random baseline:   mean={random_arr.mean():.2f}, std={random_arr.std():.2f}")
    print(f"  Alt configs:       mean={benchmark_arr.mean():.2f}, std={benchmark_arr.std():.2f}")

    # Build full loss matrix: (n_splits, n_benchmarks_total)
    # loss = -Sharpe, d = loss_benchmark - loss_champion = Sharpe_champion - Sharpe_benchmark
    all_benchmarks = np.column_stack([bh_arr, random_arr, benchmark_arr])
    n_benchmarks = all_benchmarks.shape[1]
    d = champion_arr[:, None] - all_benchmarks  # (n_splits, n_benchmarks), positive = champion better

    d_bar = d.mean(axis=0)  # mean across splits per benchmark
    d_std = d.std(axis=0, ddof=1)  # sample std per benchmark
    se = d_std / np.sqrt(n_splits)

    # SPA test statistic
    t_stats = d_bar / np.maximum(se, 1e-15)
    T_SPA = float(np.maximum(0, t_stats.max()))
    best_benchmark_idx = int(np.argmax(t_stats))

    print(f"\n  T_SPA statistic:     {T_SPA:.4f}")
    print(f"  Best benchmark idx: {best_benchmark_idx} (d_bar={d_bar[best_benchmark_idx]:.4f})")

    # Stationary bootstrap
    n_bootstrap = 1000
    L = 4  # expected block length
    rng = np.random.default_rng(42)
    boot_idx = stationary_bootstrap_indices(n_splits, L, n_bootstrap, rng)

    # Re-centered d under H0: d*_tk = d_tk - d_bar_k
    d_centered = d - d_bar[None, :]  # (n_splits, n_benchmarks)

    T_star = np.zeros(n_bootstrap)
    for b in range(n_bootstrap):
        boot_sample = d_centered[boot_idx[b]]
        boot_mean = boot_sample.mean(axis=0)
        boot_t = boot_mean / np.maximum(se, 1e-15)
        T_star[b] = float(np.maximum(0, boot_t.max()))

    p_value = float(np.mean(T_star >= T_SPA))

    print(f"\n{'=' * 50}")
    print(f"SPA TEST — RESULTS")
    print(f"{'=' * 50}")
    print(f"  Splits:              {n_splits}")
    print(f"  Benchmarks:          {n_benchmarks} (B&H + random + {len(benchmark_configs)} alt)")
    print(f"  T_SPA:               {T_SPA:.4f}")
    print(f"  p-value:             {p_value:.4f}")
    print(f"  Bootstraps:          {n_bootstrap}")

    if p_value < 0.05:
        verdict = "strong"
        print(f"  → SIGNIFICANT (p < 0.05): Champion significantly outperforms benchmarks.")
    elif p_value < 0.10:
        verdict = "marginal"
        print(f"  → MARGINAL (0.05 < p < 0.10): Weak evidence of superiority.")
    elif p_value < 0.15:
        verdict = "weak"
        print(f"  → WEAK (0.10 < p < 0.15): Champion not clearly distinguishable.")
    else:
        verdict = "fail"
        print(f"  → FAIL (p ≥ 0.15): Champion does NOT outperform benchmarks.")

    output = {
        "n_splits": n_splits,
        "n_benchmarks": n_benchmarks,
        "champion_mean_sharpe": float(champion_arr.mean()),
        "champion_std_sharpe": float(champion_arr.std()),
        "bh_mean_sharpe": float(bh_arr.mean()),
        "random_mean_sharpe": float(random_arr.mean()),
        "alt_configs_mean_sharpe": float(benchmark_arr.mean()),
        "t_spa": T_SPA,
        "p_value": p_value,
        "n_bootstrap": n_bootstrap,
        "best_benchmark_idx": best_benchmark_idx,
        "d_bar": [float(x) for x in d_bar.tolist()],
        "t_stats": [float(x) for x in t_stats.tolist()],
        "verdict": verdict,
        "elapsed_seconds": time.time() - t0,
    }

    report_path = REPORT_DIR / "spa_test.json"
    with open(report_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nReport: {report_path}")


if __name__ == "__main__":
    main()
