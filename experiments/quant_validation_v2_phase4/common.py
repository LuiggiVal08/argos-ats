"""Common utilities for QV2 Phase 4 — Cross-Regime Validation.

Reuses Phase 3.5 protocol exactly. Key additions:
  1. Regime definitions (trend + volatility) computed on raw close
  2. Per-regime filtering after subsampling
  3. Insufficient-data detection per fold
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from experiments.quant_validation_v2_phase35.common import (
    BASE_FEATURES,
    FUNDING_FEATURES,
    N_FOLDS,
    RANDOM_STATE,
    SHUFFLE_SEEDS,
    build_feature_matrix as _bfm,
    label_3class,
    label_binary,
    label_regression,
    subsample_indices,
    walk_forward_splits_phase35,
    null_classification,
    null_persist_label,
    null_regression,
    save_report as _sr,
)

REPORT_DIR = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v2_phase4"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

DATA_DIR = Path(__file__).parent.parent.parent / "apps" / "analytics-engine" / "data"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("phase4")


# ── report helpers ─────────────────────────────────────────────────


def save_report(data: dict, filename: str) -> Path:
    path = REPORT_DIR / filename
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    logger.info(f"report saved to {path}")
    return path


def load_report(filename: str) -> dict | None:
    path = REPORT_DIR / filename
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


# ── data loading (BTC Binance only) ────────────────────────────────


def load_ohlcv() -> pd.DataFrame:
    path = DATA_DIR / "btc_usdt_1h.parquet"
    df = pd.read_parquet(path)
    logger.info(f"OHLCV: {len(df)} rows, {df['timestamp'].min()} → {df['timestamp'].max()}")
    return df


def load_funding() -> pd.DataFrame:
    path = DATA_DIR / "btc_funding_rates.parquet"
    df = pd.read_parquet(path)
    logger.info(f"Funding: {len(df)} rows")
    return df


def build_feature_matrix():
    ohlcv = load_ohlcv()
    funding = load_funding()
    return _bfm(ohlcv, funding), ohlcv


# ── regime definitions ─────────────────────────────────────────────


def compute_trend_regime(close: pd.Series, window: int = 50) -> np.ndarray:
    """Label each bar by trend regime.

    Returns ndarray of str: 'bull' | 'bear' | 'sideways' | None (too early).
    """
    rolling_ret = close.pct_change(window) * 100.0
    p40 = rolling_ret.quantile(0.40)
    p60 = rolling_ret.quantile(0.60)

    regimes = np.full(len(close), None, dtype=object)
    regimes[rolling_ret > p60] = "bull"
    regimes[rolling_ret < p40] = "bear"
    mid_mask = (rolling_ret >= p40) & (rolling_ret <= p60)
    regimes[mid_mask] = "sideways"

    count_bull = int((regimes == "bull").sum())
    count_bear = int((regimes == "bear").sum())
    count_side = int((regimes == "sideways").sum())
    total = count_bull + count_bear + count_side
    logger.info(
        f"Trend regimes: bull={count_bull} ({100*count_bull/total:.0f}%), "
        f"bear={count_bear} ({100*count_bear/total:.0f}%), "
        f"sideways={count_side} ({100*count_side/total:.0f}%)"
    )
    return regimes


def compute_volatility_regime(close: pd.Series, window: int = 20) -> np.ndarray:
    """Label each bar by volatility regime.

    Returns ndarray of str: 'high_vol' | 'medium_vol' | 'low_vol' | None (too early).
    """
    rolling_std = close.pct_change().rolling(window).std() * 100.0
    p25 = rolling_std.quantile(0.25)
    p75 = rolling_std.quantile(0.75)

    regimes = np.full(len(close), None, dtype=object)
    regimes[rolling_std > p75] = "high_vol"
    low_mask = rolling_std < p25
    regimes[low_mask] = "low_vol"
    mid_mask = (rolling_std >= p25) & (rolling_std <= p75)
    regimes[mid_mask] = "medium_vol"

    count_h = int((regimes == "high_vol").sum())
    count_m = int((regimes == "medium_vol").sum())
    count_l = int((regimes == "low_vol").sum())
    total = count_h + count_m + count_l
    logger.info(
        f"Volatility regimes: high={count_h} ({100*count_h/total:.0f}%), "
        f"medium={count_m} ({100*count_m/total:.0f}%), "
        f"low={count_l} ({100*count_l/total:.0f}%)"
    )
    return regimes


# ── regime filtering ───────────────────────────────────────────────


def filter_and_validate(
    idx_sub: np.ndarray,
    regime_arr: np.ndarray,
    regime_name: str,
    lookahead: int = 5,
    stride: int = 5,
    n_folds: int = N_FOLDS,
    min_samples_per_fold: int = 100,
) -> tuple[np.ndarray, dict] | tuple[None, dict]:
    """Filter subsampled indices by regime, check per-fold sufficiency.

    Returns (idx_filtered, info) or (None, info) if insufficient.
    """
    mask = regime_arr[idx_sub] == regime_name
    idx_filtered = idx_sub[mask]
    n_total = len(idx_filtered)

    info = {
        "regime": regime_name,
        "n_total_filtered": n_total,
        "n_original_with_regime": int((regime_arr == regime_name).sum()),
    }

    if n_total == 0:
        logger.warning(f"[{regime_name}] 0 samples after subsampling → insufficient_data")
        return None, info

    embargo = np.ceil(lookahead / stride).astype(int)
    window = n_total // (n_folds + 1)
    fold_sizes = []
    for i in range(n_folds):
        test_start = (i + 1) * window
        test_end = (i + 2) * window if i < n_folds - 1 else n_total
        train_end = max(0, (i + 1) * window - embargo)
        fold_sizes.append({
            "fold": i,
            "train": train_end,
            "test": test_end - test_start,
        })

    min_train = min(f["train"] for f in fold_sizes) if fold_sizes else 0
    min_test = min(f["test"] for f in fold_sizes) if fold_sizes else 0
    info["fold_sizes"] = fold_sizes
    info["min_train"] = min_train
    info["min_test"] = min_test

    if min_train < min_samples_per_fold:
        logger.warning(
            f"[{regime_name}] smallest fold train={min_train} < {min_samples_per_fold} "
            f"→ insufficient_data"
        )
        return None, info

    if min_test < min_samples_per_fold:
        logger.warning(
            f"[{regime_name}] smallest fold test={min_test} < {min_samples_per_fold} "
            f"→ insufficient_data"
        )
        return None, info

    logger.info(
        f"[{regime_name}] {n_total} subsampled samples, "
        f"min train={min_train}, min test={min_test} → OK"
    )
    return idx_filtered, info


# ── re-exports from Phase 3.5 (unchanged protocol) ────────────────

# Everything already imported above; make them available for run.py
__all__ = [
    "BASE_FEATURES", "FUNDING_FEATURES", "N_FOLDS", "RANDOM_STATE", "SHUFFLE_SEEDS",
    "REPORT_DIR", "save_report", "load_report", "logger",
    "load_ohlcv", "load_funding", "build_feature_matrix",
    "compute_trend_regime", "compute_volatility_regime",
    "filter_and_validate",
    "label_3class", "label_binary", "label_regression",
    "subsample_indices", "walk_forward_splits_phase35",
    "null_classification", "null_persist_label", "null_regression",
]
