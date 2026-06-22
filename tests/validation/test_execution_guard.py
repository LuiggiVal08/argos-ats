"""Phase 6: Execution Guard Validation.

Tests for ExecutionGate (source-based access control) and
ExecutionGuard (confidence threshold, volatility spike, soft circuit breaker).

Coverage targets:
  ExecutionGate:  ALLOW flow, BLOCK flows, metadata edge cases  -> 100% lines
  ExecutionGuard: confidence reject, volatility reduction, soft pause  -> 100% lines
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.domain.value_objects.execution_signal import ExecutionSignal
from app.domain.value_objects.signal_side import SignalSide
from app.infrastructure.trading.execution_gate import ExecutionGate, _GateBlocked
from app.infrastructure.trading.execution_guard import ExecutionGuard, _GuardRejected


# =============================================================================
# Helpers
# =============================================================================


def _signal(
    confidence: float = 0.8,
    side: SignalSide = SignalSide.BUY,
    source: str | None = None,
    symbol: str = "BTC/USDT",
) -> ExecutionSignal:
    meta = {}
    if source is not None:
        meta["source"] = source
    return ExecutionSignal(
        side=side,
        confidence=confidence,
        symbol=symbol,
        timestamp=datetime.now(timezone.utc),
        metadata=meta,
    )


class _FakeResult:
    """A fake ExecutionReport-like result."""

    def __init__(self, approved: bool = True, reason: str = "") -> None:
        self.approved = approved
        self.reason = reason


# =============================================================================
# ExecutionGate
# =============================================================================


class TestExecutionGate:
    async def test_allow_streaming(self):
        inner = AsyncMock(return_value=_FakeResult(approved=True))
        gate = ExecutionGate(inner)
        result = await gate.execute(_signal(source="STREAMING"))
        inner.assert_awaited_once()
        assert result.approved is True

    async def test_block_unknown_source_default(self):
        inner = AsyncMock()
        gate = ExecutionGate(inner)
        result = await gate.execute(_signal())  # no source → UNKNOWN
        inner.assert_not_awaited()
        assert isinstance(result, _GateBlocked)
        assert "UNKNOWN" in result.reason

    async def test_block_non_streaming_source(self):
        inner = AsyncMock()
        gate = ExecutionGate(inner)
        for bad in ("REST", "BACKTEST", "MANUAL", "SCHEDULER", "SIGNAL_REPLAY"):
            result = await gate.execute(_signal(source=bad))
            inner.assert_not_awaited()
            assert isinstance(result, _GateBlocked)
            assert bad in result.reason

    async def test_block_returns_approved_false(self):
        inner = AsyncMock()
        gate = ExecutionGate(inner)
        result = await gate.execute(_signal(source="MANUAL"))
        assert result.approved is False
        assert result.reason

    async def test_passes_exception_from_inner(self):
        async def _fail(_sig):
            raise ValueError("exchange error")

        gate = ExecutionGate(_fail)
        with pytest.raises(ValueError, match="exchange error"):
            await gate.execute(_signal(source="STREAMING"))


# =============================================================================
# ExecutionGuard
# =============================================================================


class TestExecutionGuard:
    # ── Confidence threshold ──────────────────────────────────────────

    async def test_passes_high_confidence(self):
        inner = AsyncMock(return_value=_FakeResult(approved=True))
        guard = ExecutionGuard(inner, confidence_threshold=0.75)
        result = await guard.execute(_signal(confidence=0.9))
        inner.assert_awaited_once()
        assert result.approved is True

    async def test_rejects_low_confidence(self):
        inner = AsyncMock()
        guard = ExecutionGuard(inner, confidence_threshold=0.75)
        result = await guard.execute(_signal(confidence=0.5))
        inner.assert_not_awaited()
        assert isinstance(result, _GuardRejected)
        assert "low_confidence" in result.reason

    async def test_boundary_confidence_accepted(self):
        inner = AsyncMock(return_value=_FakeResult(approved=True))
        guard = ExecutionGuard(inner, confidence_threshold=0.75)
        result = await guard.execute(_signal(confidence=0.75))
        inner.assert_awaited_once()
        assert result.approved is True

    async def test_rejected_low_confidence_counter(self):
        inner = AsyncMock()
        guard = ExecutionGuard(inner, confidence_threshold=0.75)
        await guard.execute(_signal(confidence=0.3))
        await guard.execute(_signal(confidence=0.4))
        assert guard.rejected_low_confidence == 2

    # ── Execution errors ──────────────────────────────────────────────

    async def test_execution_error_returns_rejected(self):
        async def _fail(_sig):
            raise RuntimeError("connection lost")

        guard = ExecutionGuard(_fail, soft_pause_failures=5)
        result = await guard.execute(_signal(confidence=0.9))
        assert isinstance(result, _GuardRejected)
        assert "connection lost" in result.reason

    async def test_execution_error_increments_consecutive_failures(self):
        async def _fail(_sig):
            raise RuntimeError("err")

        guard = ExecutionGuard(_fail, soft_pause_failures=5)
        await guard.execute(_signal(confidence=0.9))
        await guard.execute(_signal(confidence=0.9))
        assert guard._consecutive_failures == 2

    # ── Rejected result (non-exception) ───────────────────────────────

    async def test_rejected_result_increments_failures(self):
        inner = AsyncMock(return_value=_FakeResult(approved=False, reason="nok"))
        guard = ExecutionGuard(inner, soft_pause_failures=5)
        await guard.execute(_signal(confidence=0.9))
        await guard.execute(_signal(confidence=0.9))
        assert guard._consecutive_failures == 2

    async def test_success_resets_consecutive_failures(self):
        inner = AsyncMock()
        guard = ExecutionGuard(inner, soft_pause_failures=5)

        inner.return_value = _FakeResult(approved=False, reason="nok")
        await guard.execute(_signal(confidence=0.9))
        assert guard._consecutive_failures == 1

        inner.return_value = _FakeResult(approved=True)
        await guard.execute(_signal(confidence=0.9))
        assert guard._consecutive_failures == 0

    # ── Soft circuit breaker ──────────────────────────────────────────

    async def test_soft_pause_triggers_after_n_failures(self):
        inner = AsyncMock(return_value=_FakeResult(approved=False, reason="nok"))
        guard = ExecutionGuard(inner, soft_pause_failures=3, soft_pause_seconds=60.0)

        await guard.execute(_signal(confidence=0.9))  # fail 1
        await guard.execute(_signal(confidence=0.9))  # fail 2
        await guard.execute(_signal(confidence=0.9))  # fail 3 → pause

        assert guard.soft_pauses_triggered == 1
        assert guard.is_paused

    async def test_soft_pause_rejects_all_signals(self):
        inner = AsyncMock(return_value=_FakeResult(approved=True))
        guard = ExecutionGuard(inner, soft_pause_failures=2, soft_pause_seconds=60.0)

        inner.return_value = _FakeResult(approved=False, reason="nok")
        await guard.execute(_signal(confidence=0.9))
        await guard.execute(_signal(confidence=0.9))  # trigger pause

        inner.reset_mock()
        result = await guard.execute(_signal(confidence=0.9))
        inner.assert_not_awaited()
        assert isinstance(result, _GuardRejected)
        assert "soft_circuit_breaker" in result.reason

    async def test_soft_pause_auto_recovers(self):
        inner = AsyncMock(return_value=_FakeResult(approved=False, reason="nok"))
        guard = ExecutionGuard(inner, soft_pause_failures=2, soft_pause_seconds=0.05)

        await guard.execute(_signal(confidence=0.9))
        await guard.execute(_signal(confidence=0.9))  # trigger pause
        assert guard.is_paused

        import asyncio

        await asyncio.sleep(0.06)
        assert not guard.is_paused

    async def test_soft_pause_counter(self):
        inner = AsyncMock(return_value=_FakeResult(approved=False, reason="nok"))
        guard = ExecutionGuard(inner, soft_pause_failures=2, soft_pause_seconds=0.1)

        # burst 1
        await guard.execute(_signal(confidence=0.9))
        await guard.execute(_signal(confidence=0.9))  # pause 1
        assert guard.soft_pauses_triggered == 1

        import asyncio

        await asyncio.sleep(0.15)

        # burst 2
        await guard.execute(_signal(confidence=0.9))
        await guard.execute(_signal(confidence=0.9))  # pause 2
        assert guard.soft_pauses_triggered == 2

    # ── Volatility spike detection ────────────────────────────────────

    def test_volatility_factor_default_when_insufficient_data(self):
        guard = ExecutionGuard(lambda s: _FakeResult(approved=True), atr_window=20)
        assert guard.volatility_reduction_factor() == 1.0

        guard.report_atr_ratio(0.01)
        guard.report_atr_ratio(0.02)
        guard.report_atr_ratio(0.015)
        guard.report_atr_ratio(0.018)  # only 4 data points
        assert guard.volatility_reduction_factor() == 1.0

    def test_volatility_factor_within_normal_range(self):
        guard = ExecutionGuard(lambda s: _FakeResult(approved=True), atr_window=20)
        for _ in range(6):
            guard.report_atr_ratio(0.01)
        assert guard.volatility_reduction_factor() == 1.0

    def test_volatility_factor_spike_returns_half(self):
        guard = ExecutionGuard(lambda s: _FakeResult(approved=True), atr_window=20)
        for _ in range(5):
            guard.report_atr_ratio(0.01)
        guard.report_atr_ratio(0.025)  # > 2x avg (0.01) → spike
        assert guard.volatility_reduction_factor() == 0.5

    def test_volatility_counter(self):
        guard = ExecutionGuard(lambda s: _FakeResult(approved=True), atr_window=20)
        for _ in range(5):
            guard.report_atr_ratio(0.01)
        guard.report_atr_ratio(0.030)  # > 2x avg 0.0117 → spike
        guard.volatility_reduction_factor()
        assert guard.volatility_reductions == 1

        guard.report_atr_ratio(0.050)  # > 2x new avg → spike
        guard.volatility_reduction_factor()
        assert guard.volatility_reductions == 2

    def test_volatility_factor_ignores_zero_or_negative(self):
        guard = ExecutionGuard(lambda s: _FakeResult(approved=True), atr_window=20)
        for _ in range(6):
            guard.report_atr_ratio(0.01)
        guard.report_atr_ratio(-1.0)  # should be ignored
        guard.report_atr_ratio(0.0)  # should be ignored
        assert guard.volatility_reduction_factor() == 1.0

    # ── is_paused property ────────────────────────────────────────────

    def test_is_paused_false_initially(self):
        guard = ExecutionGuard(lambda s: _FakeResult(approved=True))
        assert not guard.is_paused

    # ── _is_rejected static method ────────────────────────────────────

    def test_is_rejected_by_approved_flag(self):
        assert not ExecutionGuard._is_rejected(_FakeResult(approved=True))
        assert ExecutionGuard._is_rejected(_FakeResult(approved=False))

    def test_is_rejected_by_reason_string(self):
        class _NoApproved:
            def __init__(self):
                self.reason = "error"

        assert ExecutionGuard._is_rejected(_NoApproved())

    def test_is_rejected_empty_reason(self):
        class _NoApproved:
            def __init__(self):
                self.reason = ""

        assert not ExecutionGuard._is_rejected(_NoApproved())

    # ── _GuardRejected ────────────────────────────────────────────────

    def test_guard_rejected_has_approved_and_reason(self):
        r = _GuardRejected("test reason")
        assert r.approved is False
        assert r.reason == "test reason"
