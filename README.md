# argos-ats

> Autonomous production-grade crypto perpetual futures Automated Trading System (ATS).

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-green)]()
[![Stack: NestJS + FastAPI + Redis](https://img.shields.io/badge/stack-NestJS%20%2B%20FastAPI%20%2B%20Redis-blue)]()

## Status

🚀 **Alpha** — 99.3% completo (297/299 tareas). 18 historias completadas, spec-driven desde [`spec.md`](./spec.md).

Progreso vivo: [`TASKS.md`](./TASKS.md).

| ID | Historia | Estado |
|----|----------|--------|
| Setup | Infraestructura y herramientas | ✅ 100% |
| H1 | Tick Pipeline (<2ms p99) | ✅ 100% |
| H2 | Position Sizing (≤1% risk) | ✅ 100% |
| H3 | Circuit Breaker (5% drawdown) | ✅ 100% |
| H4-A | Order Retry + Emergency Market | ✅ 100% |
| H4-B | OWASP Incident Response | ✅ 100% |
| H5 | Secrets & Env Mode | ✅ 100% |
| H6 | NovaQuant ML Pipeline (LSTM) | ✅ 100% |
| H7 | Live Execution Engine | ✅ 100% |
| H8 | Backtesting Engine | ✅ 100% |
| H9 | Telemetry Webhooks (Telegram/Discord) | ✅ 100% |
| H8–H12 | ARGOS 2.0 Data Engine (NestJS) | ✅ 100% |
| H13–H22 | ARGOS 2.0 Dataset & Feature Engine | ✅ 100% |
| H23–H29 | ARGOS 2.0 Model Pipeline | ✅ 100% |
| H30–H39 | ARGOS 2.0 Execution Engine | ✅ 100% |
| H40–H50 | ARGOS 2.0 Training Engine | ✅ 100% |
| H51–H59 | ARGOS 2.0 Observability & DR | ✅ 100% |
| S01 | Additional Data Sources | ✅ 100% |
| QV1–Phase9 | Quant Validation (V2, 14 fases) | ✅ 100% |

## Architecture

Event-driven microservices, hexagonal internally.

| Service | Stack | Role |
|---|---|---|
| `data-engine` | NestJS (TS) | WebSocket → exchange, injects ticks to Redis (<2ms p99 SLA) |
| `analytics-engine` | FastAPI (Py 3.11) | Consumes Redis, computes indicators, NovaQuant ML, risk engine, execution |
| `broker` | RESP-compatible (Redis 7+/Memurai/Dragonfly/Valkey/KeyDB/Garnet/Redict) | Message bus / buffer |

Services communicate **only** via Redis. No direct imports across services.

Hard invariants: 1% risk per trade, ATR-based SL distance, 5% daily drawdown circuit-breaker.

## Quick start

The same code runs in two ways. Pick one.

### Option A — Docker Compose (recommended for production / CI)

```bash
git clone https://github.com/LuiggiVal08/argos-ats.git
cd argos-ats
docker compose up -d
docker compose ps
```

### Option B — Bare metal (recommended for native dev on Windows / macOS / Linux)

The app code is **deployment-agnostic** — Docker is one option, not the only one.

#### 1. Install a RESP-compatible broker

| OS | Install | Notes |
|---|---|---|
| macOS | `brew install redis` | Start with `brew services start redis` |
| Ubuntu / Debian (incl. WSL2) | `sudo apt install redis-server` | WSL2 forwards ports to `localhost` automatically |
| Windows (native, no WSL) | `choco install memurai` or `scoop install memurai` | Memurai is a Redis-compatible Windows broker |
| Any | `docker run -d -p 6379:6379 redis:7-alpine` | Minimal Docker dependency |

#### 2. Run the services

```bash
# Terminal 1 — data-engine (NestJS)
cd apps/data-engine
ARGOS_BROKER_URL=redis://localhost:6379 npm run dev

# Terminal 2 — analytics-engine (FastAPI)
cd apps/analytics-engine
ARGOS_BROKER_URL=redis://localhost:6379 uvicorn app.main:app --reload --port 8000
```

#### 3. Verify

```bash
/health
```

## Repository layout

```
argos-ats/
├── apps/
│   ├── data-engine/              # NestJS, TS — WebSocket → Redis
│   └── analytics-engine/         # FastAPI, Py 3.11 — Redis → signals, risk, execution
├── docs/
│   ├── prs/                      # PR bodies archive
│   └── architecture-v3-production.md
├── experiments/                  # Quant validation experiments (QV1, QV2)
├── reports/                      # Migration, security, sprint reports
├── roadmaps/                     # Quant V2 roadmap & configs
├── spec.md                       # Source of truth (8 epics in §5)
├── AGENTS.md                     # Rules for the AI agent working on this project
├── TASKS.md                      # Live progress tracker (299 tasks)
├── LICENSE                       # MIT
├── docker-compose.yml            # data-engine + analytics-engine + broker
├── config.json                   # ENVIRONMENT_MODE + risk params
├── skills-lock.json              # Pinned AI agent skills
├── BUSINESS-PLAN.md              # Business model & competitive analysis
├── ROADMAP-v2.md                 # ARGOS 2.0 full spec (V5.0)
└── .opencode/                    # AI agent tooling (tools, commands, skills)
```

## Operating modes

`ENVIRONMENT_MODE` ∈ `{BACKTESTING, PAPER_TRADING, LIVE}` (set in `config.json`).

| Mode | Real money? | Use case |
|---|---|---|
| `BACKTESTING` | No | Validate strategies on historical data |
| `PAPER_TRADING` | No | Validate pipeline end-to-end with live ticks |
| `LIVE` | **Yes** | Real trading. Aborts on missing secret env vars. |

## Features

### Core Engine
- **Tick Pipeline** — WebSocket → sanitize → Redis XADD in <2ms p99 with in-memory buffer on broker failure
- **Additional Data Sources** — Funding rates, open interest, order flow, multi-timeframe alignment
- **Candle Pipeline** — Real-time OHLCV construction, feature calculation, market replay

### Risk Management
- **Position Sizing** — ATR-based, ≤1% risk per trade, min lot validation
- **Circuit Breaker** — 5% daily drawdown threshold, auto-halt with position liquidation
- **Order Retry** — Exponential backoff (3 retries, 500ms window), emergency market fallback

### ML Pipeline (NovaQuant)
- LSTM model with configurable architecture (3 hidden layers, dropout, early stopping)
- 20+ TA features (RSI, MACD, BB, EMA, ATR, ADX)
- Model registry with champion/challenger promotion, walk-forward validation
- Bayesian evidence system (EDL) with prior skepticism, regime-conditional likelihood

### Execution Engine
- Signal → validation → circuit breaker → position sizing → order placement → position monitoring loop
- Multi-TP/BE/trailing SL support
- Portfolio manager with correlation engine and per-symbol exposure limits

### Backtesting
- Candle-by-candle engine with ATR-based SL, equity curve, max trades cap
- Built-in strategies: EMA Cross, RSI Mean Reversion (extensible via registry)
- Metrics: Sharpe ratio, max drawdown, win rate, profit factor

### Observability
- Telemetry engine (4-engine metrics, 10k-point buffer)
- Dashboard engine (market/AI/risk/training panels)
- Disaster recovery with auto-mode escalation (NORMAL → DEGRADED → SAFE → HALTED)
- Notifications via Telegram/Discord webhooks

### Security
- 4-phase OWASP incident response protocol
- Pre-flight secret validation (LIVE mode aborts on missing credentials)
- Infisical secret manager integration

## Development workflow

Git-flow-lite:

- `main` — production, deployable.
- `dev` — integration. Base for new feature branches.
- `feature/<id>-<slug>` — one branch per story. Merge → `dev`.
- `fix/<slug>` — non-critical bug. Merge → `dev`.
- `hotfix/<slug>` — critical prod bug. Merge → `main` **and** `dev`.

Commits follow [Conventional Commits](https://www.conventionalcommits.org/): `feat(data-engine): h1-001 scaffold Redis publisher`.

PRs are opened and merged manually on GitHub.

## Documentation

- **[spec.md](./spec.md)** — Full product spec, 8 epics, hard invariants.
- **[AGENTS.md](./AGENTS.md)** — Rules the AI agent follows on this project.
- **[TASKS.md](./TASKS.md)** — Current progress, story status, dev log.
- **[BUSINESS-PLAN.md](./BUSINESS-PLAN.md)** — Business model analysis.
- **[ROADMAP-v2.md](./ROADMAP-v2.md)** — ARGOS 2.0 full specification (V5.0).
- **[docs/architecture-v3-production.md](./docs/architecture-v3-production.md)** — Production architecture post-validation.

## License

[MIT](./LICENSE) © 2026 Luiggi.
