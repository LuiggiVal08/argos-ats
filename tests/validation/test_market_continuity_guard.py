from __future__ import annotations

import time

import pytest

from app.domain.recovery.market_continuity_guard import MarketContinuityGuard


class TestMarketContinuityGuard:
    def test_no_gap_on_steady_ticks(self):
        mcg = MarketContinuityGuard(gap_threshold_s=60.0)
        now = time.time()
        mcg.record_tick(now - 10)
        mcg.record_tick(now - 5)
        mcg.record_tick(now)
        assert not mcg.market_data_gap

    def test_gap_detected_when_elapsed_exceeds_threshold(self):
        mcg = MarketContinuityGuard(gap_threshold_s=30.0)
        now = time.time()
        mcg.record_tick(now - 100)
        mcg.record_tick(now - 60)
        # gap: 40s > 30s threshold
        mcg.record_tick(now - 20)
        assert mcg.market_data_gap

    def test_gap_clears_after_recovery_ticks(self):
        mcg = MarketContinuityGuard(gap_threshold_s=30.0, recovery_ticks=3)
        now = time.time()
        mcg.record_tick(now - 100)  # t1
        mcg.record_tick(now - 60)   # t2: gap from t1 (40s > 30s) → GAP, tick=1
        assert mcg.market_data_gap
        assert mcg.total_gaps == 1

        mcg.record_tick(now - 55)   # t3: within threshold, tick=2 (< 3)
        assert mcg.market_data_gap

        mcg.record_tick(now - 50)   # t4: within threshold, tick=3 (>= 3) → RESOLVED
        assert not mcg.market_data_gap

    def test_duration_tracks_gap_length(self):
        mcg = MarketContinuityGuard(gap_threshold_s=10.0, recovery_ticks=3)
        now = time.time()
        mcg.record_tick(now - 30)  # t1
        mcg.record_tick(now - 15)  # t2: gap 15s > 10s → GAP, tick=1
        assert mcg.market_data_gap
        dur = mcg.gap_duration_s
        assert dur > 0

        mcg.record_tick(now - 10)  # t3: recovery tick=2 (< 3)
        assert mcg.market_data_gap

        mcg.record_tick(now - 5)   # t4: recovery tick=3 (>= 3) → RESOLVED
        assert not mcg.market_data_gap
        assert mcg.gap_duration_s > 0

    def test_multiple_gaps_counted(self):
        mcg = MarketContinuityGuard(gap_threshold_s=10.0, recovery_ticks=3)
        now = time.time()

        mcg.record_tick(now - 50)   # t1
        mcg.record_tick(now - 35)   # t2: gap 15s → GAP #1, tick=1
        assert mcg.total_gaps == 1

        mcg.record_tick(now - 30)   # t3: recovery tick=2 (< 3)
        assert mcg.market_data_gap

        mcg.record_tick(now - 25)   # t4: recovery tick=3 (>= 3) → resolved
        assert not mcg.market_data_gap

        mcg.record_tick(now - 20)   # t5
        mcg.record_tick(now - 5)    # t6: gap 15s → GAP #2
        assert mcg.total_gaps == 2

    def test_reset_clears_state(self):
        mcg = MarketContinuityGuard(gap_threshold_s=10.0)
        now = time.time()
        mcg.record_tick(now - 30)
        mcg.record_tick(now - 15)  # gap
        assert mcg.market_data_gap

        mcg.reset()
        assert not mcg.market_data_gap
        assert mcg.last_tick_age_s == 0.0

    def test_snapshot_returns_dict(self):
        mcg = MarketContinuityGuard(gap_threshold_s=30.0)
        now = time.time()
        mcg.record_tick(now - 60)
        snap = mcg.snapshot()
        assert isinstance(snap, dict)
        assert "market_data_gap" in snap
        assert "gap_duration_s" in snap
        assert "last_tick_age_s" in snap
        assert "consecutive_ticks" in snap
        assert "total_gaps" in snap

    def test_no_tick_ages_zero(self):
        mcg = MarketContinuityGuard()
        assert mcg.last_tick_age_s == 0.0
        assert not mcg.market_data_gap
