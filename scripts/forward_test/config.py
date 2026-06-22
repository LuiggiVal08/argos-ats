"""Configuration constants and paths for Forward Test Fase B."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
LOGS_DIR = PROJECT_ROOT / "logs" / "forward_test"
MODELS_DIR = PROJECT_ROOT / "models"
POLICY_DIR = PROJECT_ROOT / "policy"
REPORTS_DIR = PROJECT_ROOT / "reports" / "forward_test"

LOOKAHEAD = 5
BUFFER_HOURS = 1000
FUNDING_BUFFER = 500
DEFAULT_INTERVAL = 60

THRESHOLDS = {"BUY": 0.60, "SELL": 0.40}
MAX_HOLD_BARS = 5

TRADING_FEE = 0.0010
SLIPPAGE = 0.0005
HALF_SPREAD = 0.00005
RT_COST = TRADING_FEE * 2 + SLIPPAGE * 2 + HALF_SPREAD * 2

WATCHDOG_MAX_DRAWDOWN = 0.05
WATCHDOG_MAX_CONSECUTIVE_FAILURES = 5
