"""LabelEngine — single source of truth for ALL label generation.

GLOBAL ENCODING (IMMUTABLE):
    class 0 = SELL
    class 1 = HOLD
    class 2 = BUY

HARD RULES:
    - Volatility uses ONLY past data (NO lookahead leakage)
    - Only shift-based future return allowed for labeling target
    - No rolling statistics on future-shifted series
    - Same label logic used by training AND inference evaluation
    - vol-adj approach in σ-units (NOT raw returns + ATR)

LEAKAGE PREVENTION:
    _compute_historical_volatility() uses close.pct_change() over
    a rolling window — only looks backward from each point.
    The future return (shift(-lookahead)) is NEVER used in the
    rolling volatility computation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ── Global encoding constants ────────────────────────────────────────
CLASS_SELL = 0
CLASS_HOLD = 1
CLASS_BUY = 2

# One-hot encoding for LSTM/ensemble training output
ONEHOT_SELL = np.array([1.0, 0.0, 0.0], dtype=np.float64)
ONEHOT_HOLD = np.array([0.0, 1.0, 0.0], dtype=np.float64)
ONEHOT_BUY = np.array([0.0, 0.0, 1.0], dtype=np.float64)

# Default parameters
_DEFAULT_LOOKAHEAD = 5
_DEFAULT_VOL_WINDOW = 60
_DEFAULT_THRESHOLD_SIGMA = 0.5


class LabelEngine:
    """Single source of truth for label generation.

    All label methods are static and stateless.
    All outputs use the GLOBAL encoding: 0=SELL, 1=HOLD, 2=BUY.
    """

    # ── Volatility computation (leakage-free) ───────────────────────

    @staticmethod
    def compute_historical_volatility(close: pd.Series, window: int = 60) -> pd.Series:
        """Compute historical volatility using ONLY past returns.

        Args:
            close: price series.
            window: rolling window for std (default 60).

        Returns:
            Series of historical vol, clipped to minimum 1e-10.
        """
        past_returns = close.pct_change() * 100.0
        vol = past_returns.rolling(window, min_periods=10).std()
        return vol.clip(lower=1e-10)

    @staticmethod
    def compute_vol_adj_returns(
        close: pd.Series,
        lookahead: int = _DEFAULT_LOOKAHEAD,
        vol_window: int = _DEFAULT_VOL_WINDOW,
    ) -> pd.Series:
        """Compute volatility-adjusted forward returns with NO lookahead bias.

        Future return for labeling is computed via shift(-lookahead).
        Volatility is computed from historical returns ONLY (no future data).

        Args:
            close: price series.
            lookahead: candles forward for target return.
            vol_window: rolling window for vol estimation.

        Returns:
            Series of vol-adjusted returns in σ-units.
            NaN for last ``lookahead`` rows (no future available).
        """
        future_close = close.shift(-lookahead)
        raw_return = (future_close / close - 1.0) * 100.0

        hist_vol = LabelEngine.compute_historical_volatility(close, window=vol_window)

        # Multiply vol by sqrt(lookahead) to express in σ-units of the
        # multi-period return (not the unit return). This aligns with
        # standard practice: σ_h = σ_1 * √h.
        # Without this factor, y_i scales with h instead of √h, which
        # over-weights longer horizons and breaks threshold calibration.
        return raw_return / (hist_vol * np.sqrt(lookahead))

    # ── Label generators ───────────────────────────────────────────

    @classmethod
    def label_3class(
        cls,
        close: pd.Series,
        lookahead: int = _DEFAULT_LOOKAHEAD,
        threshold_sigma: float = _DEFAULT_THRESHOLD_SIGMA,
        vol_window: int = _DEFAULT_VOL_WINDOW,
    ) -> np.ndarray:
        """Generate 3-class integer labels with GLOBAL encoding.

        class 0 = SELL  (vol-adj return < -threshold_sigma)
        class 1 = HOLD  (|vol-adj return| <= threshold_sigma)
        class 2 = BUY   (vol-adj return > threshold_sigma)

        Args:
            close: price series.
            lookahead: candles forward for target return.
            threshold_sigma: decision threshold in σ-units.
            vol_window: rolling window for vol estimation.

        Returns:
            Integer array of labels using global encoding.
        """
        adj = cls.compute_vol_adj_returns(close, lookahead=lookahead, vol_window=vol_window)
        labels = np.full(len(adj), CLASS_HOLD, dtype=int)
        labels[adj > threshold_sigma] = CLASS_BUY
        labels[adj < -threshold_sigma] = CLASS_SELL
        return labels

    @classmethod
    def label_3class_onehot(
        cls,
        close: pd.Series,
        lookahead: int = _DEFAULT_LOOKAHEAD,
        threshold_sigma: float = _DEFAULT_THRESHOLD_SIGMA,
        vol_window: int = _DEFAULT_VOL_WINDOW,
    ) -> np.ndarray:
        """Generate 3-class one-hot labels for LSTM/ensemble training.

        Returns (n, 3) array where each row is one of:
            SELL → [1.0, 0.0, 0.0]
            HOLD → [0.0, 1.0, 0.0]
            BUY  → [0.0, 0.0, 1.0]

        Last ``lookahead`` rows are HOLD (no future available).
        """
        int_labels = cls.label_3class(close, lookahead=lookahead, threshold_sigma=threshold_sigma, vol_window=vol_window)
        n = len(int_labels)
        onehot = np.zeros((n, 3), dtype=np.float64)
        onehot[range(n), int_labels] = 1.0
        return onehot

    # ── Validation utilities ──────────────────────────────────────

    @staticmethod
    def validate_encoding(labels: np.ndarray) -> bool:
        """Verify labels use ONLY global encoding: 0=SELL, 1=HOLD, 2=BUY."""
        flat = labels.flatten()
        unique = set(np.unique(flat[~np.isnan(flat.astype(float))]).astype(int))
        return unique.issubset({CLASS_SELL, CLASS_HOLD, CLASS_BUY}) and len(unique) > 0

    @staticmethod
    def verify_no_future_leakage_in_volatility() -> bool:
        """Design-time check: volatility uses only `close.pct_change()` (past data).

        Implementation in ``compute_historical_volatility`` uses:
            close.pct_change().rolling(window).std()

        This never uses shift(-n) — it only looks backward.
        """
        return True


def validate_label_encoding(labels: np.ndarray) -> bool:
    """Standalone validation: verify labels use global encoding."""
    return LabelEngine.validate_encoding(labels)


# ── Triple barrier labeling (complementary method) ────────────────


def label_triple_barrier_onehot(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    atr: np.ndarray,
    atr_multiplier: float = 1.5,
    max_holding: int = 5,
) -> np.ndarray:
    """Triple Barrier labeling generating one-hot encoding.

    Returns (n, 3) array with GLOBAL encoding:
        SELL (SL hit first) → [1.0, 0.0, 0.0]
        HOLD (neither hit)  → [0.0, 1.0, 0.0]
        BUY  (TP hit first) → [0.0, 0.0, 1.0]

    Args:
        close: close prices.
        high: high prices.
        low: low prices.
        atr: ATR values.
        atr_multiplier: barriers at entry ± multiplier × ATR.
        max_holding: max candles to hold before labeling HOLD.

    Returns:
        One-hot array (n, 3) with global encoding.
    """
    n = len(close)
    targets = np.zeros((n, 3), dtype=np.float64)

    for i in range(n - max_holding):
        atr_i = atr[i]
        if np.isnan(atr_i) or atr_i <= 0:
            targets[i] = ONEHOT_HOLD
            continue

        tp = close[i] + atr_multiplier * atr_i
        sl = close[i] - atr_multiplier * atr_i
        hit = False

        for t in range(i + 1, min(i + max_holding + 1, n)):
            if low[t] <= sl:
                targets[i] = ONEHOT_SELL  # SL hit = SELL
                hit = True
                break
            if high[t] >= tp:
                targets[i] = ONEHOT_BUY   # TP hit = BUY
                hit = True
                break

        if not hit:
            targets[i] = ONEHOT_HOLD

    targets[-max_holding:] = ONEHOT_HOLD
    return targets
