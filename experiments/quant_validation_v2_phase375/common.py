"""Common utilities for QV2 Phase 3.75 — Cross-Market Validation.

Reuses Phase 3.5 pipeline exactly. Only change: symbol-parameterized data loading.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from experiments.quant_validation_v2_phase35.common import (
    N_FOLDS,
    RANDOM_STATE,
    SHUFFLE_SEEDS,
    REPORT_DIR as _,
    build_feature_matrix as _bfm,
    save_report as _sr,
)

DATA_DIR = Path(__file__).parent.parent.parent / "apps" / "analytics-engine" / "data"
REPORT_DIR = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v2_phase375"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = ["BTC", "ETH", "SOL"]


def save_report(data: dict, filename: str) -> Path:
    path = REPORT_DIR / filename
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"[report] saved to {path}")
    return path


# ── parameterized data loading ──────────────────────────────────


def ohlcv_path(symbol: str) -> Path:
    return DATA_DIR / f"{symbol.lower()}_usdt_1h.parquet"


def funding_path(symbol: str) -> Path:
    return DATA_DIR / f"{symbol.lower()}_funding_rates.parquet"


def load_ohlcv(symbol: str) -> pd.DataFrame:
    path = ohlcv_path(symbol)
    if not path.exists():
        raise FileNotFoundError(
            f"OHLCV not found for {symbol}: {path}\n"
            f"Run fetch_data.py first."
        )
    df = pd.read_parquet(path)
    print(f"[data] {symbol} OHLCV: {len(df)} rows, {df['timestamp'].min()} → {df['timestamp'].max()}")
    return df


def load_funding(symbol: str) -> pd.DataFrame:
    path = funding_path(symbol)
    if not path.exists():
        raise FileNotFoundError(
            f"Funding not found for {symbol}: {path}\n"
            f"Run fetch_data.py first."
        )
    df = pd.read_parquet(path)
    print(f"[data] {symbol} Funding: {len(df)} rows, {df['timestamp'].min()} → {df['timestamp'].max()}")
    return df


def build_feature_matrix(symbol: str):
    """Load and build feature matrix for a given symbol."""
    ohlcv = load_ohlcv(symbol)
    funding = load_funding(symbol)
    return _bfm(ohlcv, funding), ohlcv


# ── re-exports from Phase 3.5 (unchanged protocol) ──────────────

from experiments.quant_validation_v2_phase35.common import (  # noqa: E402, F401
    BASE_FEATURES,
    FUNDING_FEATURES,
    N_FOLDS,
    RANDOM_STATE,
    SHUFFLE_SEEDS,
    label_3class,
    label_binary,
    label_regression,
    subsample_indices,
    walk_forward_splits_phase35,
    null_classification,
    null_persist_label,
    null_regression,
)
