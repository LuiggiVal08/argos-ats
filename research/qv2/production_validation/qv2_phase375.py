#!/usr/bin/env python3
"""PHASE 375 — Hyperparameter Robustness Validation for ARGOS ATS.

The objective is NOT maximizing metrics.
The objective is proving that alpha exists independently of a specific
hyperparameter configuration.

If alpha disappears after small parameter changes, alpha is rejected.

Usage:
    python scripts/qv2_phase375.py

Output:
    qv2_phase375_output/
    ├── all_results.json      — all LR configs + secondary models per fold
    ├── rankings.json         — aggregated rankings
    ├── sensitivity.json      — per-parameter sensitivity
    ├── robustness.json       — alpha robustness criteria check
    ├── PHASE375_REPORT.md
"""

from __future__ import annotations

import json
import os
import sys
import time
import warnings
from collections import defaultdict
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
os.environ["PYTHONWARNINGS"] = "ignore"

from sklearn.linear_model import LogisticRegression, RidgeClassifier
from sklearn.svm import LinearSVC
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.preprocessing import RobustScaler
from sklearn.metrics import matthews_corrcoef, balanced_accuracy_score, f1_score

# ── Project imports ────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "apps" / "analytics-engine"))

from app.infrastructure.training.feature_engine import FeatureEngine
from app.infrastructure.training.label_engine import LabelEngine

# ── Constants ──────────────────────────────────────────────────────
LOOKAHEAD = 3
VOL_WINDOW = 60
THRESHOLD_SIGMA = 0.5
RANDOM_SEED = 42
WARMUP_DROP = 120
TEST_WINDOW_MONTHS = 6
INITIAL_TRAIN_YEARS = 1
CACHE_DIR = PROJECT_ROOT / "cache" / "qv2"
OHLCV_CACHE = CACHE_DIR / "btc_1h_2020_2026.pkl"
OUTPUT_DIR = PROJECT_ROOT / "qv2_phase375_output"

np.random.seed(RANDOM_SEED)

# ── Hyperparameter search space ───────────────────────────────────
C_VALUES = [0.001, 0.003, 0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0]
SOLVERS = ["lbfgs", "newton-cg"]
CLASS_WEIGHTS = [None, "balanced"]

LR_CONFIGS: list[dict] = []
for C, solver, cw in product(C_VALUES, SOLVERS, CLASS_WEIGHTS):
    LR_CONFIGS.append({
        "model_type": "LogisticRegression",
        "C": C,
        "solver": solver,
        "class_weight": cw,
        "penalty": "l2",
        "config_id": f"LR_C={C}_{solver}_cw={cw}",
    })

SECONDARY_MODELS: list[dict] = [
    {"model_type": "LinearSVC", "C": 1.0, "class_weight": None, "config_id": "LinearSVC_C=1.0"},
    {"model_type": "LinearSVC", "C": 1.0, "class_weight": "balanced", "config_id": "LinearSVC_C=1.0_balanced"},
    {"model_type": "RidgeClassifier", "alpha": 1.0, "config_id": "RidgeClassifier_alpha=1.0"},
    {"model_type": "RidgeClassifier", "alpha": 0.1, "config_id": "RidgeClassifier_alpha=0.1"},
    {"model_type": "GradientBoosting", "n_estimators": 100, "max_depth": 3, "lr": 0.1, "config_id": "GBM_n100_d3_lr0.1"},
    {"model_type": "GradientBoosting", "n_estimators": 200, "max_depth": 4, "lr": 0.05, "config_id": "GBM_n200_d4_lr0.05"},
    {"model_type": "RandomForest", "n_estimators": 100, "max_depth": 6, "config_id": "RF_n100_d6"},
    {"model_type": "RandomForest", "n_estimators": 200, "max_depth": 10, "config_id": "RF_n200_d10"},
]

ALL_CONFIGS = LR_CONFIGS + SECONDARY_MODELS


def _build_model(cfg: dict):
    """Build sklearn model from config dict."""
    mt = cfg["model_type"]
    if mt == "LogisticRegression":
        return LogisticRegression(
            C=cfg["C"], solver=cfg["solver"],
            class_weight=cfg["class_weight"],
            penalty="l2", max_iter=10000, random_state=RANDOM_SEED,
        )
    elif mt == "LinearSVC":
        return LinearSVC(
            C=cfg.get("C", 1.0), class_weight=cfg.get("class_weight"),
            max_iter=10000, random_state=RANDOM_SEED,
        )
    elif mt == "RidgeClassifier":
        return RidgeClassifier(alpha=cfg.get("alpha", 1.0), random_state=RANDOM_SEED)
    elif mt == "GradientBoosting":
        return GradientBoostingClassifier(
            n_estimators=cfg.get("n_estimators", 100),
            max_depth=cfg.get("max_depth", 3),
            learning_rate=cfg.get("lr", 0.1),
            random_state=RANDOM_SEED,
        )
    elif mt == "RandomForest":
        return RandomForestClassifier(
            n_estimators=cfg.get("n_estimators", 100),
            max_depth=cfg.get("max_depth", 6),
            n_jobs=-1, random_state=RANDOM_SEED,
        )
    raise ValueError(f"unknown model: {mt}")


# ────────────────────────────────────────────────────────────────────
# 1. Data loading & preparation
# ────────────────────────────────────────────────────────────────────

def load_and_prepare() -> tuple[np.ndarray, np.ndarray, np.ndarray, list[dict]]:
    """Load features, labels, and generate walk-forward folds."""
    print("Loading data...", file=sys.stderr)
    ohlcv = pd.read_pickle(str(OHLCV_CACHE))
    print(f"  OHLCV: {ohlcv.shape}", file=sys.stderr)

    print("Computing 53 features & TARGET_SPEC_V1 labels...", file=sys.stderr)
    t0 = time.perf_counter()
    ohlcv_i = ohlcv.set_index(pd.to_datetime(ohlcv["timestamp"], unit="ms"))
    features_df = FeatureEngine.compute_all(ohlcv_i)
    features = features_df.values.astype(np.float64)
    close = ohlcv["close"].astype(float)
    label_onehot = LabelEngine.label_3class_onehot(
        close, lookahead=LOOKAHEAD, threshold_sigma=THRESHOLD_SIGMA, vol_window=VOL_WINDOW,
    )
    labels = np.argmax(label_onehot, axis=1).astype(np.int64)
    print(f"  features: {features.shape}, labels: {labels.shape} ({time.perf_counter()-t0:.1f}s)", file=sys.stderr)

    # Subsample with stride=5 (same as Phase 35 protocol) for faster evaluation
    STRIDE = 5
    subsample_idx = np.arange(WARMUP_DROP, len(features), STRIDE)
    features = features[subsample_idx]
    labels = labels[subsample_idx]
    print(f"  subsampled: {features.shape} (stride={STRIDE}, warmup already removed)", file=sys.stderr)

    # Generate folds
    print("Generating folds...", file=sys.stderr)
    timestamps_s = ohlcv["timestamp"].values[subsample_idx]  # ms
    dates = pd.to_datetime(timestamps_s, unit="ms")
    start_date, end_date = dates[0], dates[-1]
    cutoff = start_date + pd.DateOffset(years=INITIAL_TRAIN_YEARS)
    step = pd.DateOffset(months=TEST_WINDOW_MONTHS)
    folds = []
    fn = 0
    while cutoff + step < end_date:
        test_start, test_end = cutoff, min(cutoff + step, end_date)
        train_mask = dates < test_start
        test_mask = (dates >= test_start) & (dates < test_end)
        train_idx = np.where(train_mask)[0]
        test_idx = np.where(test_mask)[0]
        if len(train_idx) < 100 or len(test_idx) < 100:
            cutoff += step
            continue
        folds.append({
            "name": f"fold_{fn + 1:02d}",
            "train_start": int(train_idx[0]), "train_end": int(train_idx[-1]),
            "test_start": int(test_idx[0]), "test_end": int(test_idx[-1]),
            "n_train": len(train_idx), "n_test": len(test_idx),
            "train_date_start": str(dates[train_idx[0]]),
            "train_date_end": str(dates[train_idx[-1]]),
            "test_date_start": str(dates[test_idx[0]]),
            "test_date_end": str(dates[test_idx[-1]]),
        })
        fn += 1
        cutoff = test_start + step
    print(f"  {len(folds)} folds", file=sys.stderr)
    return features, labels, timestamps_s, folds


# ────────────────────────────────────────────────────────────────────
# 2. Evaluate a single config on a single fold
# ────────────────────────────────────────────────────────────────────

def eval_config_on_fold(
    cfg: dict, fold: dict, X: np.ndarray, y: np.ndarray,
) -> dict:
    """Train & evaluate on fold. Returns metrics dict."""
    ts, te = fold["train_start"], fold["train_end"] + 1
    tst, tste = fold["test_start"], fold["test_end"] + 1

    X_train = X[ts:te]
    y_train = y[ts:te]
    X_test = X[tst:tste]
    y_test = y[tst:tste]

    if len(X_train) == 0 or len(X_test) == 0:
        return {"error": "empty fold"}

    scaler = RobustScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s = scaler.transform(X_test)

    try:
        model = _build_model(cfg)
        model.fit(X_train_s, y_train)
        y_pred = model.predict(X_test_s)
    except Exception as e:
        return {"error": str(e)}

    mcc = float(matthews_corrcoef(y_test, y_pred))
    ba = float(balanced_accuracy_score(y_test, y_pred))
    f1 = float(f1_score(y_test, y_pred, average="macro"))
    return {"mcc": mcc, "balanced_accuracy": ba, "macro_f1": f1, "n_test": len(y_test)}


# ────────────────────────────────────────────────────────────────────
# 3. Run all configs
# ────────────────────────────────────────────────────────────────────

def run_all(
    X: np.ndarray, y: np.ndarray, folds: list[dict],
) -> list[dict]:
    """Evaluate all configs across all folds."""
    results: list[dict] = []
    n_total = len(ALL_CONFIGS) * len(folds)
    n_done = 0
    t_start = time.perf_counter()

    for cfg in ALL_CONFIGS:
        cid = cfg["config_id"]
        fold_metrics: list[dict] = []
        for fold in folds:
            m = eval_config_on_fold(cfg, fold, X, y)
            m["fold_name"] = fold["name"]
            fold_metrics.append(m)

        # Aggregate across folds
        mccs = [m["mcc"] for m in fold_metrics if "error" not in m and "mcc" in m]
        bas = [m["balanced_accuracy"] for m in fold_metrics if "error" not in m and "balanced_accuracy" in m]
        f1s = [m["macro_f1"] for m in fold_metrics if "error" not in m and "macro_f1" in m]

        n_valid = len(mccs)
        if n_valid == 0:
            continue

        results.append({
            "config_id": cid,
            "model_type": cfg["model_type"],
            "params": {k: v for k, v in cfg.items() if k not in ("model_type", "config_id")},
            "n_valid_folds": n_valid,
            "n_folds_total": len(folds),
            "mcc_mean": float(np.mean(mccs)),
            "mcc_median": float(np.median(mccs)),
            "mcc_std": float(np.std(mccs, ddof=1)),
            "mcc_min": float(np.min(mccs)),
            "mcc_max": float(np.max(mccs)),
            "mcc_positve_pct": float(np.mean([1 for m in mccs if m > 0]) * 100),
            "balanced_accuracy_mean": float(np.mean(bas)),
            "macro_f1_mean": float(np.mean(f1s)),
            "fold_metrics": fold_metrics,
        })

        n_done += 1
        elapsed = time.perf_counter() - t_start
        rate = n_done / elapsed if elapsed > 0 else 0
        remaining = (n_total - n_done) / rate if rate > 0 else 0
        print(f"  [{n_done}/{n_total}] {cid}: MCC={results[-1]['mcc_mean']:.4f} ({elapsed:.0f}s, ~{remaining:.0f}s remain)", file=sys.stderr)

    return results


# ────────────────────────────────────────────────────────────────────
# 4. Analysis
# ────────────────────────────────────────────────────────────────────

def compute_rankings(results: list[dict]) -> dict:
    """Rank all configs by mean MCC."""
    sorted_r = sorted(results, key=lambda r: r["mcc_mean"], reverse=True)
    ranking = []
    for i, r in enumerate(sorted_r, 1):
        ranking.append({
            "rank": i,
            "config_id": r["config_id"],
            "model_type": r["model_type"],
            "mcc_mean": r["mcc_mean"],
            "mcc_std": r["mcc_std"],
            "mcc_median": r["mcc_median"],
            "balanced_accuracy_mean": r["balanced_accuracy_mean"],
            "macro_f1_mean": r["macro_f1_mean"],
        })
    return {"ranking": ranking, "n_configs": len(ranking)}


def compute_lr_sensitivity(results: list[dict]) -> dict:
    """Analyze sensitivity of LR to each hyperparameter."""
    lr_results = [r for r in results if r["model_type"] == "LogisticRegression"]
    piv = pd.DataFrame(lr_results)
    if piv.empty:
        return {"error": "no LR results"}

    # Extract params as columns
    param_data = []
    for r in lr_results:
        p = r["params"]
        param_data.append({
            "config_id": r["config_id"],
            "C": p.get("C", float("nan")),
            "solver": p.get("solver", "unknown"),
            "class_weight": str(p.get("class_weight", "None")),
            "mcc_mean": r["mcc_mean"],
            "mcc_std": r["mcc_std"],
        })
    pdf = pd.DataFrame(param_data)

    # Per-parameter sensitivity
    sensitivity: dict[str, Any] = {}

    # C sensitivity
    c_means = pdf.groupby("C")["mcc_mean"].agg(["mean", "std", "count"])
    c_min = pdf.groupby("C")["mcc_mean"].min()
    c_max = pdf.groupby("C")["mcc_mean"].max()
    sensitivity["C"] = {
        "values": sorted(C_VALUES),
        "mean_mcc_per_C": {str(k): round(float(v), 4) for k, v in c_means["mean"].items()},
        "std_mcc_per_C": {str(k): round(float(v), 4) for k, v in c_means["std"].items()},
        "min_mcc_per_C": {str(k): round(float(v), 4) for k, v in c_min.items()},
        "max_mcc_per_C": {str(k): round(float(v), 4) for k, v in c_max.items()},
        "spread": round(float(c_means["mean"].max() - c_means["mean"].min()), 4),
    }

    # Solver sensitivity
    s_means = pdf.groupby("solver")["mcc_mean"].agg(["mean", "std", "count"])
    sensitivity["solver"] = {
        "mean_mcc": {k: round(float(v), 4) for k, v in s_means["mean"].items()},
        "std_mcc": {k: round(float(v), 4) for k, v in s_means["std"].items()},
        "spread": round(float(s_means["mean"].max() - s_means["mean"].min()), 4),
    }

    # class_weight sensitivity
    cw_means = pdf.groupby("class_weight")["mcc_mean"].agg(["mean", "std", "count"])
    sensitivity["class_weight"] = {
        "mean_mcc": {k: round(float(v), 4) for k, v in cw_means["mean"].items()},
        "std_mcc": {k: round(float(v), 4) for k, v in cw_means["std"].items()},
        "spread": round(float(cw_means["mean"].max() - cw_means["mean"].min()), 4),
    }

    return sensitivity


def compute_model_type_analysis(results: list[dict]) -> dict:
    """Aggregate by model type."""
    by_type: dict[str, list[float]] = defaultdict(list)
    for r in results:
        by_type[r["model_type"]].append(r["mcc_mean"])

    analysis = {}
    for mt, mccs in sorted(by_type.items()):
        analysis[mt] = {
            "n_configs": len(mccs),
            "mcc_mean": round(float(np.mean(mccs)), 4),
            "mcc_std": round(float(np.std(mccs, ddof=1)), 4),
            "mcc_median": round(float(np.median(mccs)), 4),
            "mcc_min": round(float(np.min(mccs)), 4),
            "mcc_max": round(float(np.max(mccs)), 4),
        }
    return analysis


def check_robustness(results: list[dict], rankings: dict) -> dict:
    """Check 4 alpha robustness criteria."""
    sorted_r = sorted(results, key=lambda r: r["mcc_mean"], reverse=True)
    n = len(sorted_r)

    # C1: top 20% configs remain positive
    top20 = sorted_r[:max(1, int(n * 0.2))]
    top20_positive = all(r["mcc_min"] > 0 for r in top20)
    c1 = {
        "name": "top 20% configs remain positive MCC",
        "n_configs_top20": len(top20),
        "all_positive": top20_positive,
        "min_mcc_in_top20": round(min(r["mcc_min"] for r in top20), 4),
        "passed": top20_positive,
    }

    # C2: median config MCC > 0.10
    median_idx = n // 2
    median_mcc = sorted_r[median_idx]["mcc_mean"]
    c2 = {
        "name": "median config MCC > 0.10",
        "median_mcc": round(median_mcc, 4),
        "passed": median_mcc > 0.10,
    }

    # C3: logistic baseline remains within top quartile
    baseline_id = "LR_C=0.1_lbfgs_cw=balanced"
    baseline_rank = None
    for r in rankings["ranking"]:
        if r["config_id"] == baseline_id:
            baseline_rank = r["rank"]
            break
    top_quartile = int(n * 0.25)
    c3 = {
        "name": "logistic baseline (C=0.1, balanced, lbfgs) within top quartile",
        "baseline_rank": baseline_rank,
        "top_quartile_cutoff": top_quartile,
        "passed": baseline_rank is not None and baseline_rank <= top_quartile,
    }

    # C4: no single hyperparameter causes collapse
    # Check that worst LR config still has positive MCC
    lr_results = [r for r in results if r["model_type"] == "LogisticRegression"]
    worst_lr = min(lr_results, key=lambda r: r["mcc_mean"]) if lr_results else None
    c4 = {
        "name": "no single hyperparameter causes collapse (worst LR > 0)",
        "worst_lr_config": worst_lr["config_id"] if worst_lr else "none",
        "worst_lr_mcc": round(worst_lr["mcc_mean"], 4) if worst_lr else float("nan"),
        "passed": worst_lr is not None and worst_lr["mcc_mean"] > 0,
    }

    conditions = [c1, c2, c3, c4]
    all_passed = all(c["passed"] for c in conditions)

    return {
        "robustness_verdict": "ALPHA ROBUST" if all_passed else "ALPHA FRAGILE",
        "all_conditions_passed": all_passed,
        "n_passed": sum(1 for c in conditions if c["passed"]),
        "n_total": len(conditions),
        "conditions": conditions,
    }


# ────────────────────────────────────────────────────────────────────
# 5. Report generation
# ────────────────────────────────────────────────────────────────────

def generate_report(
    rankings: dict,
    sensitivity: dict,
    model_analysis: dict,
    robustness: dict,
    results: list[dict],
) -> str:
    """Generate PHASE375_REPORT.md."""
    lines: list[str] = []

    def w(line: str = "") -> None:
        lines.append(line)

    w("# PHASE 375 — Hyperparameter Robustness Validation")
    w()
    w(f"**Generated**: {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')}")
    w(f"**Search space**: {len(LR_CONFIGS)} LogisticRegression configs + {len(SECONDARY_MODELS)} secondary model configs")
    w(f"**Folds**: 10 expanding-window (same as Phase 35)")
    w()

    w("---")
    w("## 1. Executive Summary")
    w()
    w(f"**Verdict: {robustness['robustness_verdict']}**")
    w(f"Conditions: {robustness['n_passed']}/{robustness['n_total']}")
    w()
    for c in robustness["conditions"]:
        chk = "✅" if c["passed"] else "❌"
        w(f"- {chk} {c['name']}: {c.get('value', c.get('min_mcc_in_top20', c.get('median_mcc', c.get('worst_lr_mcc', ''))))}")
    w()

    w("---")
    w("## 2. Top 20 Ranking")
    w()
    w("| Rank | Config | Model | Mean MCC | Std MCC | BA | Macro F1 |")
    w("|------|--------|-------|----------|---------|----|----------|")
    for r in rankings["ranking"][:20]:
        mt = r["model_type"][:20]
        w(f"| {r['rank']} | {r['config_id'][:40]} | {mt} | {r['mcc_mean']:.4f} | {r['mcc_std']:.4f} | {r['balanced_accuracy_mean']:.4f} | {r['macro_f1_mean']:.4f} |")
    w()

    # Full ranking as table
    w("### Full Ranking")
    w()
    w("| Rank | Config | MCC Mean | MCC Std | MCC Min | MCC Max |")
    w("|------|--------|----------|---------|---------|---------|")
    for r in rankings["ranking"]:
        w(f"| {r['rank']} | {r['config_id'][:50]} | {r['mcc_mean']:.4f} | {r.get('mcc_std', 0):.4f} | — | — |")
    w()

    w("---")
    w("## 3. Model Type Comparison")
    w()
    w("| Model Family | n Configs | Mean MCC | Std MCC | Min MCC | Max MCC |")
    w("|-------------|-----------|----------|---------|---------|---------|")
    for mt, ma in sorted(model_analysis.items()):
        w(f"| {mt} | {ma['n_configs']} | {ma['mcc_mean']:.4f} | {ma['mcc_std']:.4f} | {ma['mcc_min']:.4f} | {ma['mcc_max']:.4f} |")
    w()

    w("---")
    w("## 4. Hyperparameter Sensitivity (LogisticRegression)")
    w()

    if "error" not in sensitivity:
        # C sensitivity
        w("### C (Regularization Strength)")
        w()
        cs = sensitivity["C"]
        w(f"Spread: **{cs['spread']}** (range of mean MCC across C values)")
        w()
        w("| C | Mean MCC | Std MCC | Min MCC | Max MCC |")
        w("|---|----------|---------|---------|---------|")
        for c_val in cs["values"]:
            ck = str(c_val)
            w(f"| {c_val} | {cs['mean_mcc_per_C'].get(ck, '—')} | {cs['std_mcc_per_C'].get(ck, '—')} | {cs['min_mcc_per_C'].get(ck, '—')} | {cs['max_mcc_per_C'].get(ck, '—')} |")
        w()

        # Solver sensitivity
        w("### Solver")
        w()
        ss = sensitivity["solver"]
        w(f"Spread: **{ss['spread']}**")
        w("| Solver | Mean MCC | Std MCC |")
        w("|--------|----------|---------|")
        for solver, mean in sorted(ss["mean_mcc"].items()):
            w(f"| {solver} | {mean} | {ss['std_mcc'].get(solver, '—')} |")
        w()

        # Class weight sensitivity
        w("### Class Weight")
        w()
        cws = sensitivity["class_weight"]
        w(f"Spread: **{cws['spread']}**")
        w("| Class Weight | Mean MCC | Std MCC |")
        w("|--------------|----------|---------|")
        for cw_val, mean in sorted(cws["mean_mcc"].items()):
            w(f"| {cw_val} | {mean} | {cws['std_mcc'].get(cw_val, '—')} |")
        w()
    else:
        w(f"Sensitivity analysis not available: {sensitivity.get('error')}")
        w()

    w("---")
    w("## 5. Variance / Robustness Analysis")
    w()

    # MCC distribution across all configs
    all_mccs = [r["mcc_mean"] for r in results]
    w(f"- **Number of configs evaluated**: {len(results)}")
    w(f"- **Overall mean MCC**: {np.mean(all_mccs):.4f}")
    w(f"- **Overall median MCC**: {np.median(all_mccs):.4f}")
    w(f"- **Overall std MCC**: {np.std(all_mccs, ddof=1):.4f}")
    w(f"- **Overall min MCC**: {np.min(all_mccs):.4f}")
    w(f"- **Overall max MCC**: {np.max(all_mccs):.4f}")
    n_pos = sum(1 for m in all_mccs if m > 0)
    w(f"- **Configs with positive MCC**: {n_pos}/{len(results)} ({n_pos / len(results) * 100:.0f}%)")
    n_all_pos_folds = sum(1 for r in results if r.get("mcc_min", -1) > 0)
    w(f"- **Configs where ALL folds positive**: {n_all_pos_folds}/{len(results)}")
    w()

    # Distribution chart (text-based)
    w("### MCC Distribution (all configs)")
    w()
    bins = np.linspace(-0.1, 0.5, 13)
    hist, edges = np.histogram(all_mccs, bins=bins)
    bar_max = max(hist) if max(hist) > 0 else 1
    for i in range(len(hist)):
        bar = "▓" * int(hist[i] / bar_max * 30)
        w(f"  [{edges[i]:.2f}-{edges[i+1]:.2f}] {bar} {hist[i]}")
    w()

    w("---")
    w("## 6. Alpha Robustness Criteria")
    w()
    w("| # | Criterion | Result | Passed |")
    w("|---|----------|--------|--------|")
    for i, c in enumerate(robustness["conditions"], 1):
        chk = "✅" if c["passed"] else "❌"
        val = c.get("min_mcc_in_top20", c.get("median_mcc", c.get("worst_lr_mcc", "—")))
        w(f"| {i} | {c['name']} | {val} | {chk} |")
    w()
    w(f"**{robustness['n_passed']}/{robustness['n_total']} conditions passed**")
    w(f"**Verdict: {robustness['robustness_verdict']}**")
    w()

    w("---")
    w("## 7. Answers to Final Questions")
    w()

    # Q1: Is alpha model dependent?
    # If all model families have positive MCC, answer is NO
    all_families_positive = all(
        ma["mcc_min"] > 0 for ma in model_analysis.values()
    )
    w("### Q1: Is alpha model dependent?")
    w()
    if all_families_positive:
        w("**NO.** All 5 model families (LogisticRegression, LinearSVC, RidgeClassifier, "
          "GradientBoosting, RandomForest) achieve positive mean MCC. "
          "The alpha is not an artifact of a specific model architecture.")
    else:
        w("**YES.** Some model families fail to achieve positive MCC. "
          "The alpha may be model-dependent.")
    w()

    # Q2: Is alpha regularization dependent?
    lr_only = [r for r in results if r["model_type"] == "LogisticRegression"]
    lr_mccs = [r["mcc_mean"] for r in lr_only]
    c_sens = sensitivity.get("C", {}).get("spread", float("inf"))
    w("### Q2: Is alpha regularization dependent?")
    w()
    if isinstance(c_sens, (int, float)) and c_sens < 0.05:
        w(f"**NO.** Mean MCC varies by only {c_sens:.4f} across 11 orders of magnitude of C "
          "(0.001 → 100). The alpha is not regularization-dependent.")
    else:
        w(f"**YES.** Mean MCC varies by {c_sens:.4f} across C values, "
          "suggesting sensitivity to regularization strength.")
    w()

    # Q3: Is alpha linear or nonlinear?
    linear_families = ["LogisticRegression", "LinearSVC", "RidgeClassifier"]
    tree_families = ["GradientBoosting", "RandomForest"]
    linear_mccs = [r["mcc_mean"] for r in results if r["model_type"] in linear_families]
    tree_mccs = [r["mcc_mean"] for r in results if r["model_type"] in tree_families]
    linear_mean = np.mean(linear_mccs) if linear_mccs else 0
    tree_mean = np.mean(tree_mccs) if tree_mccs else 0
    w("### Q3: Is alpha linear or nonlinear?")
    w()
    w(f"Linear models mean MCC: **{linear_mean:.4f}**")
    w(f"Tree-based models mean MCC: **{tree_mean:.4f}**")
    if tree_mean > linear_mean * 1.05:
        w("**NONLINEAR.** Tree-based models outperform linear models, suggesting "
          "feature interactions matter.")
    elif linear_mean > tree_mean * 1.05:
        w("**LINEAR.** Linear models outperform tree-based models, suggesting "
          "the decision boundary is approximately linear.")
    else:
        w("**MIXED.** Linear and tree-based models perform similarly, suggesting "
          "the alpha exists in both linear and nonlinear representations.")
    w()

    # Q4: Does tree boosting materially improve results?
    gbm_results = [r for r in results if r["model_type"] == "GradientBoosting"]
    rf_results = [r for r in results if r["model_type"] == "RandomForest"]
    best_lr = max(r["mcc_mean"] for r in lr_only) if lr_only else 0
    best_gbm = max(r["mcc_mean"] for r in gbm_results) if gbm_results else 0
    best_rf = max(r["mcc_mean"] for r in rf_results) if rf_results else 0
    w("### Q4: Does tree boosting materially improve results?")
    w()
    w(f"Best LogisticRegression: **{best_lr:.4f}**")
    w(f"Best GradientBoosting: **{best_gbm:.4f}**")
    w(f"Best RandomForest: **{best_rf:.4f}**")
    if best_gbm > best_lr * 1.1:
        w("**YES.** GradientBoosting materially improves over LogisticRegression.")
    else:
        w("**NO.** GradientBoosting does not materially improve over LogisticRegression. "
          "The alpha is captured by linear decision boundaries.")
    w()

    # Q5: Is feature interaction required?
    w("### Q5: Is feature interaction required?")
    w()
    interaction_gap = best_gbm - best_lr
    if interaction_gap > 0.03:
        w(f"**YES.** Tree-based models outperform by {interaction_gap:.4f} MCC, "
          "suggesting feature interactions add predictive value.")
    else:
        w(f"**NO.** The gap between tree-based and linear models is only "
          f"{interaction_gap:.4f} MCC. Feature interactions are not required "
          "to capture the alpha.")
    w()

    w("---")
    w("## 8. What Was Done")
    w()
    w("### What")
    w("Comprehensive hyperparameter robustness scan of the ARGOS ATS alpha hypothesis "
      f"across {len(results)} model configurations, using the same 10 expanding-window "
      "folds from Phase 35.")
    w()
    w("### How")
    w(f"- LogisticRegression: {len(LR_CONFIGS)} configs (11 C values × 2 solvers × 2 class weights)")
    w(f"- Secondary models: {len(SECONDARY_MODELS)} configs (LinearSVC, RidgeClassifier, "
      "GradientBoosting, RandomForest)")
    w("- Each config evaluated on all 10 walk-forward folds")
    w("- RobustScaler + fit/predict per fold (no leakage)")
    w("- Aggregated: mean/median/std/min/max MCC per config")
    w("- Per-parameter sensitivity analysis")
    w("- 4 alpha robustness criteria applied")
    w()
    w("### Why")
    w("A single configuration achieving high MCC does not prove alpha exists. "
      "If the same data + labels produce positive MCC across a wide range of models "
      "and hyperparameters, the signal is real. If only one narrow configuration works, "
      "the alpha is likely a statistical artifact or overfitting.")
    w()
    w("### Assumptions Challenged")
    w("1. **Alpha is model-specific** → tested across 5 model families")
    w("2. **Alpha is C-dependent** → tested across 11 orders of magnitude")
    w("3. **Alpha requires balanced weights** → tested with None vs balanced")
    w("4. **Alpha requires specific solver** → tested lbfgs/newton-cg")
    w("5. **Alpha requires nonlinear interactions** → tested linear vs tree models")
    w()
    w("### Remaining Risks")
    w("1. **Same features, same folds, same data** — all configs share the same preprocessing")
    w("2. **Transaction costs not modeled** — alpha may not survive 0.14% round-trip")
    w("3. **Funding cost basis** — zero in inference, real in production")
    w("4. **Regime change** — unseen market structure not tested")
    w("5. **Single asset** — BTC-specific only")
    w()

    w("---")
    w("## 9. Recommendation")
    w()
    if robustness["all_conditions_passed"]:
        w("✅ **Alpha survives hyperparameter robustness validation.**")
        w("**Proceed to Phase 38 (Feature Selection).**")
    else:
        w("❌ **Alpha is fragile under hyperparameter changes.**")
        w("**Do not proceed. Investigate configurations that fail.**")
    w()

    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 60, file=sys.stderr)
    print("PHASE 375 — Hyperparameter Robustness Validation", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"LR configs: {len(LR_CONFIGS)}", file=sys.stderr)
    print(f"Secondary configs: {len(SECONDARY_MODELS)}", file=sys.stderr)
    print(f"Total: {len(ALL_CONFIGS)}", file=sys.stderr)
    print(file=sys.stderr)

    # 1. Data
    print("[1] Loading data & computing features/labels...", file=sys.stderr)
    X, y, timestamps, folds = load_and_prepare()
    print(f"  X: {X.shape}, y: {y.shape}", file=sys.stderr)

    # 2. Run all configs
    print("[2] Running all configs...", file=sys.stderr)
    results = run_all(X, y, folds)

    # 3. Save raw results
    with open(str(OUTPUT_DIR / "all_results.json"), "w") as f:
        # Remove fold_metrics to keep file manageable (too large)
        clean_results = []
        for r in results:
            clean = {k: v for k, v in r.items() if k != "fold_metrics"}
            clean_results.append(clean)
        json.dump(clean_results, f, indent=2, default=str)

    # 4. Analysis
    print("[3] Computing rankings...", file=sys.stderr)
    rankings = compute_rankings(results)
    with open(str(OUTPUT_DIR / "rankings.json"), "w") as f:
        json.dump(rankings, f, indent=2, default=str)

    print("[4] Computing sensitivity analysis...", file=sys.stderr)
    sensitivity = compute_lr_sensitivity(results)
    with open(str(OUTPUT_DIR / "sensitivity.json"), "w") as f:
        json.dump(sensitivity, f, indent=2, default=str)

    print("[5] Computing model type analysis...", file=sys.stderr)
    model_analysis = compute_model_type_analysis(results)
    with open(str(OUTPUT_DIR / "model_analysis.json"), "w") as f:
        json.dump(model_analysis, f, indent=2, default=str)

    print("[6] Checking alpha robustness criteria...", file=sys.stderr)
    robustness = check_robustness(results, rankings)
    with open(str(OUTPUT_DIR / "robustness.json"), "w") as f:
        json.dump(robustness, f, indent=2, default=str)

    # 5. Report
    print("[7] Generating PHASE375_REPORT.md...", file=sys.stderr)
    report = generate_report(rankings, sensitivity, model_analysis, robustness, results)
    report_path = OUTPUT_DIR / "PHASE375_REPORT.md"
    with open(str(report_path), "w") as f:
        f.write(report)

    # 6. Summary
    print("=" * 60, file=sys.stderr)
    print("PHASE 375 COMPLETE", file=sys.stderr)
    print(f"Configs evaluated: {len(results)}", file=sys.stderr)
    print(f"Verdict: {robustness['robustness_verdict']}", file=sys.stderr)
    print(f"Conditions: {robustness['n_passed']}/{robustness['n_total']}", file=sys.stderr)
    top5 = rankings["ranking"][:5]
    for r in top5:
        print(f"  #{r['rank']}: {r['config_id'][:40]} → MCC={r['mcc_mean']:.4f}", file=sys.stderr)
    print(f"Report: {report_path}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)


if __name__ == "__main__":
    main()
