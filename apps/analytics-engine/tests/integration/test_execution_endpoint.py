"""Tests de integración para endpoints de ejecución (H7)."""
from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.composition import build_composition


@pytest.fixture
def client(monkeypatch, tmp_path) -> Iterator[TestClient]:
    monkeypatch.setenv("ENVIRONMENT_MODE", "BACKTESTING")
    monkeypatch.setenv("ARGOS_ENV_MODE_FILE", str(tmp_path / "env_mode.json"))
    comp = build_composition()
    app.state.composition = comp
    with TestClient(app) as c:
        yield c


class TestExecuteSignalEndpoint:
    def test_execute_buy(self, client: TestClient):
        resp = client.post("/execute/signal", params={
            "side": "BUY",
            "confidence": 0.85,
            "symbol": "BTC/USDT",
            "price": 60000,
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "FILLED"
        assert data["symbol"] == "BTC/USDT"
        assert data["side"] == "BUY"

    def test_execute_sell(self, client: TestClient):
        resp = client.post("/execute/signal", params={
            "side": "SELL",
            "confidence": 0.9,
            "symbol": "ETH/USDT",
            "price": 3000,
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["side"] == "SELL"

    def test_rejects_low_confidence(self, client: TestClient):
        resp = client.post("/execute/signal", params={
            "side": "BUY",
            "confidence": 0.01,
            "symbol": "BTC/USDT",
            "price": 60000,
        })
        assert resp.status_code in (422, 425), f"expected 422 or 425, got {resp.status_code}"

    def test_rejects_invalid_side(self, client: TestClient):
        resp = client.post("/execute/signal", params={
            "side": "INVALID",
            "confidence": 0.8,
            "symbol": "BTC/USDT",
        })
        assert resp.status_code == 422

    def test_with_price(self, client: TestClient):
        resp = client.post("/execute/signal", params={
            "side": "BUY",
            "confidence": 0.8,
            "symbol": "SOL/USDT",
            "price": 150,
        })
        assert resp.status_code == 200


class TestPositionListEndpoint:
    def test_list_open(self, client: TestClient):
        resp = client.get("/execute/position/list")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_list_all(self, client: TestClient):
        resp = client.get("/execute/position/list?all=true")
        assert resp.status_code == 200


class TestExecutionLogEndpoint:
    def test_log(self, client: TestClient):
        resp = client.get("/execute/execution/log")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
