"""Common utilities for QV2 Phase 3.5 — non-overlapping labels + embargo.

Reuses QV2 feature pipeline (53 features: TA base + MTF 4h/1d + funding).
Only differences from QV2:
  1. stride-aware subsampling to eliminate label overlap
  2. embargo in walk-forward splits: train_end = test_start - ceil(lookahead/stride)
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

# Reuse everything from QV2 except label generators (redefined for stride)
from experiments.quant_validation_v2.common import (
    BASE_FEATURES,
    FUNDING_FEATURES,
    N_FOLDS,
    RANDOM_STATE,
    load_ohlcv,
    load_funding,
    build_feature_matrix,
    compute_vol_adj_returns,
    label_3class as qv2_label_3class,
    label_binary as qv2_label_binary,
    label_regression as qv2_label_regression,
)

REPORT_DIR = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v2_phase35"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def save_report(data: dict, filename: str) -> Path:
    path = REPORT_DIR / filename
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"[report] saved to {path}")
    return path


# ── stride-aware subsampling ──────────────────────────────────────


def subsample_indices(n: int, lookahead: int, stride: int) -> np.ndarray:
    """Return indices into the original array, spaced by `stride`,
    starting at `lookahead` and ending before `n - lookahead`."""
    start = lookahead
    end = n - lookahead
    return np.arange(start, end, stride, dtype=int)


def subsample(X: np.ndarray, y: np.ndarray, indices: np.ndarray,
              valid_mask: np.ndarray | None = None) -> tuple:
    X_sub = X[indices]
    y_sub = y[indices]
    mask_sub = valid_mask[indices] if valid_mask is not None else None
    return X_sub, y_sub, mask_sub


# ── label generators (same as QV2, use same functions) ────────────

label_3class = qv2_label_3class
label_binary = qv2_label_binary
label_regression = qv2_label_regression


# ── walk-forward with embargo ──────────────────────────────────────


def walk_forward_splits_phase35(n: int, n_folds: int,
                                embargo: int) -> list[dict]:
    """Expanding-window walk-forward with strict embargo.

    Parameters
    ----------
    n : int
        Number of samples (after subsampling).
    n_folds : int
        Number of folds.
    embargo : int
        Number of samples to exclude from end of training set per fold
        (= ceil(lookahead / stride)).

    Returns
    -------
    list[dict]
        Each dict: train_start, train_end, test_start, test_end.
    """
    window = n // (n_folds + 1)
    splits = []
    for i in range(n_folds):
        te = (i + 2) * window
        if i == n_folds - 1:
            te = n
        train_end = max(0, (i + 1) * window - embargo)
        splits.append({
            "train_start": 0,
            "train_end": train_end,
            "test_start": (i + 1) * window,
            "test_end": te,
        })
    return splits


# ── null baselines for non-overlap (adapted from QV1) ──────────────


def null_classification(y: np.ndarray, labels: list[int],
                        default_label: int, strategy_name: str):
    """Predict constant label for non-overlap setting."""
    from experiments.quant_validation_v1.common import NullResult
    preds = np.full_like(y, default_label)
    from sklearn.metrics import accuracy_score, f1_score
    return NullResult(
        strategy=strategy_name,
        accuracy=float(accuracy_score(y, preds)),
        f1_macro=float(f1_score(y, preds, average="macro", zero_division=0)),
    )


def null_persist_label(y: np.ndarray, strategy_name: str = "persist_last_label"):
    """Persist last observed label — adapted for non-overlap."""
    from experiments.quant_validation_v1.common import NullResult
    from sklearn.metrics import accuracy_score, f1_score
    preds = np.roll(y, 1)
    preds[0] = y[0]
    return NullResult(
        strategy=strategy_name,
        accuracy=float(accuracy_score(y, preds)),
        f1_macro=float(f1_score(y, preds, average="macro", zero_division=0)),
    )


def null_regression(y: np.ndarray, strategy: str):
    """Null baselines for regression in non-overlap setting."""
    from experiments.quant_validation_v1.common import NullResult
    from sklearn.metrics import r2_score, mean_absolute_error
    if strategy == "predict_zero":
        preds = np.zeros_like(y)
    elif strategy == "predict_mean":
        preds = np.full_like(y, np.mean(y))
    elif strategy == "persist_last_value":
        preds = np.roll(y, 1)
        preds[0] = y[0]
    else:
        raise ValueError(f"Unknown null strategy: {strategy}")
    dir_acc = float(np.mean((np.sign(preds) == np.sign(y)).astype(float)))
    return NullResult(
        strategy=strategy,
        r2=float(r2_score(y, preds)),
        mae=float(mean_absolute_error(y, preds)),
        dir_acc=dir_acc,
        accuracy=0.0,
        f1_macro=0.0,
    )


# ── helpers re-exported from QV2 ──────────────────────────────────

from experiments.quant_validation_v2.common import (
    N_FOLDS,
    RANDOM_STATE,
    SHUFFLE_SEEDS,
    SYMBOL,
    TIMEFRAME,
)
