#!/usr/bin/env python3
"""PHASE 38 — Feature Selection for ARGOS ATS.

Goal: Determine the minimum feature subset that preserves alpha
(MCC within 95% of the full 53-feature model).

Methods:
  1. Correlation analysis — pearson r matrix, identify redundant clusters
  2. L1 feature selection — LogisticRegression(C=0.1, penalty=l1, saga)
     with walk-forward CV to find stable nonzero features
  3. Greedy backward elimination — drop weakest features iteratively
  4. Optimal subset recommendation

Usage:
    python scripts/qv2_phase38.py

Output:
    qv2_phase38_output/
    ├── correlation_report.json    — 53x53 correlation matrix summary
    ├── l1_selection.json         — L1 coefficients across folds
    ├── backward_elimination.json — MCC per feature subset size
    ├── optimal_features.json     — recommended feature subset
    └── PHASE38_REPORT.md         — full report with recommendations
"""

from __future__ import annotations

import json
import os
import sys
import time
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
os.environ["PYTHONWARNINGS"] = "ignore"

from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import RobustScaler
from sklearn.metrics import matthews_corrcoef
from sklearn.feature_selection import SelectFromModel

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "apps" / "analytics-engine"))

from app.infrastructure.training.feature_engine import FeatureEngine
from app.infrastructure.training.label_engine import LabelEngine

# ── Constants ──────────────────────────────────────────────────────
SYMBOL = "BTC/USDT"
TIMEFRAME = "1h"
LOOKAHEAD = 3
VOL_WINDOW = 60
THRESHOLD_SIGMA = 0.5
RANDOM_SEED = 42
WARMUP_DROP = 120
N_FOLDS = 10
CACHE_DIR = PROJECT_ROOT / "cache" / "qv2"
OHLCV_CACHE = CACHE_DIR / "btc_1h_2020_2026.pkl"
OUTPUT_DIR = PROJECT_ROOT / "qv2_phase38_output"
PLOTS_DIR = OUTPUT_DIR / "plots"

np.random.seed(RANDOM_SEED)

# ── Feature groups ─────────────────────────────────────────────────
FEATURE_GROUPS: dict[str, list[str]] = {
    "raw_ohlcv_1h": ["open", "high", "low", "close", "volume"],
    "rsi": ["rsi", "htf_rsi_4h", "htf_rsi_1d"],
    "ema_fast": ["ema_fast", "htf_ema_fast_4h", "htf_ema_fast_1d"],
    "ema_medium": ["ema_medium", "htf_ema_medium_4h", "htf_ema_medium_1d"],
    "ema_slow": ["ema_slow", "htf_ema_slow_4h", "htf_ema_slow_1d"],
    "macd": ["macd", "htf_macd_4h", "htf_macd_1d"],
    "macd_signal": ["macd_signal", "htf_macd_signal_4h", "htf_macd_signal_1d"],
    "macd_hist": ["macd_hist", "htf_macd_hist_4h", "htf_macd_hist_1d"],
    "bb_upper": ["bb_upper", "htf_bb_upper_4h", "htf_bb_upper_1d"],
    "bb_middle": ["bb_middle", "htf_bb_middle_4h", "htf_bb_middle_1d"],
    "bb_lower": ["bb_lower", "htf_bb_lower_4h", "htf_bb_lower_1d"],
    "atr": ["atr", "htf_atr_4h", "htf_atr_1d"],
    "adx": ["adx", "htf_adx_4h", "htf_adx_1d"],
    "obv": ["obv", "htf_obv_4h", "htf_obv_1d"],
    "volume_sma": ["volume_sma", "htf_volume_sma_4h", "htf_volume_sma_1d"],
    "pct_change": ["pct_change", "htf_pct_change_4h", "htf_pct_change_1d"],
    "funding": ["funding_rate", "funding_momentum", "funding_change"],
}
ALL_FEATURES = [f for group in FEATURE_GROUPS.values() for f in group]
assert len(ALL_FEATURES) == 53, f"Expected 53 features, got {len(ALL_FEATURES)}"


STRIDE = 5


def load_data() -> pd.DataFrame:
    print("Loading cached OHLCV...")
    ohlcv = pd.read_pickle(str(OHLCV_CACHE))
    print(f"  shape: {ohlcv.shape}")
    return ohlcv


def compute_all(
    ohlcv: pd.DataFrame,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    print("Computing 53 features and TARGET_SPEC_V1 labels...")
    t0 = time.perf_counter()

    ohlcv_i = ohlcv.set_index(pd.to_datetime(ohlcv["timestamp"], unit="ms"))
    features_df = FeatureEngine.compute_all(ohlcv_i)
    features = features_df.values.astype(np.float64)
    feature_names = list(features_df.columns)

    close = ohlcv["close"].astype(float)
    label_onehot = LabelEngine.label_3class_onehot(
        close, lookahead=LOOKAHEAD, threshold_sigma=THRESHOLD_SIGMA, vol_window=VOL_WINDOW,
    )
    labels = np.argmax(label_onehot, axis=1).astype(np.int64)
    print(f"  features: {features.shape}, labels: {labels.shape} ({time.perf_counter()-t0:.1f}s)")

    # Subsample with stride=5 for faster feature selection
    ss_idx = np.arange(WARMUP_DROP, len(features), STRIDE)
    features = features[ss_idx]
    labels = labels[ss_idx]
    print(f"  subsampled: {features.shape} (stride={STRIDE}, warmup removed)")

    return features, labels, ohlcv["timestamp"].values[ss_idx], feature_names


def generate_walk_forward_splits(
    n: int, n_folds: int = N_FOLDS, min_train_pct: float = 0.15,
) -> list[tuple[slice, slice]]:
    """Expanding window splits — chronological, no leakage.
    warmup already removed from data."""
    splits: list[tuple[slice, slice]] = []
    test_frac = (1.0 - min_train_pct) / n_folds

    for i in range(n_folds):
        train_end = int(n * (min_train_pct + i * test_frac))
        test_end = int(n * (min_train_pct + (i + 1) * test_frac))

        train_end = min(train_end, n - 1)
        test_end = min(test_end, n)

        if test_end - train_end < 50:
            continue
        splits.append((slice(0, train_end), slice(train_end, test_end)))

    return splits


def evaluate_mcc(
    X_train: np.ndarray, y_train: np.ndarray,
    X_test: np.ndarray, y_test: np.ndarray,
    C: float = 0.1,
) -> float:
    """Train LR and return test MCC."""
    scaler = RobustScaler().fit(X_train)
    X_train_s = scaler.transform(X_train)
    X_test_s = scaler.transform(X_test)

    model = LogisticRegression(C=C, solver="lbfgs", max_iter=5000, random_state=RANDOM_SEED)
    model.fit(X_train_s, y_train)
    y_pred = model.predict(X_test_s)
    return float(matthews_corrcoef(y_test, y_pred))


# ── 1. Correlation Analysis ───────────────────────────────────────

def correlation_analysis(
    features: np.ndarray, feature_names: list[str],
) -> dict[str, Any]:
    """Compute 53x53 correlation matrix and identify redundant clusters."""
    print("\n[1] CORRELATION ANALYSIS")
    t0 = time.perf_counter()

    df = pd.DataFrame(features, columns=feature_names)
    corr = df.corr(method="pearson").values

    # Find highly correlated pairs (|r| > 0.95)
    high_pairs: list[dict] = []
    n = len(feature_names)
    for i in range(n):
        for j in range(i + 1, n):
            r = corr[i, j]
            if abs(r) > 0.95:
                high_pairs.append({
                    "feature_i": feature_names[i],
                    "feature_j": feature_names[j],
                    "correlation": round(float(r), 4),
                })

    # Group features by redundancy: find clusters where every pair has |r| > 0.95
    # Use a simple graph approach: features are nodes, edges if |r| > 0.95
    adj: dict[int, set[int]] = {i: set() for i in range(n)}
    for i in range(n):
        for j in range(i + 1, n):
            if abs(corr[i, j]) > 0.95:
                adj[i].add(j)
                adj[j].add(i)

    # Find connected components (redundant clusters)
    visited = set()
    clusters: list[list[int]] = []
    for i in range(n):
        if i in visited:
            continue
        cluster = []
        stack = [i]
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            cluster.append(node)
            for nb in adj[node]:
                if nb not in visited:
                    stack.append(nb)
        if len(cluster) > 1:
            clusters.append(sorted(cluster))

    # For each cluster, compute max intra-cluster correlation
    cluster_details: list[dict] = []
    for cluster in clusters:
        names = [feature_names[i] for i in cluster]
        intra_corrs = []
        for i in cluster:
            for j in cluster:
                if i < j:
                    intra_corrs.append(abs(corr[i, j]))
        cluster_details.append({
            "size": len(cluster),
            "features": names,
            "max_intra_corr": round(float(max(intra_corrs)), 4) if intra_corrs else 0,
            "min_intra_corr": round(float(min(intra_corrs)), 4) if intra_corrs else 0,
            "mean_intra_corr": round(float(np.mean(intra_corrs)), 4) if intra_corrs else 0,
        })

    # Per-feature: max correlation with any other feature
    max_corr_per_feature = {}
    for i in range(n):
        others = [abs(corr[i, j]) for j in range(n) if j != i]
        max_corr_per_feature[feature_names[i]] = round(float(max(others)), 4)

    # Summary
    n_high_pairs = len(high_pairs)
    n_redundant_features = len({p["feature_i"] for p in high_pairs} | {p["feature_j"] for p in high_pairs})

    result = {
        "n_features": n,
        "n_high_corr_pairs": n_high_pairs,
        "n_redundant_features": n_redundant_features,
        "pct_redundant": round(n_redundant_features / n * 100, 1),
        "n_redundant_clusters": len(cluster_details),
        "clusters": cluster_details,
        "max_corr_per_feature": max_corr_per_feature,
    }
    print(f"  High-correlation pairs (|r|>0.95): {n_high_pairs}")
    print(f"  Redundant features: {n_redundant_features}/{n} ({result['pct_redundant']}%)")
    print(f"  Redundant clusters: {len(cluster_details)}")
    for cl in cluster_details:
        print(f"    Cluster(size={cl['size']}): {', '.join(cl['features'][:3])}... (mean_r={cl['mean_intra_corr']})")
    print(f"  ({time.perf_counter()-t0:.1f}s)")
    return result


# ── 2. L1 Feature Selection ────────────────────────────────────────

def make_binary_labels(labels: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Convert multiclass {0,1,2} to binary {0=SELL, 1=BUY} by dropping HOLD."""
    hold_mask = labels != 1
    y_bin = labels[hold_mask].copy()
    y_bin[y_bin == 2] = 1  # BUY
    # SELL is already 0
    return y_bin, hold_mask


def l1_feature_selection(
    features: np.ndarray, labels: np.ndarray, feature_names: list[str],
    timestamps: np.ndarray,
) -> dict[str, Any]:
    """L1-regularized LR with walk-forward CV to find stable nonzero features.

    Uses binary liblinear (fast L1) on BUY vs SELL.
    """
    print("\n[2] L1 FEATURE SELECTION (binary liblinear, walk-forward CV)")
    t0 = time.perf_counter()

    # Convert to binary
    y_bin, hold_mask = make_binary_labels(labels)
    X_bin = features[hold_mask]
    print(f"  Binary samples: {len(y_bin)} (dropped HOLD)")

    splits = generate_walk_forward_splits(len(X_bin))

    C_values = [0.01, 0.1, 1.0]
    all_results: dict[str, Any] = {}

    for C in C_values:
        fold_coefs: list[np.ndarray] = []
        fold_mcc: list[float] = []
        fold_n_nonzero: list[int] = []

        for fold_idx, (train_s, test_s) in enumerate(splits):

            X_tr = X_bin[train_s]
            y_tr = y_bin[train_s]
            X_te = X_bin[test_s]
            y_te = y_bin[test_s]

            if len(np.unique(y_tr)) < 2 or len(np.unique(y_te)) < 2:
                continue

            scaler = RobustScaler().fit(X_tr)
            X_tr_s = scaler.transform(X_tr)
            X_te_s = scaler.transform(X_te)

            model = LogisticRegression(
                C=C, penalty="l1", solver="liblinear",
                max_iter=5000, random_state=RANDOM_SEED,
            )
            model.fit(X_tr_s, y_tr)
            coef = model.coef_.ravel()

            fold_coefs.append(coef)
            pred = model.predict(X_te_s)
            mcc = float(matthews_corrcoef(y_te, pred))
            fold_mcc.append(mcc)
            fold_n_nonzero.append(int(np.sum(np.abs(coef) > 1e-6)))

        if not fold_coefs:
            continue

        coef_matrix = np.array(fold_coefs)
        mean_abs_coefs = np.abs(coef_matrix).mean(axis=0)
        std_abs_coefs = np.abs(coef_matrix).std(axis=0)
        stability = mean_abs_coefs / (std_abs_coefs + 1e-10)

        nonzero_per_fold = np.array([np.abs(cf) > 1e-6 for cf in fold_coefs])
        consistency = nonzero_per_fold.mean(axis=0)
        consistent_idx = np.where(consistency > 0.5)[0]

        all_results[f"C={C}"] = {
            "C": C,
            "n_folds": len(fold_coefs),
            "mean_mcc": round(float(np.mean(fold_mcc)), 4),
            "std_mcc": round(float(np.std(fold_mcc)), 4),
            "mean_n_nonzero": round(float(np.mean(fold_n_nonzero)), 1),
            "n_consistent_nonzero": int(len(consistent_idx)),
            "consistent_features": [feature_names[i] for i in consistent_idx],
            "top_features": [
                {
                    "feature": feature_names[i],
                    "mean_abs_coef": round(float(mean_abs_coefs[i]), 6),
                    "stability": round(float(stability[i]), 2),
                    "consistency": round(float(consistency[i]), 4),
                }
                for i in np.argsort(-mean_abs_coefs)[:30]
            ],
        }
        print(f"  C={C:.3f}: MCC={all_results[f'C={C}']['mean_mcc']:.4f} "
              f"nonzero={all_results[f'C={C}']['mean_n_nonzero']:.0f}/{len(feature_names)} "
              f"consistent={all_results[f'C={C}']['n_consistent_nonzero']}")

    print(f"  ({time.perf_counter()-t0:.1f}s)")
    return {"results_by_C": all_results, "n_features_total": len(feature_names)}


# ── 3. Backward Elimination ────────────────────────────────────────

def backward_elimination(
    features: np.ndarray, labels: np.ndarray, feature_names: list[str],
) -> dict[str, Any]:
    """Greedy backward elimination: drop weakest features iteratively.

    Uses L1-based importance ranking to drop features in order of
    increasing importance (weakest first).
    Data is pre-subsampled with warmup already removed.
    """
    print("\n[3] BACKWARD ELIMINATION")
    t0 = time.perf_counter()

    splits = generate_walk_forward_splits(len(features))

    # Get full-feature baseline
    print("  Training full model (53 features)...")
    full_mcc_list: list[float] = []
    for train_s, test_s in splits:
        mcc = evaluate_mcc(features[train_s], labels[train_s], features[test_s], labels[test_s])
        full_mcc_list.append(mcc)
    full_mean_mcc = float(np.mean(full_mcc_list))
    print(f"  Full model: MCC={full_mean_mcc:.4f}")

    # Get feature importance ranking via binary LR coef on full data
    y_bin_b, hold_mask_b = make_binary_labels(labels)
    X_bin_b = features[hold_mask_b]
    scaler = RobustScaler().fit(X_bin_b)
    X_bin_s = scaler.transform(X_bin_b)

    l1 = LogisticRegression(C=0.1, penalty="l1", solver="liblinear", max_iter=5000, random_state=RANDOM_SEED)
    l1.fit(X_bin_s, y_bin_b)
    importance = np.abs(l1.coef_.ravel())
    rank = np.argsort(-importance)

    # Backward elimination
    sizes = [53, 40, 35, 30, 25, 20, 15, 12, 10, 8, 6, 5, 4, 3]
    results: list[dict] = []

    for n_keep in sizes:
        if n_keep >= len(rank):
            continue
        keep_idx = rank[:n_keep]
        dropped = [feature_names[i] for i in rank[n_keep:] if importance[i] < 1e-4]

        fold_mcc: list[float] = []
        for train_s, test_s in splits:
            X_tr_reduced = features[train_s][:, keep_idx]
            X_te_reduced = features[test_s][:, keep_idx]
            mcc = evaluate_mcc(X_tr_reduced, labels[train_s], X_te_reduced, labels[test_s])
            fold_mcc.append(mcc)

        mean_mcc = float(np.mean(fold_mcc))
        mcc_ratio = mean_mcc / full_mean_mcc if full_mean_mcc > 0 else 0

        results.append({
            "n_features": n_keep,
            "mean_mcc": round(mean_mcc, 4),
            "mcc_vs_full": round(mcc_ratio, 4),
            "preserves_95pct": mcc_ratio >= 0.95,
            "dropped_features": dropped[:10],  # sample
            "n_dropped": len(dropped),
        })

        status = "✅" if mcc_ratio >= 0.95 else "⚠️"
        print(f"  {n_keep:2d} features: MCC={mean_mcc:.4f} (ratio={mcc_ratio:.3f}) {status}")

    # Find optimal subset size
    optimal = None
    for r in sorted(results, key=lambda x: -x["n_features"]):
        if r["preserves_95pct"]:
            optimal = r
            break

    print(f"  ({time.perf_counter()-t0:.1f}s)")
    return {
        "full_model_mcc": round(full_mean_mcc, 4),
        "elimination_results": results,
        "optimal_subset_size": optimal["n_features"] if optimal else None,
        "optimal_subset_mcc": optimal["mean_mcc"] if optimal else None,
    }


# ── 4. Optimal Feature Recommendation ──────────────────────────────

def recommend_features(
    corr_result: dict,
    l1_result: dict,
    backward_result: dict,
    feature_names: list[str],
) -> dict[str, Any]:
    """Combine all analyses to recommend optimal feature subset."""
    print("\n[4] OPTIMAL FEATURE RECOMMENDATION")

    # From L1 selection with highest MCC
    l1_best = None
    l1_best = None
    for k, v in l1_result.get("results_by_C", {}).items():
        if l1_best is None or v["mean_mcc"] > l1_best["mean_mcc"]:
            l1_best = v

    l1_features = set(l1_best["consistent_features"]) if l1_best else set()

    # From backward elimination
    optimal_size = backward_result.get("optimal_subset_size", 53)

    # From correlation: for each redundant cluster, recommend keeping 1
    cluster_recommendations: list[dict] = []
    for cl in corr_result.get("clusters", []):
        rec = {
            "indicator_family": cl["features"][0].replace("_1d", "").replace("_4h", "").replace("htf_", ""),
            "redundant_features": cl["features"],
            "recommended_keep": cl["features"][0] if "4h" not in cl["features"][0] and "1d" not in cl["features"][0] else cl["features"][0],
            "rationale": "Multicollinearity > 0.95 — keep shortest timeframe",
        }
        cluster_recommendations.append(rec)

    # Build final recommended set
    recommended = set()
    # Keep all features that are not in redundant clusters (they have unique signal)
    cluster_features = {f for cl in corr_result.get("clusters", []) for f in cl["features"]}
    non_redundant = [f for f in feature_names if f not in cluster_features]
    recommended.update(non_redundant)

    # For each cluster, keep the shortest timeframe (or most important by L1)
    for cl in corr_result.get("clusters", []):
        cl_set = set(cl["features"])
        cl_l1_intersection = cl_set & l1_features
        if cl_l1_intersection:
            # Pick the one with highest L1 coefficient
            recommended.add(list(cl_l1_intersection)[0])
        else:
            # Pick the shortest timeframe
            for tf_priority in ["", "_4h", "_1d"]:
                for f in cl["features"]:
                    if f.endswith(tf_priority) and not f.startswith("htf_"):
                        recommended.add(f)
                        break
                else:
                    continue
                break

    recommended_list = sorted(recommended, key=lambda x: feature_names.index(x))

    print(f"  Non-redundant features: {len(non_redundant)}")
    print(f"  L1-consistent features: {len(l1_features)}")
    print(f"  Recommended subset: {len(recommended_list)} features")
    print(f"  Optimal backward size: {optimal_size}")

    return {
        "total_features": len(feature_names),
        "redundant_clusters": len(corr_result.get("clusters", [])),
        "non_redundant_features": len(non_redundant),
        "l1_consistent_features": len(l1_features),
        "backward_optimal_size": optimal_size,
        "recommended_subset_size": len(recommended_list),
        "recommended_features": recommended_list,
        "cluster_recommendations": cluster_recommendations,
    }


# ── Main ───────────────────────────────────────────────────────────

def main():
    global_time = time.perf_counter()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(PLOTS_DIR, exist_ok=True)

    print("=" * 70)
    print("PHASE 38 — Feature Selection")
    print("=" * 70)
    print(f"  Symbol: {SYMBOL}")
    print(f"  Lookahead: {LOOKAHEAD}, VolWindow: {VOL_WINDOW}, Threshold: {THRESHOLD_SIGMA}σ")
    print(f"  Features: 53 (TARGET_SPEC_V1)")
    print(f"  Total features: {len(ALL_FEATURES)}")
    print(f"  Feature groups: {len(FEATURE_GROUPS)}")
    print()

    ohlcv = load_data()
    features, labels, timestamps, feature_names = compute_all(ohlcv)
    print(f"  Feature names match expected: {feature_names == ALL_FEATURES}")

    # 1. Correlation Analysis
    corr_result = correlation_analysis(features, feature_names)
    save(corr_result, OUTPUT_DIR / "correlation_report.json")

    # 2. L1 Feature Selection
    l1_result = l1_feature_selection(features, labels, feature_names, timestamps)
    save(l1_result, OUTPUT_DIR / "l1_selection.json")

    # 3. Backward Elimination
    backward_result = backward_elimination(features, labels, feature_names)
    save(backward_result, OUTPUT_DIR / "backward_elimination.json")

    # 4. Optimal Recommendation
    rec = recommend_features(corr_result, l1_result, backward_result, feature_names)
    save(rec, OUTPUT_DIR / "optimal_features.json")

    # 5. Generate report
    generate_report(corr_result, l1_result, backward_result, rec, time.perf_counter() - global_time)

    print(f"\n{'=' * 70}")
    print(f"PHASE 38 COMPLETE ({time.perf_counter()-global_time:.0f}s)")
    print(f"Output: {OUTPUT_DIR}")
    print("=" * 70)


def save(data: dict, path: Path):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"  [save] {path.name}")


def generate_report(
    corr: dict, l1: dict, backward: dict, rec: dict, elapsed: float,
):
    """Generate PHASE38_REPORT.md with findings and recommendations."""

    # Build feature group table from L1
    l1_best = None
    for v in l1.get("results_by_C", {}).values():
        if l1_best is None or v["mean_mcc"] > l1_best["mean_mcc"]:
            l1_best = v

    lines = [
        f"# Phase 38 — Feature Selection Report",
        f"",
        f"**Date**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Symbol**: {SYMBOL}",
        f"**Protocol**: TARGET_SPEC_V1 (lookahead={LOOKAHEAD}, vol_window={VOL_WINDOW}, threshold={THRESHOLD_SIGMA}σ)",
        f"**Elapsed**: {elapsed:.0f}s",
        f"",
        f"---",
        f"",
        f"## 1. Correlation Analysis",
        f"",
        f"| Metric | Value |",
        f"|---|---|",
        f"| Features | {corr['n_features']} |",
        f"| High-corr pairs (|r|>0.95) | {corr['n_high_corr_pairs']} |",
        f"| Redundant features | {corr['n_redundant_features']} ({corr['pct_redundant']}%) |",
        f"| Redundant clusters | {corr['n_redundant_clusters']} |",
        f"",
        f"### Redundant Clusters",
        f"",
        f"| Size | Features | Mean Intra-r |",
        f"|---|---|---|",
    ]
    for cl in corr["clusters"]:
        names = ", ".join(cl["features"])
        lines.append(f"| {cl['size']} | {names} | {cl['mean_intra_corr']} |")

    lines += [
        f"",
        f"## 2. L1 Feature Selection (Walk-Forward CV)",
        f"",
        f"| C | Mean MCC | Nonzero (avg) | Consistent |",
        f"|---|---|---|---|",
    ]
    for c_val, res in sorted(l1.get("results_by_C", {}).items()):
        lines.append(
            f"| {res['C']} | {res['mean_mcc']:.4f} | {res['mean_n_nonzero']:.0f}/{l1['n_features_total']} | {res['n_consistent_nonzero']} |"
        )

    if l1_best:
        lines += [
            f"",
            f"### Top Features by L1 Coefficient (C={l1_best['C']})",
            f"",
            f"| Rank | Feature | Mean |coef| Stability | Consistency |",
            f"|---|---|---|---|---|",
        ]
        for i, feat in enumerate(l1_best["top_features_by_abs_coef"][:20]):
            lines.append(
                f"| {i+1} | {feat['feature']} | {feat['mean_abs_coef']} | {feat['stability_score']} | {feat['consistency']} |"
            )

    lines += [
        f"",
        f"## 3. Backward Elimination",
        f"",
        f"| Features | Mean MCC | Ratio vs Full | Preserves 95%? |",
        f"|---|---|---|---|",
    ]
    for r in sorted(backward.get("elimination_results", []), key=lambda x: -x["n_features"]):
        icon = "✅" if r["preserves_95pct"] else "❌"
        lines.append(
            f"| {r['n_features']} | {r['mean_mcc']:.4f} | {r['mcc_vs_full']:.3f} | {icon} |"
        )
    lines += [
        f"",
        f"**Full model**: MCC = {backward.get('full_model_mcc', 0):.4f}",
        f"**Optimal subset size**: {backward.get('optimal_subset_size', 'N/A')} features "
        f"(MCC = {backward.get('optimal_subset_mcc', 'N/A'):.4f})",
        f"",
        f"## 4. Recommendation",
        f"",
        f"| Metric | Value |",
        f"|---|---|",
        f"| Total features | {rec['total_features']} |",
        f"| Non-redundant (by correlation) | {rec['non_redundant_features']} |",
        f"| L1-consistent (across folds) | {rec['l1_consistent_features']} |",
        f"| Backward optimal size | {rec['backward_optimal_size']} |",
        f"| **Recommended subset size** | **{rec['recommended_subset_size']}** |",
        f"",
        f"### Recommended Features",
        f"",
        f"```",
    ]
    for f in rec["recommended_features"]:
        lines.append(f"  {f}")
    lines += [
        f"```",
        f"",
        f"### Cluster Recommendations (redundant → keep one)",
        f"",
        f"| Family | Redundant Features | Keep |",
        f"|---|---|---|",
    ]
    for cl_rec in rec.get("cluster_recommendations", []):
        lines.append(
            f"| {cl_rec['indicator_family']} | {', '.join(cl_rec['redundant_features'])} | {cl_rec['recommended_keep']} |"
        )

    lines += [
        f"",
        f"---",
        f"",
        f"**Next step**: Validate recommended subset with Phase 35 (walk-forward) protocol.",
    ]

    report = "\n".join(lines) + "\n"
    path = OUTPUT_DIR / "PHASE38_REPORT.md"
    path.write_text(report)
    print(f"  [report] {path}")


if __name__ == "__main__":
    main()
