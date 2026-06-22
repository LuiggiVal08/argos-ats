#!/usr/bin/env bash
# =============================================================================
# ARGOS — prepare-env: Generate .env from Infisical for Docker Compose
# =============================================================================
# Uso:
#   ./scripts/prepare-env.sh                         # usa entorno "dev"
#   INFISICAL_ENV=staging ./scripts/prepare-env.sh   # otro entorno
#   INFISICAL_ENV=prod  ./scripts/prepare-env.sh     # producción
#
# Output: escribe .env en la raíz del proyecto, listo para docker compose up.
# Si Infisical no está instalado, genera defaults no-secretos y advierte.
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"
INFISICAL_ENV="${INFISICAL_ENV:-dev}"

# ── 1. Defaults no-secretos ─────────────────────────────────────────────────
# Estos valores son seguros para estar en .env porque no son secretos.
# Docker Compose los usa como base; Infisical sobreescribe los que sean secrets.
cat > "$ENV_FILE" << 'DEFAULTS'
# ────────────────────────────────────────────────────────────────────────────
# ARGOS — .env generado por scripts/prepare-env.sh
# NO EDITAR: este archivo es regenerado cada vez que corres prepare-env.sh.
# Los valores de Infisical sobreescriben cualquier default aquí.
# ────────────────────────────────────────────────────────────────────────────

# --- Mode ---
ENVIRONMENT_MODE=PAPER_TRADING

# --- Symbols ---
SYMBOL=BTC/USDT

# --- Exchange WebSocket ---
EXCHANGE_WS_URL=wss://stream.binance.com:9443/ws

# --- Exchange identity (binanceusdm = USDT-M Futures, binance = Spot) ---
EXCHANGE_ID=binanceusdm

# --- Binance Futures Demo (default: enabled for PAPER_TRADING) ---
BINANCE_TESTNET=true

# --- Risk defaults ---
RISK_PCT=0.01
DRAWDOWN_PCT=0.05

# --- Notifications (optional — Infisical sobreescribe si existen) ---
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# --- Exchange API keys (required for LIVE — Infisical sobreescribe) ---
EXCHANGE_API_KEY=
EXCHANGE_API_SECRET=
BINANCE_TESTNET_API_KEY=
BINANCE_TESTNET_SECRET=

# --- Optional (can be overridden by Infisical) ---
STREAM_PREFIX=ticks:
TICK_BUFFER_CAP=100
USE_PYTORCH=false
ARGOS_BROKER_URL=redis://broker:6379
DISCORD_WEBHOOK_URL=
DEFAULTS

# ── 2. Overlay Infisical secrets ────────────────────────────────────────────
if command -v infisical &>/dev/null; then
    echo "[prepare-env] Infisical CLI found — exporting secrets (env=$INFISICAL_ENV)..."

    INFISICAL_OUTPUT=$(infisical export --env="$INFISICAL_ENV" 2>&1) || {
        echo "[prepare-env] WARNING: infisical export failed (exit $?)"
        echo "[prepare-env] WARNING: proceeding with defaults only — preflight will abort if secrets are missing"
        echo "[prepare-env] WARNING: fix: run 'infisical init' or check your Infisical token"
        echo "$INFISICAL_OUTPUT"
        exit 0
    }

    # Append Infisical secrets to .env (overwrites any duplicate keys)
    echo "" >> "$ENV_FILE"
    echo "# --- Infisical secrets (env=$INFISICAL_ENV) ---" >> "$ENV_FILE"
    echo "$INFISICAL_OUTPUT" >> "$ENV_FILE"

    count=$(echo "$INFISICAL_OUTPUT" | grep -c '=')
    echo "[prepare-env] OK — $count secrets injected from Infisical"
else
    echo "[prepare-env] WARNING: Infisical CLI not found"
    echo "[prepare-env] WARNING: proceeding with defaults only — preflight will abort if secrets are missing"
    echo "[prepare-env] WARNING: install: https://infisical.com/docs/cli/install"
fi

echo "[prepare-env] written: $ENV_FILE"
echo "[prepare-env] ready for: docker compose up"
