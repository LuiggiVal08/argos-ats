# Docker Compose Fix Plan

## 1. Fix analytics-engine/Dockerfile

Replace the broken multi-stage build (uses `--prefix /install` which doesn't work) with a proper wheel-based multi-stage build.

**File**: `apps/analytics-engine/Dockerfile`

```dockerfile
FROM python:3.11-slim AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml requirements.txt* ./
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.11-slim
WORKDIR /app
RUN addgroup --system argos && adduser --system --ingroup argos argos && \
    mkdir -p /var/lib/argos /app/data && chown -R argos:argos /var/lib/argos /app/data
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels argos-analytics-engine && \
    rm -rf /wheels
COPY app/ app/
USER argos
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## 2. Improve docker-compose.yml

**File**: `docker-compose.yml`

Changes:
- Add port mappings for all services
- Add `SYMBOL` env var for data-engine
- Add `STREAM_PREFIX` for data-engine
- Add explicit `USE_PYTORCH=false` for analytics-engine
- Expose broker port (optional, for debug)

```yaml
# Argos bot - production-grade trading bot stack
services:
  data-engine:
    build: ./apps/data-engine
    container_name: argos-data-engine
    ports:
      - "3000:3000"
    environment:
      - ENVIRONMENT_MODE=${ENVIRONMENT_MODE:-PAPER_TRADING}
      - ARGOS_BROKER_URL=redis://broker:6379
      - EXCHANGE_WS_URL=${EXCHANGE_WS_URL:-wss://stream.binance.com:9443/ws}
      - SYMBOL=${SYMBOL:-BTC/USDT}
      - STREAM_PREFIX=${STREAM_PREFIX:-ticks:}
    env_file:
      - .env
    depends_on:
      broker:
        condition: service_healthy
    networks: [argos-net]
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "node", "-e", "require('http').get('http://localhost:3000/health', r => process.exit(r.statusCode === 200 ? 0 : 1)).on('error', () => process.exit(1))"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "3" }

  analytics-engine:
    build: ./apps/analytics-engine
    container_name: argos-analytics-engine
    ports:
      - "8000:8000"
    environment:
      - ENVIRONMENT_MODE=${ENVIRONMENT_MODE:-PAPER_TRADING}
      - ARGOS_BROKER_URL=redis://broker:6379
      - SYMBOL=${SYMBOL:-BTC/USDT}
      - USE_PYTORCH=${USE_PYTORCH:-false}
      - RISK_PCT=${RISK_PCT:-0.01}
      - DRAWDOWN_PCT=${DRAWDOWN_PCT:-0.05}
    env_file:
      - .env
    depends_on:
      broker:
        condition: service_healthy
    networks: [argos-net]
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "3" }

  broker:
    image: redis:7-alpine
    container_name: argos-broker
    ports:
      - "6379:6379"
    command: ["redis-server", "--appendonly", "yes"]
    networks: [argos-net]
    volumes:
      - broker-data:/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 3
      start_period: 5s

networks:
  argos-net:
    driver: bridge

volumes:
  broker-data:
```

## 3. Create root .env.example

**File**: `.env.example`

```
# Argos bot - root env file
# Copy to .env and fill in your values.

# --- Mode ---
ENVIRONMENT_MODE=PAPER_TRADING

# --- Symbols ---
SYMBOL=BTC/USDT

# --- Exchange ---
EXCHANGE_WS_URL=wss://stream.binance.com:9443/ws

# --- Exchange API (required for LIVE) ---
EXCHANGE_API_KEY=
EXCHANGE_API_SECRET=

# --- Binance Spot Testnet ---
BINANCE_TESTNET=true
BINANCE_TESTNET_API_KEY=
BINANCE_TESTNET_SECRET=

# --- Risk defaults ---
RISK_PCT=0.01
DRAWDOWN_PCT=0.05

# --- Notifications (optional) ---
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
DISCORD_WEBHOOK_URL=
```
