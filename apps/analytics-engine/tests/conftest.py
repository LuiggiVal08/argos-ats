"""Shared fixtures for analytics-engine tests."""
from __future__ import annotations

import time

import pytest


@pytest.fixture
def skip_startup_warmup() -> None:
    """Override _boot_timestamp so the /health endpoint reports 'ok' immediately."""
    import app.main as _main

    _main._boot_timestamp = time.monotonic() - 60
