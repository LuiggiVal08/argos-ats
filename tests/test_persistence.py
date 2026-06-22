"""Tests for the Data Persistence Layer (Clean Architecture).

Covers DTOs, JSONL repositories, and forward_test integration.
All tests are file-based (no DB, no Redis, no I/O beyond temp files).
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime

import pytest

from infrastructure.persistence.dto.trade_dto import TradeDTO
from infrastructure.persistence.dto.episode_dto import EpisodeDTO
from infrastructure.persistence.dto.tensor_dto import EdgeTensorDTO, EdgeComponentDTO
from infrastructure.persistence.file_impl.jsonl_trade_repository import JsonlTradeRepository
from infrastructure.persistence.file_impl.jsonl_episode_repository import JsonlEpisodeRepository
from infrastructure.persistence.file_impl.jsonl_tensor_repository import JsonlTensorRepository


# ═══════════════════════════════════════════════════════════════
# DTO Tests
# ═══════════════════════════════════════════════════════════════

class TestTradeDTO:
    def test_minimal_creation(self):
        t = TradeDTO(
            symbol="BTC", entry_ts="2024-01-01T00:00", exit_ts="2024-01-01T01:00",
            side="LONG", entry_price=100.0, exit_price=110.0, size=1000.0,
            gross_pnl=100.0, costs=3.1, net_pnl=96.9, duration_bars=5,
            exit_reason="time_exit",
        )
        assert t.symbol == "BTC"
        assert t.net_pnl == 96.9

    def test_frozen(self):
        t = TradeDTO(
            symbol="BTC", entry_ts="", exit_ts="",
            side="LONG", entry_price=0, exit_price=0, size=0,
            gross_pnl=0, costs=0, net_pnl=0, duration_bars=0,
            exit_reason="",
        )
        with pytest.raises(AttributeError):
            t.symbol = "ETH"

    def test_default_experiment_id(self):
        t = TradeDTO(
            symbol="BTC", entry_ts="", exit_ts="",
            side="LONG", entry_price=0, exit_price=0, size=0,
            gross_pnl=0, costs=0, net_pnl=0, duration_bars=0,
            exit_reason="",
        )
        assert t.experiment_id == ""

    def test_experiment_id_custom(self):
        t = TradeDTO(
            symbol="BTC", entry_ts="", exit_ts="",
            side="LONG", entry_price=0, exit_price=0, size=0,
            gross_pnl=0, costs=0, net_pnl=0, duration_bars=0,
            exit_reason="", experiment_id="phase15",
        )
        assert t.experiment_id == "phase15"


class TestEpisodeDTO:
    def test_minimal_creation(self):
        e = EpisodeDTO(episode_id="abc123", t_entry="2024-01-01T00:00", side="LONG")
        assert e.episode_id == "abc123"
        assert e.t_exit is None
        assert e.pnl == 0.0

    def test_frozen(self):
        e = EpisodeDTO(episode_id="x", t_entry="")
        with pytest.raises(AttributeError):
            e.episode_id = "y"

    def test_settled_episode(self):
        e = EpisodeDTO(
            episode_id="abc123", t_entry="2024-01-01T00:00",
            t_exit="2024-01-01T01:00", side="LONG", pnl=96.9,
        )
        assert e.t_exit == "2024-01-01T01:00"
        assert e.pnl == 96.9


class TestEdgeTensorDTO:
    def test_minimal_creation(self):
        et = EdgeTensorDTO(experiment_id="BTC", timestamp="2024-01-01T12:00")
        assert et.experiment_id == "BTC"
        assert et.directional.mean == 0.0
        assert et.directional.identifiable is False

    def test_frozen(self):
        et = EdgeTensorDTO(experiment_id="x", timestamp="")
        with pytest.raises(AttributeError):
            et.experiment_id = "y"

    def test_with_components(self):
        et = EdgeTensorDTO(
            experiment_id="BTC", timestamp="2024-01-01T12:00",
            symbol="BTC", n_trades=10,
            directional=EdgeComponentDTO(mean=0.65, std=0.1, ci_lower=0.45, ci_upper=0.85, identifiable=True),
        )
        assert et.directional.mean == 0.65
        assert et.directional.identifiable is True
        assert et.n_trades == 10

    def test_edge_component_defaults(self):
        c = EdgeComponentDTO()
        assert c.mean == 0.0
        assert c.std == 0.0
        assert c.identifiable is False


# ═══════════════════════════════════════════════════════════════
# JSONL Repository Tests
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def tmp_jsonl():
    """Yield a temporary directory path for JSONL files."""
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


class TestJsonlTradeRepository:
    def test_append_and_replay(self, tmp_jsonl):
        path = os.path.join(tmp_jsonl, "trades.jsonl")
        repo = JsonlTradeRepository(path)

        repo.append_trade(TradeDTO(
            symbol="BTC", entry_ts="2024-01-01T00:00", exit_ts="2024-01-01T01:00",
            side="LONG", entry_price=100.0, exit_price=110.0, size=1000.0,
            gross_pnl=100.0, costs=3.1, net_pnl=96.9, duration_bars=5,
            exit_reason="time_exit",
        ))
        assert len(repo.replay()) == 1

        repo.append_trade(TradeDTO(
            symbol="ETH", entry_ts="2024-01-02T00:00", exit_ts="2024-01-02T01:00",
            side="SHORT", entry_price=2000.0, exit_price=1900.0, size=500.0,
            gross_pnl=50.0, costs=1.55, net_pnl=48.45, duration_bars=3,
            exit_reason="flip",
        ))
        trades = repo.replay()
        assert len(trades) == 2
        assert trades[0].symbol == "BTC"
        assert trades[1].symbol == "ETH"

    def test_replay_empty_file(self, tmp_jsonl):
        path = os.path.join(tmp_jsonl, "empty.jsonl")
        repo = JsonlTradeRepository(path)
        assert repo.replay() == []

    def test_get_trades_by_symbol_and_range(self, tmp_jsonl):
        path = os.path.join(tmp_jsonl, "trades.jsonl")
        repo = JsonlTradeRepository(path)

        repo.append_trade(TradeDTO(
            symbol="BTC", entry_ts="2024-01-01T00:00", exit_ts="2024-01-01T01:00",
            side="LONG", entry_price=100.0, exit_price=110.0, size=1000.0,
            gross_pnl=100.0, costs=3.1, net_pnl=96.9, duration_bars=5,
            exit_reason="time_exit",
        ))
        repo.append_trade(TradeDTO(
            symbol="ETH", entry_ts="2024-06-01T00:00", exit_ts="2024-06-01T01:00",
            side="SHORT", entry_price=2000.0, exit_price=1900.0, size=500.0,
            gross_pnl=50.0, costs=1.55, net_pnl=48.45, duration_bars=3,
            exit_reason="flip",
        ))

        btc_trades = repo.get_trades("BTC", datetime(2024, 1, 1), datetime(2024, 12, 31))
        assert len(btc_trades) == 1
        assert btc_trades[0].symbol == "BTC"

        eth_trades = repo.get_trades("ETH", datetime(2024, 1, 1), datetime(2024, 12, 31))
        assert len(eth_trades) == 1

        no_trades = repo.get_trades("SOL", datetime(2024, 1, 1), datetime(2024, 12, 31))
        assert len(no_trades) == 0

    def test_deterministic_replay_order(self, tmp_jsonl):
        """Replay must return trades in insertion order."""
        path = os.path.join(tmp_jsonl, "trades.jsonl")
        repo = JsonlTradeRepository(path)

        for i in range(5):
            repo.append_trade(TradeDTO(
                symbol="BTC", entry_ts=f"2024-01-{i+1:02d}T00:00",
                exit_ts=f"2024-01-{i+1:02d}T01:00",
                side="LONG", entry_price=100.0 + i, exit_price=110.0 + i,
                size=1000.0, gross_pnl=10.0, costs=3.1, net_pnl=6.9,
                duration_bars=5, exit_reason="time_exit",
            ))

        trades = repo.replay()
        assert len(trades) == 5
        for i, t in enumerate(trades):
            assert f"2024-01-{i+1:02d}T00:00" in t.entry_ts


class TestJsonlEpisodeRepository:
    def test_append_and_list_open(self, tmp_jsonl):
        path = os.path.join(tmp_jsonl, "episodes.jsonl")
        repo = JsonlEpisodeRepository(path)

        e1 = EpisodeDTO(episode_id="ep1", t_entry="2024-01-01T00:00", side="LONG", experiment_id="BTC")
        e2 = EpisodeDTO(episode_id="ep2", t_entry="2024-01-02T00:00", side="SHORT", experiment_id="ETH")
        repo.append_episode(e1)
        repo.append_episode(e2)

        open_eps = repo.get_open_episodes()
        assert len(open_eps) == 2

    def test_settle_episode(self, tmp_jsonl):
        path = os.path.join(tmp_jsonl, "episodes.jsonl")
        repo = JsonlEpisodeRepository(path)

        repo.append_episode(EpisodeDTO(
            episode_id="ep1", t_entry="2024-01-01T00:00", side="LONG", experiment_id="BTC",
        ))
        repo.settle_episode("ep1", 96.9, "2024-01-01T01:00")

        open_eps = repo.get_open_episodes()
        assert len(open_eps) == 0

        settled = repo.get_settled_episodes()
        assert len(settled) == 1
        assert settled[0].pnl == 96.9
        assert settled[0].t_exit == "2024-01-01T01:00"

    def test_settle_nonexistent_raises(self, tmp_jsonl):
        path = os.path.join(tmp_jsonl, "episodes.jsonl")
        repo = JsonlEpisodeRepository(path)

        with pytest.raises(ValueError, match="not found"):
            repo.settle_episode("nonexistent", 0.0, "2024-01-01")

    def test_replay_in_order(self, tmp_jsonl):
        path = os.path.join(tmp_jsonl, "episodes.jsonl")
        repo = JsonlEpisodeRepository(path)

        for i in range(3):
            repo.append_episode(EpisodeDTO(
                episode_id=f"ep{i}", t_entry=f"2024-01-{i+1:02d}T00:00",
                side="LONG", experiment_id="BTC",
            ))

        eps = repo.replay()
        assert len(eps) == 3
        for i, e in enumerate(eps):
            assert e.episode_id == f"ep{i}"

    def test_replay_empty(self, tmp_jsonl):
        path = os.path.join(tmp_jsonl, "episodes.jsonl")
        repo = JsonlEpisodeRepository(path)
        assert repo.replay() == []


class TestJsonlTensorRepository:
    def test_store_and_get_series(self, tmp_jsonl):
        path = os.path.join(tmp_jsonl, "tensors.jsonl")
        repo = JsonlTensorRepository(path)

        repo.store_tensor(EdgeTensorDTO(
            experiment_id="BTC", timestamp="2024-01-01T12:00",
            symbol="BTC", n_trades=10,
        ))
        repo.store_tensor(EdgeTensorDTO(
            experiment_id="BTC", timestamp="2024-01-02T12:00",
            symbol="BTC", n_trades=20,
        ))

        series = repo.get_tensor_series("BTC")
        assert len(series) == 2
        assert series[0].n_trades == 10
        assert series[1].n_trades == 20

    def test_get_series_empty(self, tmp_jsonl):
        path = os.path.join(tmp_jsonl, "tensors.jsonl")
        repo = JsonlTensorRepository(path)
        assert repo.get_tensor_series("NONEXISTENT") == []

    def test_replay(self, tmp_jsonl):
        path = os.path.join(tmp_jsonl, "tensors.jsonl")
        repo = JsonlTensorRepository(path)

        repo.store_tensor(EdgeTensorDTO(
            experiment_id="BTC", timestamp="2024-01-01T12:00",
            symbol="BTC", n_trades=10,
            directional=EdgeComponentDTO(mean=0.65, identifiable=True),
        ))

        tensors = repo.replay()
        assert len(tensors) == 1
        assert tensors[0].directional.mean == 0.65
        assert tensors[0].directional.identifiable is True

    def test_replay_empty(self, tmp_jsonl):
        path = os.path.join(tmp_jsonl, "tensors.jsonl")
        repo = JsonlTensorRepository(path)
        assert repo.replay() == []
