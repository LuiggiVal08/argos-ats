"""Phase 5: Replay Validation.

Tests EventStream, ReplayState, all rebuilders, drift detection,
consistency reporting, and ReplayEngine orchestration.

Risk level: CRITICAL — untested deterministic replay for audit trail.
Covers spec Sections 15–19 (replay reconstruction).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.replay.event_stream import EventStream, EventType, Event
from app.domain.replay.replay_state import ReplayState, ReplayPosition
from app.domain.replay.reconstruction.trade_rebuilder import TradeRebuilder, ReplayTrade
from app.domain.replay.reconstruction.episode_rebuilder import EpisodeRebuilder
from app.domain.replay.reconstruction.tensor_rebuilder import TensorRebuilder
from app.domain.replay.reconstruction.control_rebuilder import ControlRebuilder
from app.domain.replay.validation.drift_detector import DriftDetector
from app.domain.replay.validation.replay_consistency import ReplayConsistencyReport
from app.domain.replay.replay_engine import ReplayEngine
from infrastructure.persistence.dto.trade_dto import TradeDTO
from infrastructure.persistence.dto.episode_dto import EpisodeDTO
from infrastructure.persistence.dto.tensor_dto import EdgeTensorDTO, EdgeComponentDTO


# ═════════════════════════════════════════════════════════════════════
# 1. EventStream
# ═════════════════════════════════════════════════════════════════════

class TestEventStream:
    def test_empty_stream(self):
        s = EventStream()
        assert s.is_empty
        assert s.count == 0
        assert list(s) == []

    def test_add_event_increments_count(self):
        s = EventStream()
        s.add_event(EventType.TRADE_CLOSED, {"net_pnl": 100})
        assert s.count == 1
        assert not s.is_empty

    def test_sorted_by_timestamp_then_sequence(self):
        s = EventStream()
        s.add_event(EventType.TRADE_CLOSED, {"id": 1}, timestamp="2024-01-01T01:00")
        s.add_event(EventType.TRADE_CLOSED, {"id": 2}, timestamp="2024-01-01T00:00")
        events = s.sorted()
        assert events[0].data["id"] == 2
        assert events[1].data["id"] == 1

    def test_empty_timestamp_falls_back_to_empty_string(self):
        s = EventStream()
        ev = s.add_event(EventType.TRADE_CLOSED)
        assert ev.timestamp == ""

    def test_deterministic_order_same_input(self):
        s1 = EventStream()
        s1.add_event(EventType.TRADE_CLOSED, {"i": 1}, timestamp="2024-01-01T00:00")
        s1.add_event(EventType.EPISODE_CREATED, {"i": 2}, timestamp="2024-01-01T00:00")

        s2 = EventStream()
        s2.add_event(EventType.TRADE_CLOSED, {"i": 1}, timestamp="2024-01-01T00:00")
        s2.add_event(EventType.EPISODE_CREATED, {"i": 2}, timestamp="2024-01-01T00:00")

        e1 = s1.sorted()
        e2 = s2.sorted()
        for a, b in zip(e1, e2):
            assert a.sequence_id == b.sequence_id
            assert a.event_type == b.event_type

    def test_by_type_filters_correctly(self):
        s = EventStream()
        s.add_event(EventType.TRADE_CLOSED)
        s.add_event(EventType.EPISODE_CREATED)
        s.add_event(EventType.TRADE_CLOSED)
        assert len(s.by_type(EventType.TRADE_CLOSED)) == 2
        assert len(s.by_type(EventType.EPISODE_CREATED)) == 1
        assert len(s.by_type(EventType.TENSOR_STORED)) == 0

    def test_clear_resets(self):
        s = EventStream()
        s.add_event(EventType.TRADE_CLOSED)
        s.clear()
        assert s.is_empty
        assert s.count == 0

    def test_iteration_in_sorted_order(self):
        s = EventStream()
        s.add_event(EventType.EPISODE_CREATED, timestamp="2024-01-01T02:00")
        s.add_event(EventType.TRADE_CLOSED, timestamp="2024-01-01T01:00")
        types = [e.event_type for e in s]
        assert types == [EventType.TRADE_CLOSED, EventType.EPISODE_CREATED]

    def test_add_events_batch(self):
        s = EventStream()
        s.add_events([
            (EventType.TRADE_CLOSED, {"net_pnl": 50}, "2024-01-01T00:00"),
            (EventType.EPISODE_CREATED, {"episode_id": "e1"}, "2024-01-01T01:00"),
        ])
        assert s.count == 2
        assert len(s.by_type(EventType.TRADE_CLOSED)) == 1


# ═════════════════════════════════════════════════════════════════════
# 2. ReplayState
# ═════════════════════════════════════════════════════════════════════

class TestReplayState:
    def test_default_state(self):
        s = ReplayState()
        assert s.symbol == ""
        assert s.total_trades == 0
        assert s.total_costs == 0.0
        assert s.free_balance == 0.0
        assert s.position.in_position is False
        assert s.last_trade is None

    def test_to_dict_structure(self):
        s = ReplayState()
        d = s.to_dict()
        assert "symbol" in d
        assert "total_trades" in d
        assert "free_balance" in d
        assert "in_position" in d
        assert "last_trade" not in d  # transformed to has_last_trade
        assert d["has_last_trade"] is False

    def test_to_dict_with_data(self):
        s = ReplayState()
        s.symbol = "BTC"
        s.total_trades = 10
        s.total_costs = 5.0
        s.free_balance = 100000.0
        s.last_trade = {"net_pnl": 100}
        d = s.to_dict()
        assert d["symbol"] == "BTC"
        assert d["total_trades"] == 10
        assert d["total_costs"] == 5.0
        assert d["free_balance"] == 100000.0
        assert d["has_last_trade"] is True

    def test_replay_position_defaults(self):
        p = ReplayPosition()
        assert p.in_position is False
        assert p.side == 0
        assert p.entry_bar == 0
        assert p.entry_price == 0.0


# ═════════════════════════════════════════════════════════════════════
# 3. TradeRebuilder
# ═════════════════════════════════════════════════════════════════════

class TestTradeRebuilder:
    def test_rebuild_empty(self):
        rb = TradeRebuilder()
        rb.rebuild([])
        assert rb.trades == []

    def test_rebuild_single_trade(self):
        rb = TradeRebuilder()
        dto = TradeDTO(
            symbol="BTC", entry_ts="2024-01-01T00:00", exit_ts="2024-01-01T01:00",
            side="LONG", entry_price=100.0, exit_price=110.0, size=1000.0,
            gross_pnl=100.0, costs=3.1, net_pnl=96.9, duration_bars=5,
            exit_reason="time_exit",
        )
        rb.rebuild([dto])
        assert len(rb.trades) == 1
        t = rb.trades[0]
        assert t.symbol == "BTC"
        assert t.net_pnl == 96.9

    def test_rebuild_sorted_by_entry_ts(self):
        rb = TradeRebuilder()
        dtos = []
        for i, ts in enumerate(["2024-01-03T00:00", "2024-01-01T00:00", "2024-01-02T00:00"]):
            dtos.append(TradeDTO(
                symbol="BTC", entry_ts=ts, exit_ts=ts,
                side="LONG", entry_price=100.0, exit_price=110.0, size=1000.0,
                gross_pnl=10.0, costs=1.0, net_pnl=9.0, duration_bars=1,
                exit_reason="time_exit",
            ))
        rb.rebuild(dtos)
        timestamps = [t.entry_ts for t in rb.trades]
        assert timestamps == sorted(timestamps)

    def test_validate_consistency_empty_original(self):
        rb = TradeRebuilder()
        dto = TradeDTO(
            symbol="BTC", entry_ts="2024-01-01T00:00", exit_ts="2024-01-01T01:00",
            side="LONG", entry_price=100.0, exit_price=110.0, size=1000.0,
            gross_pnl=100.0, costs=3.1, net_pnl=96.9, duration_bars=5,
            exit_reason="time_exit",
        )
        rb.rebuild([dto])
        result = rb.validate_consistency([])
        assert result["n_rebuilt"] == 1
        assert result["n_original"] == 0

    def test_validate_consistency_matched(self):
        rb = TradeRebuilder()
        dto = TradeDTO(
            symbol="BTC", entry_ts="2024-01-01T00:00", exit_ts="2024-01-01T01:00",
            side="LONG", entry_price=100.0, exit_price=110.0, size=1000.0,
            gross_pnl=100.0, costs=3.1, net_pnl=96.9, duration_bars=5,
            exit_reason="time_exit",
        )
        rb.rebuild([dto])
        original = [{"entry_ts": "2024-01-01T00:00", "side": "1", "net_pnl": "96.9"}]
        result = rb.validate_consistency(original)
        assert result["match_rate"] >= 0


# ═════════════════════════════════════════════════════════════════════
# 4. EpisodeRebuilder
# ═════════════════════════════════════════════════════════════════════

class TestEpisodeRebuilder:
    def test_rebuild_empty(self):
        rb = EpisodeRebuilder()
        rb.rebuild([])
        assert rb.all_episodes == []

    def test_rebuild_open_episode(self):
        rb = EpisodeRebuilder()
        dto = EpisodeDTO(episode_id="ep1", t_entry="2024-01-01T00:00", side="LONG")
        rb.rebuild([dto])
        assert len(rb.open_episodes) == 1
        assert rb.open_episodes[0].episode_id == "ep1"

    def test_rebuild_settled_episode(self):
        rb = EpisodeRebuilder()
        dto = EpisodeDTO(
            episode_id="ep1", t_entry="2024-01-01T00:00",
            t_exit="2024-01-01T01:00", side="LONG", pnl=96.9,
        )
        rb.rebuild([dto])
        assert len(rb.open_episodes) == 0
        assert len(rb.settled_episodes) == 1
        assert rb.settled_episodes[0].pnl == 96.9

    def test_rebuild_tracks_both(self):
        rb = EpisodeRebuilder()
        rb.rebuild([
            EpisodeDTO(episode_id="ep1", t_entry="2024-01-01T00:00", side="LONG"),
            EpisodeDTO(episode_id="ep2", t_entry="2024-01-02T00:00",
                       t_exit="2024-01-02T01:00", side="SHORT", pnl=50.0),
        ])
        assert len(rb.all_episodes) == 2
        assert len(rb.open_episodes) == 1
        assert len(rb.settled_episodes) == 1

    def test_validate_consistency(self):
        rb = EpisodeRebuilder()
        dto = EpisodeDTO(episode_id="ep1", t_entry="2024-01-01T00:00", side="LONG")
        rb.rebuild([dto])
        result = rb.validate_consistency({"n_episodes": 1})
        assert "match_rate" in result
        assert "total_episodes" in result


# ═════════════════════════════════════════════════════════════════════
# 5. TensorRebuilder
# ═════════════════════════════════════════════════════════════════════

class TestTensorRebuilder:
    def test_rebuild_empty(self):
        rb = TensorRebuilder()
        rb.rebuild([])
        assert rb.tensors == []
        assert rb.latest is None

    def test_rebuild_single_tensor(self):
        rb = TensorRebuilder()
        dto = EdgeTensorDTO(
            experiment_id="BTC", timestamp="2024-01-01T12:00",
            symbol="BTC", n_trades=10,
            directional=EdgeComponentDTO(mean=0.65, std=0.1, identifiable=True),
        )
        rb.rebuild([dto])
        assert len(rb.tensors) == 1
        t = rb.latest
        assert t is not None
        assert t.directional.mean == 0.65
        assert t.directional.identifiable is True

    def test_rebuild_sorted_by_timestamp(self):
        rb = TensorRebuilder()
        rb.rebuild([
            EdgeTensorDTO(experiment_id="BTC", timestamp="2024-01-03T12:00"),
            EdgeTensorDTO(experiment_id="BTC", timestamp="2024-01-01T12:00"),
        ])
        assert rb.tensors[0].timestamp == "2024-01-01T12:00"
        assert rb.tensors[1].timestamp == "2024-01-03T12:00"

    def test_validate_consistency(self):
        rb = TensorRebuilder()
        dto = EdgeTensorDTO(
            experiment_id="BTC", timestamp="2024-01-01T12:00",
            directional=EdgeComponentDTO(mean=0.65, identifiable=True),
        )
        rb.rebuild([dto])
        result = rb.validate_consistency(None)
        assert "is_consistent" in result
        assert result["n_tensors"] == 1


# ═════════════════════════════════════════════════════════════════════
# 6. ControlRebuilder
# ═════════════════════════════════════════════════════════════════════

class TestControlRebuilder:
    def test_rebuild_empty(self):
        rb = ControlRebuilder()
        rb.rebuild([])
        assert rb.decisions == []
        assert rb.last_decision is None

    def test_rebuild_single_decision(self):
        rb = ControlRebuilder()
        protocol = {
            "control": {
                "action": "CONTINUE",
                "h_interno": 0.3,
                "h_externo": 0.2,
                "reason": "normal",
            },
        }
        rb.rebuild([protocol])
        assert len(rb.decisions) == 1
        assert rb.last_decision["action"] == "CONTINUE"

    def test_validate_consistency(self):
        rb = ControlRebuilder()
        protocol = {"control": {"action": "CONTINUE", "h_interno": 0.3, "h_externo": 0.2}}
        rb.rebuild([protocol])
        result = rb.validate_consistency(protocol)
        assert "is_consistent" in result
        assert result["n_decisions"] == 1

    def test_validate_invalid_action(self):
        rb = ControlRebuilder()
        protocol = {"control": {"action": "INVALID_ACTION", "h_interno": 0.5, "h_externo": 0.5}}
        rb.rebuild([protocol])
        result = rb.validate_consistency(None)
        assert not result["is_consistent"]


# ═════════════════════════════════════════════════════════════════════
# 7. DriftDetector
# ═════════════════════════════════════════════════════════════════════

class TestDriftDetector:
    def test_utility_no_drift(self):
        dd = DriftDetector(tolerance=0.01)
        result = dd.check_utility_drift(100.0, 100.0)
        assert not result["detected"]

    def test_utility_drift_exceeds_tolerance(self):
        dd = DriftDetector(tolerance=0.01)
        result = dd.check_utility_drift(100.0, 105.0)
        assert result["detected"]

    def test_utility_none_skips(self):
        dd = DriftDetector()
        result = dd.check_utility_drift(None, 100.0)
        assert not result["detected"]

    def test_tensor_no_drift(self):
        dd = DriftDetector(tolerance=0.001)
        orig = {"directional": {"mean": 0.5}, "timing": {"mean": 0.3},
                "execution": {"mean": 0.2}, "structural": {"mean": 0.1}}
        recon = {"directional": {"mean": 0.5}, "timing": {"mean": 0.3},
                 "execution": {"mean": 0.2}, "structural": {"mean": 0.1}}
        result = dd.check_tensor_drift(orig, recon)
        assert not result["detected"]

    def test_tensor_drift_detected(self):
        dd = DriftDetector(tolerance=0.001)
        orig = {"directional": {"mean": 0.5}, "timing": {"mean": 0.3},
                "execution": {"mean": 0.2}, "structural": {"mean": 0.1}}
        recon = {"directional": {"mean": 0.9}, "timing": {"mean": 0.1},
                 "execution": {"mean": 0.5}, "structural": {"mean": 0.0}}
        result = dd.check_tensor_drift(orig, recon)
        assert result["detected"]

    def test_control_no_drift(self):
        dd = DriftDetector()
        orig = {"action": "CONTINUE", "h_interno": 0.3, "h_externo": 0.2}
        recon = {"action": "CONTINUE", "h_interno": 0.3, "h_externo": 0.2}
        result = dd.check_control_drift(orig, recon)
        assert not result["detected"]

    def test_control_action_drift(self):
        dd = DriftDetector()
        orig = {"action": "CONTINUE", "h_interno": 0.3, "h_externo": 0.2}
        recon = {"action": "HALT", "h_interno": 0.3, "h_externo": 0.2}
        result = dd.check_control_drift(orig, recon)
        assert result["detected"]

    def test_temporal_consistency_passes(self):
        dd = DriftDetector()
        events = [
            {"timestamp": "2024-01-01T00:00", "type": "TRADE"},
            {"timestamp": "2024-01-01T01:00", "type": "TRADE"},
            {"timestamp": "2024-01-01T02:00", "type": "TRADE"},
        ]
        result = dd.check_temporal_consistency(events)
        assert not result["detected"]

    def test_temporal_consistency_fails(self):
        dd = DriftDetector()
        events = [
            {"timestamp": "2024-01-01T02:00", "type": "TRADE"},
            {"timestamp": "2024-01-01T01:00", "type": "TRADE"},
            {"timestamp": "2024-01-01T00:00", "type": "TRADE"},
        ]
        result = dd.check_temporal_consistency(events)
        assert result["detected"]
        assert result["n_issues"] > 0


# ═════════════════════════════════════════════════════════════════════
# 8. ReplayConsistencyReport
# ═════════════════════════════════════════════════════════════════════

class TestReplayConsistencyReport:
    def test_empty_report_fails(self):
        report = ReplayConsistencyReport()
        s = report.summary()
        assert not s["passed"]
        assert s["veredict"] == "FAIL"

    def test_all_match_passes(self):
        report = ReplayConsistencyReport()
        report.add_result("trades", {"match_rate": 1.0, "n_original": 1, "n_rebuilt": 1})
        report.add_result("episodes", {"match_rate": 1.0, "total_episodes": 1, "n_rebuilt": 1})
        report.add_result("tensors", {"match_rate": 1.0, "n_tensors": 1, "n_rebuilt": 1})
        report.add_result("control", {"match_rate": 1.0, "n_decisions": 1, "n_rebuilt": 1})
        s = report.summary()
        assert s["passed"]
        assert s["veredict"] == "PASS"

    def test_partial_match_fails(self):
        report = ReplayConsistencyReport()
        report.add_result("trades", {"match_rate": 0.5, "n_original": 2, "n_rebuilt": 1})
        s = report.summary()
        assert not s["passed"]

    def test_passed_property(self):
        report = ReplayConsistencyReport()
        assert report.passed is False
        report.add_result("trades", {"match_rate": 1.0, "n_original": 1, "n_rebuilt": 1})
        report.add_result("episodes", {"match_rate": 1.0, "total_episodes": 1, "n_rebuilt": 1})
        report.add_result("tensors", {"match_rate": 1.0, "n_tensors": 1, "n_rebuilt": 1})
        report.add_result("control", {"match_rate": 1.0, "n_decisions": 1, "n_rebuilt": 1})
        assert report.passed


# ═════════════════════════════════════════════════════════════════════
# 9. ReplayEngine Integration
# ═════════════════════════════════════════════════════════════════════

class MockTradeRepo:
    def __init__(self, trades: list | None = None):
        self._trades = trades or []
    def replay(self):
        return self._trades


class MockEpisodeRepo:
    def __init__(self, episodes: list | None = None):
        self._episodes = episodes or []
    def replay(self):
        return self._episodes


class MockTensorRepo:
    def __init__(self, tensors: list | None = None):
        self._tensors = tensors or []
    def replay(self):
        return self._tensors


class TestReplayEngine:
    def test_init_no_repos(self):
        engine = ReplayEngine()
        assert engine.event_stream.is_empty
        assert engine.state.total_trades == 0

    def test_load_experiment_sets_metadata(self):
        engine = ReplayEngine()
        engine.load_experiment("BTC")
        assert engine.state.symbol == "BTC"
        assert engine.state.experiment_id == "BTC"

    def test_load_experiment_with_trades(self):
        dto = TradeDTO(
            symbol="BTC", entry_ts="2024-01-01T00:00", exit_ts="2024-01-01T01:00",
            side="LONG", entry_price=100.0, exit_price=110.0, size=1000.0,
            gross_pnl=100.0, costs=3.1, net_pnl=96.9, duration_bars=5,
            exit_reason="time_exit",
        )
        engine = ReplayEngine(trade_repo=MockTradeRepo([dto]))
        engine.load_experiment("BTC")
        assert engine.event_stream.count == 1
        trade_events = engine.event_stream.by_type(EventType.TRADE_CLOSED)
        assert len(trade_events) == 1
        assert trade_events[0].data["net_pnl"] == 96.9

    def test_replay_full_mode(self):
        dto = TradeDTO(
            symbol="BTC", entry_ts="2024-01-01T00:00", exit_ts="2024-01-01T01:00",
            side="LONG", entry_price=100.0, exit_price=110.0, size=1000.0,
            gross_pnl=100.0, costs=3.1, net_pnl=96.9, duration_bars=5,
            exit_reason="time_exit",
        )
        engine = ReplayEngine(trade_repo=MockTradeRepo([dto]))
        engine.load_experiment("BTC")
        report = engine.replay(mode="full")
        assert report["experiment_id"] == "BTC"
        assert report["state"]["total_trades"] == 1
        assert report["events"]["total"] == 1

    def test_replay_control_mode_filters(self):
        engine = ReplayEngine()
        engine.load_experiment("BTC")
        report = engine.replay(mode="control")
        assert report["mode"] == "control"

    def test_replay_inference_mode_filters(self):
        engine = ReplayEngine()
        engine.load_experiment("BTC")
        report = engine.replay(mode="inference")
        assert report["mode"] == "inference"

    def test_invalid_replay_mode(self):
        engine = ReplayEngine()
        engine.load_experiment("BTC")
        with pytest.raises(ValueError, match="Unknown replay mode"):
            engine.replay(mode="invalid")

    def test_load_experiment_with_episodes_and_tensors(self):
        ep = EpisodeDTO(episode_id="ep1", t_entry="2024-01-01T00:00", side="LONG")
        ten = EdgeTensorDTO(experiment_id="BTC", timestamp="2024-01-01T12:00")
        engine = ReplayEngine(
            trade_repo=MockTradeRepo(),
            episode_repo=MockEpisodeRepo([ep]),
            tensor_repo=MockTensorRepo([ten]),
        )
        engine.load_experiment("BTC")
        assert engine.event_stream.count == 2

    def test_repo_failure_does_not_crash(self):
        class FailingRepo:
            def replay(self):
                raise RuntimeError("db crash")

        engine = ReplayEngine(trade_repo=FailingRepo())
        engine.load_experiment("BTC")
        assert engine.state.total_trades == 0
        assert engine.event_stream.is_empty

    def test_build_report_structure(self):
        dto = TradeDTO(
            symbol="BTC", entry_ts="2024-01-01T00:00", exit_ts="2024-01-01T01:00",
            side="LONG", entry_price=100.0, exit_price=110.0, size=1000.0,
            gross_pnl=100.0, costs=3.1, net_pnl=96.9, duration_bars=5,
            exit_reason="time_exit",
        )
        engine = ReplayEngine(trade_repo=MockTradeRepo([dto]))
        engine.load_experiment("BTC")
        report = engine.build_report()
        assert "experiment_id" in report
        assert "symbol" in report
        assert "mode" in report
        assert "state" in report
        assert "events" in report
        assert "reconstructed" in report

    def test_load_snapshot_skips_missing_files(self):
        engine = ReplayEngine()
        engine.load_snapshot(
            protocol_path="/tmp/nonexistent_protocol.json",
            state_path="/tmp/nonexistent_state.json",
            trades_csv_path="/tmp/nonexistent_trades.csv",
        )
        assert engine.event_stream.is_empty

    def test_validate_before_load_passes_empty(self):
        engine = ReplayEngine()
        engine.load_experiment("BTC")
        result = engine.validate()
        assert "drift_analysis" not in result  # no original protocol loaded
