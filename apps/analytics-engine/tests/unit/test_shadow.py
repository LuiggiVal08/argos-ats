"""Tests for the Shadow Mode module: producer, outcome worker, metrics."""

import json

import pytest
import time

from app.infrastructure.shadow.shadow_metrics import compute_shadow_metrics
from app.infrastructure.shadow.shadow_producer import make_decision_id


class TestDecisionId:
    def test_deterministic(self) -> None:
        id1 = make_decision_id("BTC/USDT", 1000000, "BUY")
        id2 = make_decision_id("BTC/USDT", 1000000, "BUY")
        assert id1 == id2

    def test_different_action_changes_id(self) -> None:
        buy_id = make_decision_id("BTC/USDT", 1000000, "BUY")
        sell_id = make_decision_id("BTC/USDT", 1000000, "SELL")
        assert buy_id != sell_id

    def test_different_symbol_changes_id(self) -> None:
        btc = make_decision_id("BTC/USDT", 1000000, "BUY")
        eth = make_decision_id("ETH/USDT", 1000000, "BUY")
        assert btc != eth

    def test_different_ts_changes_id(self) -> None:
        ts1 = make_decision_id("BTC/USDT", 1000000, "BUY")
        ts2 = make_decision_id("BTC/USDT", 2000000, "BUY")
        assert ts1 != ts2

    def test_output_length(self) -> None:
        did = make_decision_id("BTC/USDT", 1000000, "BUY")
        assert len(did) == 16


class TestShadowMetrics:
    def test_empty_outcomes(self) -> None:
        result = compute_shadow_metrics([])
        assert result["error"] == "no_outcomes"
        assert result["total"] == 0

    def test_all_wins(self) -> None:
        outcomes = [
            {"close_pnl_pct": 1.0, "r_multiple": 2.0, "action": "BUY", "regime": "trending", "mae_pct": -0.3, "mfe_pct": 1.5, "candle_ts": 1000},
            {"close_pnl_pct": 0.5, "r_multiple": 1.0, "action": "BUY", "regime": "trending", "mae_pct": -0.2, "mfe_pct": 0.8, "candle_ts": 2000},
        ]
        result = compute_shadow_metrics(outcomes)
        assert result["total_outcomes"] == 2
        assert result["accuracy_directional"] == 1.0
        assert result["win_rate"] == 1.0
        assert result["profit_factor"] == float("inf")

    def test_mixed_wins_losses(self) -> None:
        outcomes = [
            {"close_pnl_pct": 2.0, "r_multiple": 3.0, "action": "BUY", "regime": "trending", "mae_pct": -0.5, "mfe_pct": 2.5, "candle_ts": 1000},
            {"close_pnl_pct": -1.0, "r_multiple": -1.5, "action": "BUY", "regime": "ranging", "mae_pct": -1.5, "mfe_pct": 0.3, "candle_ts": 2000},
            {"close_pnl_pct": 1.5, "r_multiple": 2.2, "action": "SELL", "regime": "trending", "mae_pct": -0.4, "mfe_pct": 2.0, "candle_ts": 3000},
        ]
        result = compute_shadow_metrics(outcomes)
        assert result["total_outcomes"] == 3
        assert result["accuracy_directional"] == pytest.approx(2 / 3, abs=0.001)
        assert result["win_rate"] == pytest.approx(2 / 3, abs=0.001)
        assert result["profit_factor"] is not None

    def test_regime_stability(self) -> None:
        outcomes = [
            {"close_pnl_pct": 2.0, "r_multiple": 3.0, "action": "BUY", "regime": "trending", "mae_pct": -0.5, "mfe_pct": 2.5, "candle_ts": 1000},
            {"close_pnl_pct": -1.5, "r_multiple": -2.0, "action": "BUY", "regime": "ranging", "mae_pct": -2.0, "mfe_pct": 0.2, "candle_ts": 2000},
            {"close_pnl_pct": -1.0, "r_multiple": -1.5, "action": "BUY", "regime": "ranging", "mae_pct": -1.5, "mfe_pct": 0.3, "candle_ts": 3000},
        ]
        result = compute_shadow_metrics(outcomes)
        assert "trending" in result["regime_expectancies"]
        assert "ranging" in result["regime_expectancies"]
        assert result["regime_stability_pct"] >= 0

    def test_direction_correct_buy_win(self) -> None:
        outcomes = [
            {"close_pnl_pct": 1.0, "r_multiple": 1.0, "action": "BUY", "regime": "trending", "mae_pct": -0.2, "mfe_pct": 1.2, "candle_ts": 1000},
        ]
        result = compute_shadow_metrics(outcomes)
        assert result["accuracy_directional"] == 1.0

    def test_direction_correct_sell_win(self) -> None:
        outcomes = [
            {"close_pnl_pct": 1.0, "r_multiple": 1.0, "action": "SELL", "regime": "trending", "mae_pct": -0.2, "mfe_pct": 1.2, "candle_ts": 1000},
        ]
        result = compute_shadow_metrics(outcomes)
        assert result["accuracy_directional"] == 1.0

    def test_direction_wrong(self) -> None:
        outcomes = [
            {"close_pnl_pct": -1.0, "r_multiple": -1.0, "action": "BUY", "regime": "ranging", "mae_pct": -1.5, "mfe_pct": 0.1, "candle_ts": 1000},
        ]
        result = compute_shadow_metrics(outcomes)
        assert result["accuracy_directional"] == 0.0

    def test_action_distribution(self) -> None:
        outcomes = [
            {"close_pnl_pct": 0.5, "r_multiple": 0.5, "action": "BUY", "regime": "trending", "mae_pct": -0.1, "mfe_pct": 0.6, "candle_ts": 1000},
            {"close_pnl_pct": -0.3, "r_multiple": -0.3, "action": "BUY", "regime": "ranging", "mae_pct": -0.5, "mfe_pct": 0.1, "candle_ts": 2000},
            {"close_pnl_pct": 1.0, "r_multiple": 1.0, "action": "SELL", "regime": "trending", "mae_pct": -0.2, "mfe_pct": 1.2, "candle_ts": 3000},
        ]
        result = compute_shadow_metrics(outcomes)
        assert result["action_distribution"]["BUY"] == 2
        assert result["action_distribution"]["SELL"] == 1


class TestShadowMetricsEdgeCases:
    def test_single_outcome(self) -> None:
        outcomes = [
            {"close_pnl_pct": 0.1, "r_multiple": 0.1, "action": "BUY", "regime": "trending", "mae_pct": 0.0, "mfe_pct": 0.1, "candle_ts": 1000},
        ]
        result = compute_shadow_metrics(outcomes)
        assert result["total_outcomes"] == 1
        assert result["expectancy_pct"] == 0.1

    def test_large_set(self) -> None:
        outcomes = []
        for i in range(100):
            outcomes.append({
                "close_pnl_pct": 0.5 if i % 2 == 0 else -0.3,
                "r_multiple": 1.0 if i % 2 == 0 else -0.5,
                "action": "BUY" if i % 2 == 0 else "SELL",
                "regime": "trending",
                "mae_pct": -0.1,
                "mfe_pct": 0.6,
                "candle_ts": i * 3600_000,
            })
        result = compute_shadow_metrics(outcomes)
        assert result["total_outcomes"] == 100
        assert result["win_rate"] == 0.5
        assert result["accuracy_directional"] == 0.5
