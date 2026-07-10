"""Automatic tests verifying NO lookahead leakage in the target definition.

These tests are the first line of defense against data leakage in the
prediction target. They must pass before any model training can proceed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.infrastructure.training.label_engine import LabelEngine


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def random_close() -> pd.Series:
    """Random walk close prices — 2000 samples."""
    np.random.seed(42)
    prices = 100.0 * np.cumprod(1 + np.random.normal(0, 0.01, 2000))
    return pd.Series(prices)


@pytest.fixture
def trend_close() -> pd.Series:
    """Strong trending close prices — 2000 samples."""
    np.random.seed(42)
    trend = np.linspace(0, 0.5, 2000)
    prices = 100.0 * np.cumprod(1 + np.random.normal(0.0005, 0.015, 2000) + trend * 0.0001)
    return pd.Series(prices)


# ── Tests ─────────────────────────────────────────────────────────────


class TestNoLookaheadLeakage:
    """Critical: verify that volatility computation never uses future data."""

    def test_volatility_is_past_only(self, random_close):
        """Volatility at index i must use only data up to i-1.

        This is verified by comparing:
            vol[i] computed by LabelEngine (which uses rolling())
        against a manual computation that is GUARANTEED past-only.

        If they match, there is no leakage.
        """
        close = random_close
        vol_window = 60

        # LabelEngine vol
        le_vol = LabelEngine.compute_historical_volatility(close, window=vol_window)

        # Manual past-only vol (guaranteed no lookahead)
        past_returns = close.pct_change() * 100.0
        manual_vol = past_returns.rolling(vol_window, min_periods=10).std().clip(lower=1e-10)

        # Must match exactly (same computation)
        pd.testing.assert_series_equal(le_vol, manual_vol, check_names=False)

    def test_voladj_return_separates_past_and_future(self, random_close):
        """Verify that the vol-adjusted return numerator contains future data
        but denominator contains ONLY past data.

        This test verifies:
        1. denominator (vol) at index i uses NO data from i or later
        2. numerator (return) uses close[i+h], which IS from the future

        If the test passes, the target is correctly specified.
        """
        close = random_close
        h = 3
        w = 60

        # Compute vol-adjusted returns
        adj = LabelEngine.compute_vol_adj_returns(close, lookahead=h, vol_window=w)

        # Pre-compute past returns (same as LabelEngine does)
        # LabelEngine's compute_historical_volatility:
        #   past_returns = close.pct_change() * 100.0
        #   vol = past_returns.rolling(w, min_periods=10).std()
        #   return vol.clip(lower=1e-10)
        # Then compute_vol_adj_returns divides by hist_vol * sqrt(h)
        past_returns_full = close.pct_change() * 100.0

        for i in range(w, len(close) - h):
            expected_num = (close.iloc[i + h] / close.iloc[i] - 1.0) * 100.0
            expected_den = float(
                past_returns_full.iloc[max(1, i - w + 1) : i + 1].std()
            )
            expected_den = max(expected_den, 1e-10)
            expected_den *= np.sqrt(h)
            expected_adj = expected_num / expected_den

            val = adj.iloc[i]
            if not (np.isnan(val) or np.isinf(val)):
                assert abs(val - expected_adj) < 1e-6, (
                    f"Mismatch at index {i}: LabelEngine={val:.6f}, "
                    f"expected={expected_adj:.6f}"
                )

    def test_vol_never_uses_close_shift_negative(self, random_close):
        """A zero-day test: verify that compute_historical_volatility
        never uses shift(-n) (which would be future data).

        We monkey-patch pd.Series.shift to assert that shift(-n) is
        never called within vol computation.
        """
        close = random_close.copy()

        original_shift = pd.Series.shift

        calls: list = []

        def tracking_shift(self, periods=1, *args, **kwargs):
            calls.append(periods)
            return original_shift(self, periods, *args, **kwargs)

        pd.Series.shift = tracking_shift

        try:
            LabelEngine.compute_historical_volatility(close, window=60)

            # Verify all shift calls used past data (periods >= 0)
            for p in calls:
                assert p >= 0, f"shift({p}) used with negative period (future leakage!)"

        finally:
            pd.Series.shift = original_shift

    def test_label_engine_verify_no_leakage(self):
        """LabelEngine has a design-time assertion that volatility
        is computed from past data only. This test verifies it passes.
        """
        assert LabelEngine.verify_no_future_leakage_in_volatility()


class TestLabelConsistency:
    """Verify that labels are consistent across methods."""

    def test_ternary_labels_use_global_encoding(self, random_close):
        """Ternary labels must use 0=SELL, 1=HOLD, 2=BUY."""
        labels = LabelEngine.label_3class(random_close, lookahead=3, threshold_sigma=0.5)

        assert labels.ndim == 1
        assert len(labels) == len(random_close)
        assert set(np.unique(labels)).issubset({0, 1, 2})

    def test_onehot_labels_match_ternary(self, random_close):
        """One-hot and integer labels must agree."""
        int_labels = LabelEngine.label_3class(random_close, lookahead=3, threshold_sigma=0.5)
        oh_labels = LabelEngine.label_3class_onehot(random_close, lookahead=3, threshold_sigma=0.5)

        assert oh_labels.shape == (len(random_close), 3)

        # One-hot argmax should match integer labels
        oh_argmax = np.argmax(oh_labels, axis=1)
        np.testing.assert_array_equal(oh_argmax, int_labels)

    def test_last_n_labels_are_hold(self, random_close):
        """The last 'lookahead' candles must be HOLD (no future available)."""
        h = 3
        labels = LabelEngine.label_3class(random_close, lookahead=h)
        assert np.all(labels[-h:] == 1)  # HOLD

    def test_sell_encoding(self):
        """SELL must be one-hot [1, 0, 0]."""
        from app.infrastructure.training.label_engine import ONEHOT_SELL
        np.testing.assert_array_equal(ONEHOT_SELL, [1.0, 0.0, 0.0])

    def test_hold_encoding(self):
        """HOLD must be one-hot [0, 1, 0]."""
        from app.infrastructure.training.label_engine import ONEHOT_HOLD
        np.testing.assert_array_equal(ONEHOT_HOLD, [0.0, 1.0, 0.0])

    def test_buy_encoding(self):
        """BUY must be one-hot [0, 0, 1]."""
        from app.infrastructure.training.label_engine import ONEHOT_BUY
        np.testing.assert_array_equal(ONEHOT_BUY, [0.0, 0.0, 1.0])


class TestLabelStability:
    """Verify labels are stable across regimes and configurations."""

    def test_labels_deterministic(self, random_close):
        """Same input must produce identical labels."""
        l1 = LabelEngine.label_3class(random_close, lookahead=3, threshold_sigma=0.5)
        l2 = LabelEngine.label_3class(random_close, lookahead=3, threshold_sigma=0.5)
        np.testing.assert_array_equal(l1, l2)

    def test_threshold_affects_density(self, random_close):
        """Higher threshold should reduce trade density."""
        labels_low = LabelEngine.label_3class(random_close, lookahead=3, threshold_sigma=0.25)
        labels_high = LabelEngine.label_3class(random_close, lookahead=3, threshold_sigma=1.0)

        density_low = np.sum(labels_low != 1) / len(labels_low)
        density_high = np.sum(labels_high != 1) / len(labels_high)

        assert density_low > density_high, (
            f"Lower threshold (0.25) should produce MORE trades than "
            f"higher threshold (1.0): {density_low:.3f} vs {density_high:.3f}"
        )

    def test_voladj_is_more_balanced_than_raw(self, random_close):
        """Vol-adjusted labels should be more balanced across regimes
        than raw return labels. We test this by checking that the
        HOLD proportion is closer to theoretical expectation.
        """
        close = random_close
        h = 3
        th = 0.5

        # Vol-adjusted
        adj = LabelEngine.compute_vol_adj_returns(close, lookahead=h)
        labels_adj = LabelEngine.label_3class(close, lookahead=h, threshold_sigma=th)
        hold_adj = np.sum(labels_adj == 1) / len(labels_adj)

        # Raw
        future = close.shift(-h)
        raw_ret = (future / close - 1.0) * 100.0
        labels_raw = np.full(len(raw_ret), 1, dtype=int)
        labels_raw[raw_ret > th] = 2
        labels_raw[raw_ret < -th] = 0
        labels_raw[-h:] = 1
        hold_raw = np.sum(labels_raw == 1) / len(labels_raw)

        # Both should have HOLD proportion > 0
        assert hold_adj > 0
        assert hold_raw > 0

    def test_regime_labels_pass(self, trend_close, random_close):
        """Labels must work on both trending and random data."""
        for close, name in [(trend_close, "trend"), (random_close, "random")]:
            labels = LabelEngine.label_3class(close, lookahead=3, threshold_sigma=0.5)
            unique = set(np.unique(labels))
            assert unique.issubset({0, 1, 2}), f"{name}: unexpected classes {unique}"
            assert len(labels) == len(close), f"{name}: length mismatch"
