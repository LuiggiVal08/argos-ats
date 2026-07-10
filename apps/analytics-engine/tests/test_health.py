"""Tests for the /health endpoint warmup contract."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_ok(skip_startup_warmup: None) -> None:
    """After warmup, /health returns status 'ok'."""
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["uptime_s"] >= 30


def test_health_starting_up() -> None:
    """Immediately after boot, /health returns status 'starting_up'."""
    import app.main as _main

    original = _main._boot_timestamp
    try:
        _main._boot_timestamp = time.monotonic()
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "starting_up"
    finally:
        _main._boot_timestamp = original
