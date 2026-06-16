"""Unit tests for NovaQuant domain value objects."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.domain.value_objects.model_config import ModelConfig
from app.domain.value_objects.signal_side import SignalSide
from app.domain.value_objects.trading_signal import TradingSignal


class TestSignalSide:
    def test_enum_values(self) -> None:
        assert SignalSide.BUY.value == "BUY"
        assert SignalSide.SELL.value == "SELL"
        assert SignalSide.HOLD.value == "HOLD"

    def test_enum_is_string(self) -> None:
        assert isinstance(SignalSide.BUY, str)


class TestTradingSignal:
    def test_valid_creation(self) -> None:
        ts = TradingSignal(side=SignalSide.BUY, confidence=0.85)
        assert ts.side == SignalSide.BUY
        assert ts.confidence == 0.85
        assert isinstance(ts.timestamp, datetime)

    def test_invalid_confidence_too_low(self) -> None:
        with pytest.raises(ValueError, match="confidence must be in"):
            TradingSignal(side=SignalSide.BUY, confidence=-0.1)

    def test_invalid_confidence_too_high(self) -> None:
        with pytest.raises(ValueError, match="confidence must be in"):
            TradingSignal(side=SignalSide.BUY, confidence=1.5)

    def test_boundary_confidence(self) -> None:
        ts0 = TradingSignal(side=SignalSide.BUY, confidence=0.0)
        assert ts0.confidence == 0.0
        ts1 = TradingSignal(side=SignalSide.BUY, confidence=1.0)
        assert ts1.confidence == 1.0

    def test_is_actionable_buy_above_threshold(self) -> None:
        ts = TradingSignal(side=SignalSide.BUY, confidence=0.85)
        assert ts.is_actionable() is True
        assert ts.is_actionable(threshold=0.9) is False

    def test_is_actionable_sell_above_threshold(self) -> None:
        ts = TradingSignal(side=SignalSide.SELL, confidence=0.75)
        assert ts.is_actionable() is True

    def test_is_actionable_hold_never(self) -> None:
        ts = TradingSignal(side=SignalSide.HOLD, confidence=0.99)
        assert ts.is_actionable() is False

    def test_custom_threshold(self) -> None:
        ts = TradingSignal(side=SignalSide.BUY, confidence=0.65)
        assert ts.is_actionable(threshold=0.6) is True
        assert ts.is_actionable(threshold=0.7) is False

    def test_timestamp_default_is_utc(self) -> None:
        ts = TradingSignal(side=SignalSide.BUY, confidence=0.5)
        assert ts.timestamp.tzinfo == timezone.utc

    def test_immutable(self) -> None:
        ts = TradingSignal(side=SignalSide.BUY, confidence=0.5)
        with pytest.raises(AttributeError):
            ts.confidence = 0.9  # type: ignore[misc]

    def test_metadata_default_empty(self) -> None:
        ts = TradingSignal(side=SignalSide.BUY, confidence=0.5)
        assert ts.metadata == {}

    def test_metadata_passthrough(self) -> None:
        meta = {"features": ["rsi_14", "macd"], "model": "v1"}
        ts = TradingSignal(side=SignalSide.SELL, confidence=0.8, metadata=meta)
        assert ts.metadata == meta


class TestModelConfig:
    def test_default_values(self) -> None:
        cfg = ModelConfig()
        assert cfg.lookback == 60
        assert cfg.confidence_threshold == 0.7
        assert cfg.layers == (128, 64, 32, 16)
        assert len(cfg.features) == 20
        assert cfg.target_lookahead == 5
        assert cfg.target_return_pct == 1.0
        assert cfg.dropout_rate == 0.2
        assert cfg.batch_size == 32
        assert cfg.max_epochs == 200
        assert cfg.early_stop_patience == 10

    def test_custom_values(self) -> None:
        cfg = ModelConfig(
            lookback=30,
            confidence_threshold=0.8,
            layers=(64, 32, 8),
            features=("rsi_14", "macd"),
            target_lookahead=3,
            target_return_pct=2.0,
            dropout_rate=0.1,
            batch_size=64,
            max_epochs=100,
            early_stop_patience=5,
        )
        assert cfg.lookback == 30
        assert cfg.confidence_threshold == 0.8
        assert cfg.layers == (64, 32, 8)
        assert cfg.features == ("rsi_14", "macd")
        assert cfg.batch_size == 64

    def test_lookback_too_low(self) -> None:
        with pytest.raises(ValueError, match="lookback must be in"):
            ModelConfig(lookback=5)

    def test_lookback_too_high(self) -> None:
        with pytest.raises(ValueError, match="lookback must be in"):
            ModelConfig(lookback=501)

    def test_confidence_too_low(self) -> None:
        with pytest.raises(ValueError, match="confidence_threshold"):
            ModelConfig(confidence_threshold=0.4)

    def test_confidence_too_high(self) -> None:
        with pytest.raises(ValueError, match="confidence_threshold"):
            ModelConfig(confidence_threshold=1.1)

    def test_layers_too_few(self) -> None:
        with pytest.raises(ValueError, match="at least 2 layers"):
            ModelConfig(layers=(128,))

    def test_empty_features(self) -> None:
        with pytest.raises(ValueError, match="features list cannot be empty"):
            ModelConfig(features=())

    def test_target_lookahead_zero(self) -> None:
        with pytest.raises(ValueError, match="target_lookahead"):
            ModelConfig(target_lookahead=0)

    def test_target_return_too_high(self) -> None:
        with pytest.raises(ValueError, match="target_return_pct"):
            ModelConfig(target_return_pct=101)

    def test_dropout_too_high(self) -> None:
        with pytest.raises(ValueError, match="dropout_rate"):
            ModelConfig(dropout_rate=0.6)

    def test_immutable(self) -> None:
        cfg = ModelConfig()
        with pytest.raises(AttributeError):
            cfg.lookback = 30  # type: ignore[misc]
