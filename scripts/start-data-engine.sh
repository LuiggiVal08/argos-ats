#!/usr/bin/env bash
# =============================================================================
# ARGOS — Data Engine startup (DEV/STAGING)
# =============================================================================
# Infisical inyecta secrets vía CLI. Solo para DEV/STAGING.
# En PRODUCCIÓN, las variables son inyectadas por systemd / orchestrator.
#
# Uso:
#   ./scripts/start-data-engine.sh              # con Infisical
#   ENVIRONMENT_MODE=BACKTESTING ./scripts/start-data-engine.sh  # override modo
# =============================================================================
set -euo pipefail

echo "[argos:data-engine] starting via infisical (DEV/STAGING mode)"

exec infisical run -- npm run start --prefix "$(dirname "$0")/../apps/data-engine"
