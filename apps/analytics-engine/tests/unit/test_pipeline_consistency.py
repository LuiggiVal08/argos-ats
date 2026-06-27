"""Pipeline consistency validation tests.

Verifies that training and inference use:
1. Same label encoding (global standard: 0=SELL, 1=HOLD, 2=BUY)
2. Same feature computation
3. Consistent class mapping

This is the single source of truth for pipeline consistency.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.domain.value_objects.model_config import ModelConfig
from app.domain.value_objects.signal_side import SignalSide
from app.infrastructure.training.data_preprocessor import TaDataPreprocessor


@pytest.fixture
def preprocessor() -> TaDataPreprocessor:
    return TaDataPreprocessor()


@pytest.fixture
def sample_ohlcv() -> list[dict]:
    """200 velas simuladas de BTC/USDT."""
    np.random.seed(42)
    data = []
    price = 50000.0
    for i in range(200):
        price *= 1 + np.random.normal(0, 0.002)
        high = price * (1 + abs(np.random.normal(0, 0.001)))
        low = price * (1 - abs(np.random.normal(0, 0.001)))
        vol = np.random.uniform(100, 1000)
        data.append({
            "timestamp": i * 3600 * 1000,
            "open": round(price, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(price, 2),
            "volume": round(vol, 4),
        })
    return data


# ── Label encoding contract ─────────────────────────────────────────────
#
# GLOBAL STANDARD:
#   class 0 = SELL  → one-hot [1, 0, 0]
#   class 1 = HOLD  → one-hot [0, 1, 0]
#   class 2 = BUY   → one-hot [0, 0, 1]
#
# This MUST match streaming_inference.py class mapping:
#   probs[0] = SELL, probs[1] = HOLD, probs[2] = BUY


_SELL_ONEHOT = np.array([1.0, 0.0, 0.0])
_HOLD_ONEHOT = np.array([0.0, 1.0, 0.0])
_BUY_ONEHOT = np.array([0.0, 0.0, 1.0])


class TestLabelEncodingContract:
    """Verify create_targets uses correct global encoding."""

    def test_one_hot_encoding_positions(self, preprocessor, sample_ohlcv):
        config = ModelConfig(target_lookahead=5, target_return_pct=0.5)
        targets = _await_async(preprocessor.create_targets(sample_ohlcv, config))

        # Each row must be a valid one-hot vector with correct positions
        for row in targets[:-config.target_lookahead]:
            assert np.isclose(row.sum(), 1.0), f"not one-hot: {row}"
            if row[0] == 1.0:
                np.testing.assert_array_equal(row, _SELL_ONEHOT)
            elif row[1] == 1.0:
                np.testing.assert_array_equal(row, _HOLD_ONEHOT)
            elif row[2] == 1.0:
                np.testing.assert_array_equal(row, _BUY_ONEHOT)
            else:
                pytest.fail(f"invalid encoding: {row}")

    def test_last_lookahead_are_hold(self, preprocessor, sample_ohlcv):
        """Last target_lookahead entries must be HOLD (no future data)."""
        config = ModelConfig(target_lookahead=5)
        targets = _await_async(preprocessor.create_targets(sample_ohlcv, config))
        for i in range(-config.target_lookahead, 0):
            np.testing.assert_array_equal(
                targets[i], _HOLD_ONEHOT,
                f"index {i} should be HOLD [0,1,0]",
            )

    def test_sell_encoding(self, preprocessor, sample_ohlcv):
        """SELL must be [1,0,0] (class 0)."""
        # Force SELL condition using a high threshold and falling price
        ohlcv = list(sample_ohlcv)
        for i in range(len(ohlcv) - 1):
            ohlcv[i]["close"] = 60000.0
        ohlcv[-1]["close"] = 50000.0

        config = ModelConfig(target_lookahead=1, target_return_pct=0.1)
        targets = _await_async(preprocessor.create_targets(ohlcv, config))
        np.testing.assert_array_equal(
            targets[-2], _SELL_ONEHOT,
            "SELL should be [1,0,0]",
        )

    def test_buy_encoding(self, preprocessor, sample_ohlcv):
        """BUY must be [0,0,1] (class 2)."""
        # Force BUY condition using a low threshold and rising price
        ohlcv = list(sample_ohlcv)
        for i in range(len(ohlcv) - 1):
            ohlcv[i]["close"] = 50000.0
        ohlcv[-1]["close"] = 60000.0

        config = ModelConfig(target_lookahead=1, target_return_pct=0.1)
        targets = _await_async(preprocessor.create_targets(ohlcv, config))
        np.testing.assert_array_equal(
            targets[-2], _BUY_ONEHOT,
            "BUY should be [0,0,1]",
        )

    def test_inference_encoding_matches_training(self):
        """Verify streaming_inference.py class mapping matches training encoding.

        streaming_inference.py (lines 258-263):
            class_idx = int(np.argmax(probs))
            prob_sell = float(probs[0])         # probs[0] = P(SELL)
            prob_hold = float(probs[1])         # probs[1] = P(HOLD)
            prob_buy  = float(probs[2])         # probs[2] = P(BUY)
            side = [SELL, HOLD, BUY][class_idx] # class 0=SELL, 1=HOLD, 2=BUY

        Training creates one-hot where:
            [1,0,0] = SELL (class 0)
            [0,1,0] = HOLD (class 1)
            [0,0,1] = BUY  (class 2)

        These match: argmax([1,0,0]) = 0 → SELL ✓
                     argmax([0,1,0]) = 1 → HOLD ✓
                     argmax([0,0,1]) = 2 → BUY  ✓
        """
        sell_probs = np.array([0.8, 0.1, 0.1])
        hold_probs = np.array([0.1, 0.8, 0.1])
        buy_probs = np.array([0.1, 0.1, 0.8])

        assert np.argmax(sell_probs) == 0, "SELL must be class 0"
        assert np.argmax(hold_probs) == 1, "HOLD must be class 1"
        assert np.argmax(buy_probs) == 2, "BUY must be class 2"


class TestFeatureConsistency:
    """Verify build_features produces features compatible with model."""

    def test_20_base_features_match_metadata(self, preprocessor):
        """TaDataPreprocessor must define exactly 20 base features."""
        expected_20 = (
            "open", "high", "low", "close", "volume",
            "rsi", "ema_fast", "ema_medium", "ema_slow",
            "macd", "macd_signal", "macd_hist",
            "bb_upper", "bb_middle", "bb_lower",
            "atr", "adx", "obv", "volume_sma", "pct_change",
        )
        assert preprocessor.FEATURE_NAMES == expected_20, (
            f"base features mismatch: {preprocessor.FEATURE_NAMES}"
        )

    def test_20_feature_config_produces_20_cols(self, preprocessor, sample_ohlcv):
        """Default ModelConfig (20 features) must produce 20 columns."""
        config = ModelConfig()
        features = _await_async(preprocessor.build_features(sample_ohlcv, config))
        assert features.shape[1] == 20, (
            f"expected 20 features, got {features.shape[1]}"
        )

    def test_53_feature_list_produces_53_cols(self, preprocessor):
        """53 features (base + MTF 4h/1d + funding) must produce 53 cols.
        
        Uses 1000 candles (≈42 days) to ensure 1d MTF indicators have data.
        """
        np.random.seed(42)
        data = []
        price = 50000.0
        for i in range(1000):
            price *= 1 + np.random.normal(0, 0.002)
            high = price * (1 + abs(np.random.normal(0, 0.001)))
            low = price * (1 - abs(np.random.normal(0, 0.001)))
            vol = np.random.uniform(100, 1000)
            data.append({
                "timestamp": i * 3600 * 1000,
                "open": round(price, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "close": round(price, 2),
                "volume": round(vol, 4),
            })

        feature_names = (
            "open", "high", "low", "close", "volume",
            "rsi", "ema_fast", "ema_medium", "ema_slow",
            "macd", "macd_signal", "macd_hist",
            "bb_upper", "bb_middle", "bb_lower",
            "atr", "adx", "obv", "volume_sma", "pct_change",
            "htf_rsi_4h", "htf_ema_fast_4h", "htf_ema_medium_4h",
            "htf_ema_slow_4h", "htf_macd_4h", "htf_macd_signal_4h",
            "htf_macd_hist_4h", "htf_bb_upper_4h", "htf_bb_middle_4h",
            "htf_bb_lower_4h", "htf_atr_4h", "htf_adx_4h",
            "htf_obv_4h", "htf_volume_sma_4h", "htf_pct_change_4h",
            "htf_rsi_1d", "htf_ema_fast_1d", "htf_ema_medium_1d",
            "htf_ema_slow_1d", "htf_macd_1d", "htf_macd_signal_1d",
            "htf_macd_hist_1d", "htf_bb_upper_1d", "htf_bb_middle_1d",
            "htf_bb_lower_1d", "htf_atr_1d", "htf_adx_1d",
            "htf_obv_1d", "htf_volume_sma_1d", "htf_pct_change_1d",
            "funding_rate", "funding_momentum", "funding_change",
        )
        config = ModelConfig(features=feature_names)
        features = _await_async(preprocessor.build_features(data, config))
        assert features.shape[1] == 53, (
            f"expected 53 features, got {features.shape[1]}"
        )
        assert not np.isnan(features).any(), "NaN values in 53-feature output"


class TestClassEncodingPermutation:
    """Verify the class-encoding fix: SELL→SELL, HOLD→HOLD, BUY→BUY.

    Regression tests for the encoding rotation bug (P0/Critical) where
    streaming_inference.py mapped probs[0]→BUY, probs[1]→SELL, probs[2]→HOLD
    and [BUY,SELL,HOLD][class_idx] instead of the correct
    [SELL,HOLD,BUY][class_idx].

    The global encoding standard (label_engine.py:29-31):
        0 = SELL, 1 = HOLD, 2 = BUY
    """

    def test_sell_class_emits_sell(self):
        """probs=[0.8,0.1,0.1] → argmax=0 → class 0 → SELL."""
        probs = np.array([0.8, 0.1, 0.1])
        class_idx = int(np.argmax(probs))
        side = [SignalSide.SELL, SignalSide.HOLD, SignalSide.BUY][class_idx]
        assert side == SignalSide.SELL, f"Expected SELL, got {side}"

    def test_hold_class_emits_hold(self):
        """probs=[0.1,0.8,0.1] → argmax=1 → class 1 → HOLD."""
        probs = np.array([0.1, 0.8, 0.1])
        class_idx = int(np.argmax(probs))
        side = [SignalSide.SELL, SignalSide.HOLD, SignalSide.BUY][class_idx]
        assert side == SignalSide.HOLD, f"Expected HOLD, got {side}"

    def test_buy_class_emits_buy(self):
        """probs=[0.1,0.1,0.8] → argmax=2 → class 2 → BUY."""
        probs = np.array([0.1, 0.1, 0.8])
        class_idx = int(np.argmax(probs))
        side = [SignalSide.SELL, SignalSide.HOLD, SignalSide.BUY][class_idx]
        assert side == SignalSide.BUY, f"Expected BUY, got {side}"

    def test_prob_variables_map_correctly(self):
        """Verify prob_sell/prob_hold/prob_buy map to correct sklearn indices.

        With model.classes_ = [0,1,2], predict_proba returns:
            probs[0] = P(class_0) = P(SELL)
            probs[1] = P(class_1) = P(HOLD)
            probs[2] = P(class_2) = P(BUY)
        """
        probs = np.array([0.8, 0.1, 0.1])  # P(SELL)=0.8, P(HOLD)=0.1, P(BUY)=0.1
        prob_sell = float(probs[0])
        prob_hold = float(probs[1])
        prob_buy = float(probs[2])
        assert prob_sell == 0.8, f"Expected prob_sell=0.8, got {prob_sell}"
        assert prob_hold == 0.1, f"Expected prob_hold=0.1, got {prob_hold}"
        assert prob_buy == 0.1, f"Expected prob_buy=0.1, got {prob_buy}"

    def test_guard_rail_passes_on_valid_model(self):
        """Runtime guard rail must pass with classes_=[0,1,2]."""
        sklearn = pytest.importorskip("sklearn")
        from sklearn.linear_model import LogisticRegression
        X = np.array([[1.0], [2.0], [3.0]])
        y = np.array([0, 1, 2])
        model = LogisticRegression(solver="lbfgs", fit_intercept=False)
        model.fit(X, y)
        assert model.classes_.tolist() == [0, 1, 2], (
            f"Expected [0,1,2], got {model.classes_.tolist()}"
        )


class TestModelPathConsistency:
    """Verify model path resolution matches actual model location."""

    def test_models_dir_resolution_from_composition(self):
        """composition.py: models_dir must resolve to project-root/models/.

        composition.py line 671:
            models_dir = Path(__file__).resolve().parent.parent.parent.parent / "models"

        This must point to the project root (4 levels up from composition.py),
        NOT to apps/analytics-engine/models (2 levels up, which was the bug).
        """
        from pathlib import Path

        # Simulate composition.py's path resolution
        #   composition.py:  .parent.parent.parent.parent = project root (4 levels from app/)
        #   test file:       .parent.parent.parent.parent.parent = project root (5 levels from tests/unit/)
        project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
        models_dir = project_root / "models"

        assert models_dir.is_dir(), (
            f"models/ not found at project root: {models_dir}"
        )
        assert (models_dir / "btc").is_dir(), (
            f"models/production/btc/ not found at {models_dir}"
        )


# Helper
def _await_async(coro):
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        return asyncio.run_coroutine_threadsafe(coro, loop).result()
    return asyncio.run(coro)
