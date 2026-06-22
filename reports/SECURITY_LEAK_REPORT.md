# SECURITY LEAK REPORT — argos-ats

> Generado: 2026-06-17
> Auditor: Production Engineering Agent
> Estado: ⚠️ SECRETOS LOCALES DETECTADOS — Repositorio git limpio

---

## Resumen Ejecutivo

| Métrica | Valor |
|---|---|
| Archivos .env en disco | 2 (`./.env`, `apps/analytics-engine/.env`) |
| Secretos reales en disco | 3 (1 Telegram token + 2 Binance testnet credenciales) |
| Secretos en git history | 0 ✅ |
| .env en git tracking | 0 ✅ (ignorados por `.gitignore`) |
| Env var logging en código | 0 ✅ |
| Hardcoded credenciales en código | 0 ✅ |

**Conclusión**: el repositorio git está limpio. Los secretos existen solo en `.env` locales que están correctamente ignorados. Sin embargo, los archivos `.env` contienen credenciales reales que deben ser eliminadas y reemplazadas por un secret manager.

---

## Hallazgos Detallados

### 🔴 CRÍTICO — Secretos en archivos .env locales

| # | Archivo | Línea | Tipo | Secreto | Severidad | Acción Requerida |
|---|---|---|---|---|---|---|
| L1 | `./.env` | 17 | Telegram Bot Token | `8618962250:AAEF7dnuW74yI_Gq1YJtoNqSUMZIqZyuNos` | 🔴 CRÍTICO | Rotar token inmediatamente. Reemplazar por placeholder. Migrar a Infisical. |
| L2 | `./.env` | 18 | Telegram Chat ID | `2026849598` | 🔴 CRÍTICO | Reemplazar por placeholder. Migrar a Infisical. |
| L3 | `apps/analytics-engine/.env` | 4 | Binance Testnet API Key | `jC7ouOdQuLsGMKHlEa97OFlVCT2irFxBQMz5qRopUcjNyNbylG6Ab79kUFqmF8FZ` | 🔴 CRÍTICO | Reemplazar por placeholder. Migrar a Infisical. |
| L4 | `apps/analytics-engine/.env` | 5 | Binance Testnet Secret | `fBK7nk3BneGe4becA2ctQVmTIpzkhy19S5lBDsrh2U1KufwSVkiQfsaCv4S4CRe8` | 🔴 CRÍTICO | Reemplazar por placeholder. Migrar a Infisical. |

### 🟢 SIN RIESGO — Archivos .env.example (trackeados)

| Archivo | Estado | Contenido |
|---|---|---|
| `./.env.example` | ✅ Seguro | Placeholders vacíos |
| `apps/data-engine/.env.example` | ✅ Seguro | Placeholders vacíos |
| `apps/analytics-engine/.env.example` | ✅ Seguro | Placeholders vacíos |

### 🟢 SIN RIESGO — Git History

- `git log -p --all` — Búsqueda de patrones `api_key`, `api_secret`, `token`, `telegram`, `binance`: **0 resultados**
- `git log -p --all -- ".env"` — **0 archivos .env commiteados**
- `.gitignore` incluye `.env` y `*.env` desde el commit inicial (e8b3572)

---

## Mapa de Variables de Entorno por Servicio

### data-engine (NestJS — `process.env.*`)

| Variable | ¿Sensible? | ¿Preflight? | ¿En .env actual? |
|---|---|---|---|
| `ARGOS_BROKER_URL` | No (solo host) | ✅ | Sí |
| `EXCHANGE_WS_URL` | No | ❌ (sería bueno) | Sí |
| `EXCHANGE_TYPE` | No | ❌ | No |
| `SYMBOL` | No | ❌ | Sí |
| `STREAM_PREFIX` | No | ❌ | Sí |
| `TICK_BUFFER_CAP` | No | ❌ | Sí |
| `ARGOS_HISTORICAL_DIR` | No | ❌ | No |
| `TELEGRAM_BOT_TOKEN` | 🔴 SÍ | ❌ (nuevo) | Sí (🔴 REAL) |
| `TELEGRAM_CHAT_ID` | 🔴 SÍ | ❌ (nuevo) | Sí (🔴 REAL) |
| `DISCORD_WEBHOOK_URL` | 🔴 SÍ | ❌ (nuevo) | Sí (vacío) |
| `ENVIRONMENT_MODE` | No | ✅ (implícito) | Sí |

### analytics-engine (FastAPI — `os.environ.get()`)

| Variable | ¿Sensible? | ¿Preflight? | ¿En .env actual? |
|---|---|---|---|
| `ENVIRONMENT_MODE` | No | ✅ | Sí |
| `ARGOS_BROKER_URL` | No | ✅ | Sí |
| `BINANCE_TESTNET` | No | ❌ | Sí |
| `BINANCE_TESTNET_API_KEY` | 🔴 SÍ | ✅ (solo LIVE) | Sí (🔴 REAL) |
| `BINANCE_TESTNET_SECRET` | 🔴 SÍ | ✅ (solo LIVE) | Sí (🔴 REAL) |
| `EXCHANGE_API_KEY` | 🔴 SÍ | ✅ (solo LIVE) | No |
| `EXCHANGE_API_SECRET` | 🔴 SÍ | ✅ (solo LIVE) | No |
| `EXCHANGE_PASSPHRASE` | 🔴 SÍ | ❌ (opcional) | No |
| `EXCHANGE_ID` | No | ❌ | No |
| `USE_PYTORCH` | No | ❌ | No |
| `ARGOS_ENV_MODE_FILE` | No | ❌ | No |
| `RISK_PCT` | No | ❌ | Sí |
| `DRAWDOWN_PCT` | No | ❌ | Sí |

---

## Acciones Correctivas (Priorizadas)

| Prioridad | Acción | FASE |
|---|---|---|
| P1 | Rotar Telegram token (está comprometido — texto plano en disco) | F2 |
| P1 | Eliminar Binance testnet credenciales de .env | F2 |
| P1 | Reemplazar valores reales por placeholders en .env | F2 |
| P2 | Crear schema unificado `required_secrets.json` | F5 |
| P2 | Crear preflight check compartido (Python + TS) | F5 |
| P2 | Integrar preflight en bootstrap de ambos servicios | F6 |
| P3 | Crear scripts de inicio Infisical para DEV/STAGING | F4 |
| P3 | Crear CI security audit script | F8 |
| P4 | Extender preflight para todos los modos (no solo LIVE) | F5 |

---

## Estado Final Esperado

- ✅ `.env` → template local sin valores reales
- ✅ `.env.example` → fuente de verdad para schema de variables
- ✅ Preflight en data-engine + analytics-engine
- ✅ Infisical para DEV/STAGING
- ✅ Producción con secrets vía sistema operativo/orchestrator
- ✅ CI audit sin falsos positivos
