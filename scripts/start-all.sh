#!/usr/bin/env bash
# =============================================================================
# ARGOS — Full stack startup (DEV/STAGING)
# =============================================================================
# Inicia broker (Redis) via Docker Compose, luego ambos servicios via Infisical.
# Solo para DEV/STAGING. En PRODUCCIÓN usar systemd units o Docker Compose
# nativo con secrets inyectados por el orchestrator.
#
# Uso:
#   ./scripts/start-all.sh
# =============================================================================
set -euo pipefail

ROOT="$(dirname "$0")/.."
echo "[argos] starting full stack (DEV/STAGING mode)"

# 0. Generate .env from Infisical (secrets for Docker Compose)
echo "[argos] preparing environment (Infisical → .env)..."
bash "$ROOT/scripts/prepare-env.sh"

# 1. Preflight check
python3 "$ROOT/security/preflight.py" all

# 2. Start broker (Redis) — detach
echo "[argos] starting broker (Redis)..."
docker compose -f "$ROOT/docker-compose.yml" up -d broker
echo "[argos] broker ready"

# 3. Start services in background via Infisical
echo "[argos] starting data-engine via infisical..."
infisical run -- npm run start --prefix "$ROOT/apps/data-engine" &
PID_DATA=$!

echo "[argos] starting analytics-engine via infisical..."
infisical run -- uvicorn app.main:app --host 0.0.0.0 --port 8000 &
PID_ANALYTICS=$!

# 4. Trap shutdown
trap 'echo "[argos] shutting down..."; kill $PID_DATA $PID_ANALYTICS 2>/dev/null; docker compose -f "$ROOT/docker-compose.yml" stop broker; exit 0' SIGINT SIGTERM

echo "[argos] all services running. Press Ctrl+C to stop."
wait
