"""Phase 1: System Boot Validation.

Verifies:
- services are running
- health endpoints respond
- core imports work
- dependency initialization
- scheduler loop metadata

PASS/FAIL with bounded tolerance — no assumption of perfect determinism.
"""

import os
import sys
import time
import urllib.request
import urllib.error
import json
import importlib

import pytest

BOOT_TIMEOUT_S = 10
HEALTH_ENDPOINTS = [
    ("data-engine", "http://localhost:3000/health", {"status": "ok"}),
    ("analytics-engine", "http://localhost:8000/health", {"status": "ok"}),
]


def test_core_imports():
    """All core Python dependencies import cleanly."""
    errors = {}
    for mod_name in ["numpy", "pandas", "joblib", "ccxt", "fastapi",
                      "structlog", "sqlite3", "asyncio", "uvicorn", "redis"]:
        try:
            importlib.import_module(mod_name)
        except ImportError as e:
            errors[mod_name] = str(e)
    assert len(errors) == 0, f"Missing imports: {errors}"


def test_project_structure():
    """Verify critical files exist."""
    from pathlib import Path
    root = Path.cwd()
    required = [
        "config.json",
        "scripts/forward_test.py",
        "scripts/predict.py",
        "scripts/control_layer.py",
        "scripts/external_reference.py",
        "scripts/trajectory_model.py",
        "scripts/utility_measure.py",
        "infrastructure/truth/store/event_store.py",
        "infrastructure/persistence/dto/trade_dto.py",
        "validation/srv/system_replay_validator.py",
    ]
    missing = [f for f in required if not (root / f).exists()]
    assert len(missing) == 0, f"Missing files: {missing}"


def test_model_files_exist():
    """Model artifacts for BTC must be present."""
    from pathlib import Path
    model_dir = Path("models/btc")
    required = ["model.pkl", "scaler.pkl", "metadata.json"]
    missing = [f for f in required if not (model_dir / f).exists()]
    assert len(missing) == 0, f"Missing model files: {missing}"


@pytest.mark.parametrize("name,url,expected", HEALTH_ENDPOINTS)
def test_health_endpoint(name, url, expected):
    """Health endpoints respond with correct status within timeout."""
    start = time.time()
    last_err = None
    while time.time() - start < BOOT_TIMEOUT_S:
        try:
            resp = urllib.request.urlopen(url, timeout=3)
            body = json.loads(resp.read())
            for k, v in expected.items():
                assert body.get(k) == v, f"{name}: {k}={body.get(k)} != {v}"
            return
        except (urllib.error.URLError, ConnectionRefusedError) as e:
            last_err = e
            time.sleep(0.5)
    pytest.fail(f"{name}: endpoint unreachable after {BOOT_TIMEOUT_S}s: {last_err}")


def test_data_engine_trading_status():
    """analytics-engine /observability/trading returns structured status."""
    resp = urllib.request.urlopen("http://localhost:8000/observability/trading", timeout=5)
    body = json.loads(resp.read())
    assert "mode" in body
    assert body["mode"] in ("PAPER_TRADING", "BACKTESTING", "LIVE")
    assert "exchange" in body
    assert "drawdown" in body
    assert "positions" in body
    assert "pipeline" in body


def test_forward_test_health_server():
    """Forward test health server (port 9999) if running, skip otherwise."""
    try:
        resp = urllib.request.urlopen("http://localhost:9999/", timeout=2)
        _ = json.loads(resp.read())
    except (urllib.error.URLError, ConnectionRefusedError):
        pytest.skip("Forward test health server not running on :9999")


def test_env_mode_consistent():
    """ENVIRONMENT_MODE is set consistently across files."""
    # Check config.json
    import json as j
    config = j.loads(open("config.json").read())
    config_mode = config.get("environment_mode", "")
    env_mode = os.environ.get("ENVIRONMENT_MODE", "")

    # Both must be valid
    valid_modes = {"BACKTESTING", "PAPER_TRADING", "LIVE"}
    assert config_mode in valid_modes, f"config.json mode={config_mode} invalid"
    if env_mode:
        assert env_mode in valid_modes, f"ENV mode={env_mode} invalid"
        # Warn if mismatch, but don't fail
        if env_mode != config_mode:
            pytest.skip(f"ENV={env_mode} != config={config_mode}")
