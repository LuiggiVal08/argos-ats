#!/usr/bin/env python3
"""
Preflight Security Check — ARGOS unified secret validation.

Lee `required_secrets.json` y valida que las variables de entorno
requeridas estén presentes y no vacías según el modo de operación.

USO:
    python3 security/preflight.py                    # check all services
    python3 security/preflight.py data-engine        # check only data-engine
    python3 security/preflight.py analytics-engine   # check only analytics-engine
    ENVIRONMENT_MODE=LIVE python3 security/preflight.py

EXIT CODES:
    0  → todos los checks pasaron
    1  → falta al menos un secret requerido

Arquitectura:
    - Infisical (DEV/STAGING) / systemd (PROD) inyectan las vars.
    - Este script verifica que estén presentes antes de iniciar.
    - No depende de dotenv. Lee directo de os.environ.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def load_schema() -> dict:
    schema_path = Path(__file__).resolve().parent / "required_secrets.json"
    with open(schema_path) as f:
        return json.load(f)


def get_required_vars(schema: dict, mode: str) -> list[str]:
    """Return the list of required env var names for the given mode."""
    mode_cfg = schema.get("mode_requirements", {}).get(mode, {})
    required_groups = mode_cfg.get("required_groups", [])

    required: list[str] = []
    for group_name in required_groups:
        group = schema.get(group_name, [])
        required.extend(group)
    return required


def check_vars(required: list[str]) -> list[str]:
    """Return list of missing/empty var names."""
    errors: list[str] = []
    for var in required:
        val = os.environ.get(var)
        if not val or val.strip() == "":
            errors.append(var)
    return errors


def preflight_check(service: str | None = None) -> list[str]:
    """Run preflight for given service (or all). Returns list of errors."""
    schema = load_schema()
    mode = os.environ.get("ENVIRONMENT_MODE", "BACKTESTING").upper()

    required = get_required_vars(schema, mode)
    return check_vars(required)


def main() -> None:
    service = sys.argv[1] if len(sys.argv) > 1 else "all"
    mode = os.environ.get("ENVIRONMENT_MODE", "BACKTESTING").upper()

    errors = preflight_check(service)

    if errors:
        print(
            f"SECURITY PREFLIGHT FAILED [{service}] mode={mode}",
            file=sys.stderr,
        )
        print(f"  Missing required secrets: {', '.join(errors)}", file=sys.stderr)
        print(
            "  Injection: use 'infisical run' (DEV/STAGING) or set env vars (PROD)",
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        f"SECURITY PREFLIGHT PASSED [{service}] mode={mode}",
        file=sys.stderr,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
