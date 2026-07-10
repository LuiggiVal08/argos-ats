"""Correlation ID system for cross-component traceability.

Every event related to a single trade shares a common ``trade_id``.
Identifiers are generated as compact hex strings (12-24 chars) using
uuid4 for uniqueness without external dependencies.

Usage::

    from app.infrastructure.logging.correlation import (
        bind_correlation_context,
        generate_decision_id,
        generate_inference_id,
        generate_inference_sequence_id,
        generate_trade_id,
        clear_correlation_context,
    )

    # At start of inference cycle:
    seq_id = generate_inference_sequence_id()
    bind_correlation_context(
        session_id=SESSION_ID,
        inference_id=generate_inference_id(),
        inference_sequence_id=seq_id,
    )

    # When a signal is generated:
    bind_correlation_context(decision_id=generate_decision_id())

    # When a trade is opened:
    bind_correlation_context(trade_id=generate_trade_id())

    # Logging automatically includes all bound context:
    log.info("model_inference", prob_buy=0.85)
    # -> Output includes session_id, inference_id, decision_id, trade_id
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars, merge_contextvars

from .logging_config import get_logger

_SESSION_ID: str | None = None
log = get_logger("system")

_INFERENCE_COUNTER_FILE: str | None = None
_inference_counter_lock: bool = False


def generate_id(prefix: str = "") -> str:
    """Generate a compact unique identifier.

    Args:
        prefix: Optional prefix (e.g. "inf", "dec", "trd").

    Returns:
        20-char hex string with optional prefix: ``<prefix>_<20hex>``.
    """
    raw = uuid.uuid4().hex[:20]
    return f"{prefix}_{raw}" if prefix else raw


def set_inference_counter_path(path: str | Path) -> None:
    global _INFERENCE_COUNTER_FILE
    _INFERENCE_COUNTER_FILE = str(path)


def _read_inference_counter() -> int:
    if _INFERENCE_COUNTER_FILE is None:
        return 0
    try:
        with open(_INFERENCE_COUNTER_FILE) as f:
            data = json.load(f)
            return int(data.get("counter", 0))
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return 0


def _write_inference_counter(count: int) -> None:
    if _INFERENCE_COUNTER_FILE is None:
        return
    try:
        p = Path(_INFERENCE_COUNTER_FILE)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w") as f:
            json.dump({"counter": count, "updated": datetime.now(timezone.utc).isoformat()}, f)
    except OSError:
        log.warning("inference_counter_write_failed", path=_INFERENCE_COUNTER_FILE)


def generate_inference_sequence_id() -> str:
    """Generate a persistent sequential inference ID (INF-000001, INF-000002, ...).

    The counter is persisted to ``state/inference_counter.json`` and survives
    process restarts. Each call increments the counter atomically.

    Returns:
        ID string like ``INF-000012``.
    """
    global _inference_counter_lock
    if _inference_counter_lock:
        raise RuntimeError("inference_sequence_id re-entrancy detected")
    _inference_counter_lock = True
    try:
        count = _read_inference_counter() + 1
        seq = f"INF-{count:06d}"
        _write_inference_counter(count)
        return seq
    finally:
        _inference_counter_lock = False


def generate_session_id() -> str:
    return generate_id("ses")


def generate_decision_id() -> str:
    return generate_id("dec")


def generate_inference_id() -> str:
    return generate_id("inf")


def generate_signal_id() -> str:
    return generate_id("sig")


def generate_trade_id() -> str:
    return generate_id("trd")


def generate_position_id() -> str:
    return generate_id("pos")


def generate_order_id() -> str:
    return generate_id("ord")


def get_or_create_session_id() -> str:
    """Return the current session ID, creating one if needed."""
    global _SESSION_ID
    if _SESSION_ID is None:
        _SESSION_ID = generate_session_id()
    return _SESSION_ID


def bind_correlation_context(**kwargs: Any) -> None:
    """Bind key-value pairs to the current structured log context.

    All subsequent log calls in this async context will include these fields.
    Uses structlog.contextvars for async-safe context propagation.

    Args:
        **kwargs: Key-value pairs to bind (e.g. trade_id, inference_id).
    """
    bind_contextvars(**kwargs)


def clear_correlation_context() -> None:
    """Clear all bound correlation context variables."""
    clear_contextvars()


def start_session_context(session_id: str | None = None) -> str:
    """Initialize a new session context with a fresh session ID.

    Clears any existing context then binds a new session_id.

    Args:
        session_id: Optional explicit session ID; auto-generated if None.

    Returns:
        The active session ID.
    """
    clear_correlation_context()
    sid = session_id or get_or_create_session_id()
    global _SESSION_ID
    _SESSION_ID = sid
    bind_correlation_context(
        session_id=sid,
        service="analytics-engine",
        environment=os.environ.get("ENVIRONMENT_MODE", "UNKNOWN"),
        startup_timestamp=datetime.now(timezone.utc).isoformat(),
    )
    log.info("session_started", session_id=sid)
    return sid
