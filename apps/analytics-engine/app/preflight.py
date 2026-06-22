"""PreflightCheck: validates env requirements before init.

Per spec §5 Historia 5 sad path: if ENVIRONMENT_MODE=LIVE but required
secret env vars are missing or empty, abort init with exit code 1.

Extiende validación a PAPER_TRADING y LIVE usando el schema
compartido `security/required_secrets.json`.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _load_schema() -> dict:
    """Load the shared secrets schema. Falls back to embedded defaults."""
    search_paths = [
        Path(__file__).resolve().parent.parent.parent.parent / "security" / "required_secrets.json",
        Path.cwd() / "security" / "required_secrets.json",
        Path("/etc/argos/required_secrets.json"),
    ]
    for p in search_paths:
        if p.exists():
            try:
                return json.loads(p.read_text())
            except (json.JSONDecodeError, OSError):
                continue

    # Embedded fallback
    return {
        "production": ["EXCHANGE_API_KEY", "EXCHANGE_API_SECRET", "ARGOS_BROKER_URL"],
        "testnet": ["BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_SECRET"],
        "notifications": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
        "optional": [
            "EXCHANGE_PASSPHRASE", "EXCHANGE_ID", "EXCHANGE_WS_URL",
            "DISCORD_WEBHOOK_URL", "USE_PYTORCH", "RISK_PCT", "DRAWDOWN_PCT",
            "ARGOS_ENV_MODE_FILE",
        ],
        "mode_requirements": {
            "BACKTESTING": {"required_groups": [], "description": ""},
            "PAPER_TRADING": {"required_groups": ["testnet"], "description": ""},
            "LIVE": {"required_groups": ["production", "notifications"], "description": ""},
            "LIVE_SIMULATION": {"required_groups": ["testnet", "notifications"], "description": ""},
        },
    }


def _get_required_vars(schema: dict, mode: str) -> list[str]:
    mode_cfg = schema.get("mode_requirements", {}).get(mode, {})
    required_groups = mode_cfg.get("required_groups", [])
    required: list[str] = []
    for group_name in required_groups:
        group = schema.get(group_name, [])
        required.extend(group)
    return required


def _required_vars_for_mode(mode: str) -> list[str]:
    """Return the list of required env var names for a given mode."""
    schema = _load_schema()
    return _get_required_vars(schema, mode)


# Backward-compat export for tests
REQUIRED_LIVE_VARS = _required_vars_for_mode("LIVE")


def preflight_check(mode: str) -> list[str]:
    """Return a list of missing-var error messages.
    If the list is empty, all checks passed.
    """
    schema = _load_schema()
    required = _get_required_vars(schema, mode)
    errors: list[str] = []

    for var in required:
        val = os.environ.get(var)
        if not val or val.strip() == "":
            errors.append(f"{var} is required in {mode} mode but is missing or empty")

    return errors


def abort_if_missing(mode: str) -> None:
    """Run preflight_check and sys.exit(1) if any errors found.

    Strict abort for LIVE mode (missing secrets = unsafe).
    For simulation modes (BACKTESTING, PAPER_TRADING, LIVE_SIMULATION)
    missing required vars are logged as warnings but don't block boot.
    """
    errors = preflight_check(mode)
    if not errors:
        return

    import structlog
    log = structlog.get_logger()
    if mode == "LIVE":
        log.critical(
            "preflight_failed",
            mode=mode,
            errors=errors,
            message=f"{mode} mode init aborted: missing required secrets",
        )
        print(f"FATAL: preflight failed [{mode}]: {errors}", file=sys.stderr)
        sys.exit(1)

    # Simulation modes: warn but don't block
    log.warning(
        "preflight_missing_optional_vars",
        mode=mode,
        errors=errors,
        message=f"{mode} mode booting with missing recommended vars",
    )
