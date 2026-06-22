#!/usr/bin/env bash
# =============================================================================
# ARGOS — Analytics Engine startup (DEV/STAGING)
# =============================================================================
# Infisical inyecta secrets vía CLI. Solo para DEV/STAGING.
# En PRODUCCIÓN, las variables son inyectadas por systemd / orchestrator.
#
# Uso:
#   ./scripts/start-analytics-engine.sh
#   ENVIRONMENT_MODE=BACKTESTING ./scripts/start-analytics-engine.sh
# =============================================================================
set -euo pipefail

ANALYTICS_DIR="$(dirname "$0")/../apps/analytics-engine"

echo "[argos:analytics-engine] starting via infisical (DEV/STAGING mode)"

exec infisical run -- uvicorn app.main:app --host 0.0.0.0 --port 8000
