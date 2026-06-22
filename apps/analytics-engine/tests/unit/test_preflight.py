"""Tests for H5 preflight check (env var validation in LIVE mode).

Per spec §5 Historia 5 sad path: LIVE mode must abort init if required
secret env vars are missing or empty.
"""
from __future__ import annotations

import os

import pytest

from app.preflight import REQUIRED_LIVE_VARS, abort_if_missing, preflight_check


class TestPreflightCheck:
    def test_backtesting_no_errors(self) -> None:
        assert preflight_check("BACKTESTING") == []

    def test_paper_trading_requires_testnet(self) -> None:
        errors = preflight_check("PAPER_TRADING")
        assert len(errors) == 2
        assert "BINANCE_TESTNET_API_KEY" in str(errors)
        assert "BINANCE_TESTNET_SECRET" in str(errors)

    def test_live_with_missing_vars(self) -> None:
        # Unset all required LIVE vars.
        saved = {}
        for var in REQUIRED_LIVE_VARS:
            saved[var] = os.environ.pop(var, None)
        try:
            errors = preflight_check("LIVE")
            assert len(errors) == len(REQUIRED_LIVE_VARS)
            for var in REQUIRED_LIVE_VARS:
                assert var in str(errors)
        finally:
            for var in REQUIRED_LIVE_VARS:
                if saved[var] is not None:
                    os.environ[var] = saved[var]

    def test_live_with_all_vars(self) -> None:
        saved = {}
        for var in REQUIRED_LIVE_VARS:
            saved[var] = os.environ.get(var)
            os.environ[var] = f"test_{var}"
        try:
            assert preflight_check("LIVE") == []
        finally:
            for var in REQUIRED_LIVE_VARS:
                if saved[var] is not None:
                    os.environ[var] = saved[var]
                else:
                    os.environ.pop(var, None)

    def test_live_rejects_empty_string(self) -> None:
        saved = {}
        for var in REQUIRED_LIVE_VARS:
            saved[var] = os.environ.get(var)
            os.environ[var] = ""
        try:
            errors = preflight_check("LIVE")
            assert len(errors) == len(REQUIRED_LIVE_VARS)
        finally:
            for var in REQUIRED_LIVE_VARS:
                if saved[var] is not None:
                    os.environ[var] = saved[var]
                else:
                    os.environ.pop(var, None)

    def test_abort_if_missing_exits_on_live_without_vars(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for var in REQUIRED_LIVE_VARS:
            monkeypatch.delenv(var, raising=False)

        with pytest.raises(SystemExit) as exc:
            abort_if_missing("LIVE")
        assert exc.value.code == 1

    def test_abort_if_missing_does_not_exit_on_backtesting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for var in REQUIRED_LIVE_VARS:
            monkeypatch.delenv(var, raising=False)

        # Should not raise SystemExit.
        abort_if_missing("BACKTESTING")

    def test_abort_if_missing_does_not_exit_on_live_with_vars(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for var in REQUIRED_LIVE_VARS:
            monkeypatch.setenv(var, f"test_{var}")

        # Should not raise SystemExit.
        abort_if_missing("LIVE")
