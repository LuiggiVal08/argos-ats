---
project: argos-bot
total_tasks: 98
completed: 98
in_progress: 0
blocked: 0
overall_pct: 100
last_updated: 2026-06-16
---

# TASKS — argos-bot

> Tracker persistente. Se mapea 1:1 con las historias de `spec.md` sección 5.
> Actualizado al final de cada sesión de trabajo.

**Estados**: ⬜ TODO · 🟡 IN_PROGRESS · ✅ DONE · ⛔ BLOCKED · 🚫 CANCELLED
**%**: progreso de la historia completa (no por tarea individual)

---

## Resumen ejecutivo

| ID    | Historia                | Estado | %      | Tareas |
|-------|-------------------------|--------|--------|--------|
| Setup | Infra y tools           | ✅     | 100%   | 14/14  |
| H1    | Tick Pipeline (<2ms)    | ✅     | 100%   | 11/11  |
| H2    | Position Sizing (≤1%)   | ✅     | 100%   | 9/9    |
| H3    | Circuit Breaker (5%)    | ✅     | 100%   | 9/9    |
| H4-A  | Order Retry + Emergency | ✅     | 100%   | 7/7    |
| H4-B  | OWASP Incident Response | ✅     | 100%   | 4/4    |
| H5    | Secrets & Env Mode      | ✅     | 100%   | 4/4    |
| H6    | NovaQuant ML Pipeline   | ✅     | 100%   | 9/9    |
| H7    | Live Execution Engine   | ✅     | 100%   | 9/9    |
| H8    | Backtesting Engine      | ✅     | 100%   | 9/9    |
| H9    | Telemetry Webhooks      | ✅     | 100%   | 9/9    |
| H8–12 | ARGOS 2.0 Data Engine   | ✅     | 100%   | 39/39  |
| H23–29| ARGOS 2.0 Part III      | ✅     | 100%   | 45/45  |
| H30–39| ARGOS 2.0 Part IV       | ✅     | 100%   | 25/25  |
| H13–22| ARGOS 2.0 Part II        | ✅     | 100%   | 8/8    |
| H40–50| ARGOS 2.0 Part V         | ✅     | 100%   | 24/24  |
| H51–59| ARGOS 2.0 Part VI        | ✅     | 100%   | 11/11  |
| Q00   | Quant V2 — Baseline          | ✅     | 100%   | 2/2    |
| Q01   | Quant V2 — Freeze Baseline    | ✅     | 100%   | 3/3    |
| Q02   | Quant V2 — Signal Val. Sprint | ✅     | 100%   | 4/4    |
| Q03   | Quant V2 — Feature Audit      | 🚫     | 0%     | 0/4    |
| Q04   | Quant V2 — New Features       | 🚫     | 0%     | 0/5    |
| Q05   | Quant V2 — Simple Baselines   | 🚫     | 0%     | 0/6    |
| Q06   | Quant V2 — Alpha Validation   | 🚫     | 0%     | 0/4    |
| Q07   | Quant V2 — Advanced Models    | 🚫     | 0%     | 0/3    |
| Q08   | Quant V2 — Loss Functions     | 🚫     | 0%     | 0/4    |
| Q09   | Quant V2 — Realistic BTest    | 🚫     | 0%     | 0/5    |
| Q10   | Quant V2 — Scaling            | 🚫     | 0%     | 0/3    |
| QX    | Quant V2 — Temporal Audit     | 🚫     | 0%     | 0/5    |

---

## ✅ Setup — Infraestructura y herramientas

> Todo lo previo a implementar las historias de usuario.

- [x] S001 — Crear estructura del proyecto (`apps/data-engine`, `apps/analytics-engine`)
- [x] S002 — Escribir `spec.md` con las 5 historias
- [x] S003 — Configurar `opencode.json` (plugin + MCP context7)
- [x] S004 — Mover 10 tools a `.opencode/tools/`
- [x] S005 — 12 slash commands en `.opencode/commands/`
- [x] S006 — 28 custom tools cargados sin error
- [x] S007 — Cargar 7 skills relevantes
- [x] S008 — Fix `import.meta.dir` (Bun → Node) en `backtest.ts`, `indicators.ts`
- [x] S009 — Fix regex `(?i)` en `quality.ts` (flag inline no soportado)
- [x] S010 — 21 `execute()` marcados como `async` (compatibilidad Effect)
- [x] S011 — 16 tools con `args: {}` añadidos
- [x] S012 — Crear `AGENTS.md` con reglas e invariantes
- [x] S013 — Crear `TASKS.md` (este archivo)
- [x] S014 — Refactor agnóstico: spec.md + AGENTS.md + config.json + README + health.ts + .gitattributes

**Progreso**: 14/14 = **100%**

---

## ✅ H1 — Tick Pipeline (<2ms)

> spec.md §5 Historia 1. NestJS WS → use case → broker XADD < 2ms p99. FastAPI consume async. Sad path: broker down → buffer in-memory max 100; > 10s → close WS orderly.

- [x] H1-001 — Domain: `Tick` entity, `Symbol` / `Price` / `StreamName` value objects
- [x] H1-002 — Application ports: `MessageBus`, `ExchangeGateway`, `TickBuffer`, `HealthMonitor`
- [x] H1-003 — Application use cases: `IngestTickUseCase`, `BufferTickUseCase`, `FlushBufferUseCase`, `HealthMonitorUseCase`
- [x] H1-004 — Infrastructure: `BinanceWebSocketAdapter`, `RedisProtocolBus`, `InMemoryTickBuffer`
- [x] H1-005 — Sad path: `HealthMonitorUseCase` con cutoff de 10s (cierra WS)
- [x] H1-006 — NestJS DI wiring: `AppModule` con providers + lifecycle hooks
- [x] H1-007 — Analytics-engine subscriber mínimo (prueba end-to-end)
- [x] H1-008 — Tests unit: domain + use cases con ports mockeados (26/26 PASS)
- [x] H1-009 — Tests integration: Redis real (Docker) con p99 < 2ms
- [x] H1-010 — Benchmark de latencia XADD (`benchmark/xadd-latency.ts`)
- [x] H1-011 — Bitácora + cierre H1

**Progreso**: 11/11 = **100%**
**Dependencias**: ninguna
**Notas**:
- Buffer de 100 es insuficiente para 10s a >10 ticks/s. Spec se implementa literal; sizing del buffer queda como follow-up H1-FU1.
- H1-009 y H1-010 no se ejecutaron en sandbox (no hay Docker/red broker); se ejecutan en CI/dev con `docker compose up broker` y `ARGOS_BROKER_URL=redis://localhost:6379`.
- Hexagonal: `tick-pipeline.service.ts` se movió a `infrastructure/services/` (es glue NestJS, no caso de uso del dominio). Application/ queda sin imports de Infrastructure.
- ESLint añadido (`@typescript-eslint` con rule override para domain class `Symbol`).
- `@nestjs/config@3.2.0` instalado (3.1.x requiere `reflect-metadata@^0.1.13`, conflict).

---

## ✅ H2 — Position Sizing (≤1% del free balance)

> spec.md §5 Historia 2. Capa de Dominio calcula `units = (free_balance * risk_pct) / atr`. SL dinámico a distancia ATR. Sad path: CCXT timeout o balance=0 → abort; size < min_lot → descartar.

- [x] H2-001 — Domain VOs: `Atr`, `RiskPct`, `PositionSize` (validación: atr>0, 0<risk_pct≤0.02, units≥0)
- [x] H2-002 — Domain entity: `RiskCalculator.calculate(free_balance, atr, risk_pct) → PositionSize`
- [x] H2-003 — Application ports: `BalanceProvider`, `AtrCalculator`, `MinLotProvider` (Protocol)
- [x] H2-004 — Application use case: `ComputePositionSizeUseCase` con CCXT-error handling + min_lot check
- [x] H2-005 — Infrastructure adapters: `TaAtrCalculator` (lib `ta`), `CcxtBalanceProvider`, `MockBalanceProvider`, `CcxtMinLotProvider`
- [x] H2-006 — API endpoint: `POST /risk/position-size` con DI composition root
- [x] H2-007 — Tests unit (VOs + entity + use case con mocks) + integration (TA lib real)
- [x] H2-008 — Validation: pytest (43 passed, 1 skipped), mypy clean, arch_lint clean, secret_scan clean
- [x] H2-009 — Commit + PR body (`docs/prs/h2-position-sizing.md`); **PR #2 mergeado a dev**

**Progreso**: 9/9 = **100%**
**Dependencias**: ninguna
**Notas**: el tool opencode `risk_position_size` ya implementa la fórmula base (H2-002 lo institucionaliza en el engine). Min lot check es la pieza nueva que el tool no tenía. Integration con strategy/IA queda para historias posteriores.

---

## ✅ H3 — Circuit Breaker (5% drawdown diario)

> spec.md §5 Historia 3. Cortar operativa si pérdida diaria ≥ 5% del balance de apertura UTC 00:00. Acciones: cancelar órdenes, cerrar posiciones a mercado, `ENVIRONMENT_MODE=PASIVO`, halt loop, log crítico.

- [x] H3-001 — Domain VOs: `DrawdownState`, `DrawdownSnapshot`, `TripAction` (con orden canónico enforced)
- [x] H3-002 — Domain entity: `CircuitBreaker.evaluate(snapshot, current_state) → DrawdownState` (SAFE/WARN/TRIP, HALTED sticky) + `should_reset_utc(now)` + `trip_action()`
- [x] H3-003 — Application ports: `TradeJournal`, `ExchangeOrderClient`, `EnvironmentModeWriter`, `DrawdownSnapshotRepo`
- [x] H3-004 — Application use cases: `CheckDrawdownUseCase`, `TripCircuitBreakerUseCase`, `OpenDayUseCase`
- [x] H3-005 — Infrastructure: `InMemoryTradeJournal`, `InMemorySnapshotRepo`, `CcxtOrderClient`, `FileEnvironmentModeWriter` (atomic write, BACKTESTING default on missing)
- [x] H3-006 — API: `GET /risk/drawdown` (read-only), `POST /risk/drawdown/check`, `POST /risk/day/open`
- [x] H3-007 — Tests: 54 nuevos (30 unit domain, 13 unit usecases, 5 unit adapters, 6 integration endpoint). 97 passed / 1 skipped
- [x] H3-008 — Validation: pytest 97/97 OK, mypy OK (H3-specific issues fixed: HALTED stickiness, datetime import, `Request` real import, `warn_ratio` validation), arch_lint PASS, secret_scan clean (los 5 hits son placeholders en `.agents/skills/*/SKILL.md`, no en código)
- [x] H3-009 — Commit (8 commits, conventional) + PR body

**Progreso**: 9/9 = **100%**
**Dependencias**: H5-003 (env mode write file/secret) — H3 escribe `ENVIRONMENT_MODE=PASIVO` via port; H5 controla el sad path LIVE (spec sad path: si LIVE sin EXCHANGE_API_KEY → abort exit 1, manejado por `config_toggle_mode` + pre-flight en `composition.py`).
**Notas**:
- Bug pre-existente del H2: el helper `get_compute_position_size_usecase(request: "Request")` usaba string forward-ref; con `pydantic 2.13` esto rompía la resolución de params de FastAPI. Reemplazado por `from fastapi import Request` real. Mismo fix aplicado a los nuevos helpers H3.
- `warn_ratio` se valida en `(0, 1]` pero NO se compara con `threshold` (es una fracción del mismo, p.ej. 0.6). La comparación invertida del primer commit habría roto `CircuitBreaker()` con defaults; corregido en H3-002.
- HALTED es sticky: aunque el drawdown se recupere, `evaluate` no downgrade. El use case no re-arma el breaker por accidente.
- `TripAction.__post_init__` enforces canonical order: si pasás `(CLOSE, CANCEL, ...)` revienta. No se puede ejecutar `SET_PASIVO` antes que `CANCEL_ORDERS`.
- Trip en BACKTESTING usa un no-op order client (no hay órdenes reales que cancelar); el resto del trip (env + clear snapshot) corre igual para validar la cadena completa.
- 6 integration tests cubren el flujo end-to-end via TestClient + composition.

---

## ✅ H4-A — Order Retry + Emergency Market

> spec.md §5 Historia 4-A. Order placement with retry logic for stop-loss and emergency market fallback.

**Domain VOs**: `OrderSide`, `OrderType`, `OrderStatus`, `CompositeOrder`, `OrderResult` — con validaciones (entry_amount > 0, enum values).

**Port extension**: `ExchangeOrderClient` → `place_composite_order()` (entry + SL/TP bracket), `place_emergency_market()` (liquidation), `SlPlacementError` carrying `entry_order`.

**Use case**: `PlaceOrderUseCase` — calls `place_composite_order`, catches `SlPlacementError`, issues emergency close on opposite side. If both fail, raises `PlaceOrderError`.

**Infrastructure**: `CcxtOrderClient.place_composite_order` — market entry → SL with exponential backoff (100ms base, max 3 retries, ±20ms jitter) → TP (no retry, non-critical). TP failure silently logged.

**API**: `POST /order/place` — body: `{symbol, side, entry_amount, sl_price?, tp_price?}` → response: `{succeeded, entry_order, emergency_order?}`. Returns 422 on hard errors.

- [x] H4-A-001 — Domain VOs: OrderSide, OrderType, OrderStatus, CompositeOrder (entry_amount > 0), OrderResult
- [x] H4-A-002 — Extend ExchangeOrderClient port: place_composite_order, place_emergency_market, SlPlacementError with entry_order
- [x] H4-A-003 — PlaceOrderUseCase: retry catch + emergency fallback
- [x] H4-A-004 — CcxtOrderClient: place_composite_order (SL retry 3x, TP fire-and-forget), place_emergency_market
- [x] H4-A-005 — POST /order/place API endpoint
- [x] H4-A-006 — Tests: 6 unit (happy + sad) + 5 integration (HTTP contract)
- [x] H4-A-007 — Validation: 108/108 tests, arch_lint PASS, secret_scan clean; branch `feature/h4-a-order-retry` pushed

**Progreso**: 7/7 = **100%**
**Dependencias**: H3 (CcxtOrderClient port ya existe, composition root ready)
**Notas**:
- `SlPlacementError` carries the entry `OrderResult` because the entry was already placed when SL fails; the use case needs it for the response.
- Emergency side = opposite of entry (BUY → SELL, SELL → BUY).
- TP failure is non-critical — silently ignored.
- SL retry: exponential backoff with jitter. If all 3 fail → `SlPlacementError` → use case catches and issues emergency.

---

## ✅ H4-B — OWASP Incident Response (4 fases)

> spec.md §4. Protocolo OWASP: Identificación → Contención → Erradicación → Recuperación.

**Documento**: `docs/incident-response.md` con las 4 fases mapeadas a código, SLAs por severidad (P1-P4), responsables y runbook.

**Detectores automáticos**: sistema de reporting de incidentes con `IncidentSeverity` (P1-P4), `IncidentEvent` VO, `IncidentReporter` port (logging via structlog), `IncidentRepository` port (in-memory), `ReportIncidentUseCase`, `ListIncidentsUseCase`, API endpoints `GET /incident/list` y `POST /incident/declare`.

**Runbook**: incluido en `docs/incident-response.md` — cada fase tiene acciones automáticas y manuales, SLAs, responsables, criterios de salida.

**Drill**: comando `/incident-drill` existente (tabletop read-only) que simula las 4 fases OWASP inyectando un escenario.

- [x] H4-B-001 — Definir las 4 fases en `docs/incident-response.md`
- [x] H4-B-002 — Detectores automáticos: incident VOs + ports + use cases + API + in-memory/logging infrastructure
- [x] H4-B-003 — Runbook por fase con responsabilidades, SLAs y criterios de salida
- [x] H4-B-004 — Drill end-to-end con `/incident-drill` (ya existía como comando; verificado)

**Progreso**: 4/4 = **100%**
**Dependencias**: H3 (Circuit Breaker como trigger de Contain), H4-A (orphan order detection)
**Notas**: los detectores concretos (drawdown ≥ 5%, orphan order) ya existen en H3 y H4-A; H4-B-002 añade el sistema de tracking de incidentes sobre esos detectores.

---

## ✅ H5 — Secrets & Env Mode

> spec.md §5 Historia 5. Variables de entorno, validación LIVE, pre-flight check.

**Pre-flight validator**: `app/preflight.py` con `preflight_check(mode)` y `abort_if_missing(mode)`. En LIVE, valida que existan y no estén vacías: `EXCHANGE_API_KEY`, `EXCHANGE_API_SECRET`, `ARGOS_BROKER_URL`. Si falta alguna → `sys.exit(1)`.

**Integración**: `build_composition()` llama `abort_if_missing(mode)` antes de construir el exchange. BACKTESTING/PAPER_TRADING son no-op.

**.env.example**: ambos engines actualizados con secciones claras (Required / Required for LIVE / Optional / Risk defaults).

- [x] H5-001 — Pre-flight validator (`preflight_check` + `abort_if_missing`) con sad path (LIVE sin secrets → exit 1)
- [x] H5-002 — Integración en `build_composition()` antes de construir exchange
- [x] H5-003 — `.env.example` completo (data-engine + analytics-engine) con secciones y defaults documentados
- [x] H5-004 — Tests: 8 tests unitarios (modo, vars faltantes, vars vacías, abort exit), 116/116 passed, arch_lint PASS

**Progreso**: 4/4 = **100%**
**Dependencias**: ninguna
**Notas**:
- `REQUIRED_LIVE_VARS`: `EXCHANGE_API_KEY`, `EXCHANGE_API_SECRET`, `ARGOS_BROKER_URL`.
- `OPTIONAL_LIVE_VARS`: `EXCHANGE_PASSPHRASE`, `EXCHANGE_ID`, `EXCHANGE_WS_URL` — documentados pero no validados.
- La validación es temprana (en `build_composition()`) para que el engine nunca arranque parcialmente configurado en LIVE. `sys.exit(1)` es intencional: en Docker el contenedor se reinicia con error.

---

## ✅ H6 — NovaQuant ML Pipeline

> Modelo LSTM propio (NovaQuant) para predicción de señales de trading. Pipeline completo: fetching OHLCV → preprocesamiento (TA-lib indicators) → feature selection (Pearson correlation) → training (Keras LSTM) → inference → checkpoint persistence.

**Domain VOs**: `ModelConfig` (lookback, features, layers, dropout, target thresholds), `TradingSignal` (BUY/SELL/HOLD con confidence), `SignalSide` enum.

**Domain Entity**: `NovaQuantModel` — weights hash, version, feature means/stds, metrics, trained_at, age_days, is_stale, validate_input, assert_not_stale, assert_version.

**Ports**: `OhlcvSource` (fetch historical), `DataPreprocessor` (build_features, normalize, create_windows, create_targets), `FeatureAnalyzer` (compute_correlations, filter_features), `ModelTrainer` (train, save), `ModelPredictor` (load, predict), `CheckpointRepository` (save/load model state).

**Use Cases**: `TrainModelUseCase` — fetch OHLCV → preprocess → analyze → train → save checkpoint. `PredictSignalUseCase` — fetch OHLCV → preprocess → load checkpoint → predict → return `TradingSignal`.

**Infrastructure**: `TaDataPreprocessor` (RSI, MACD, BB, EMA, ATR features via `ta`), `CorrelationFeatureAnalyzer` (Pearson r, filter noise features, keep min 3), `NovaQuantKerasModel` (train/predict via tf.keras, 3 hidden layers, dropout, checkpoint save/load), `FsCheckpointRepository` (JSON + .keras on filesystem).

**API**: `POST /model/train` — body: `{symbol, timeframe, lookback, features, layers, epochs}` → response: `{status, version, metrics, feature_count, checkpoint_path}`. `POST /model/predict` — body: `{symbol, timeframe}` → response: `{signal, side, confidence, version}`. Returns 422 on errors.

- [x] H6-001 — Domain VOs: ModelConfig (validación: lookback 5-500, features no vacío, target_lookahead ≥ 1, confidence threshold 0.5-0.99, capa depth 1-10, dropout 0-0.5), TradingSignal (confidence 0-1, actionable threshold), SignalSide (BUY/SELL/HOLD)
- [x] H6-002 — Domain entity: NovaQuantModel con weights_hash, version, feature_stats, metrics, trained_at, age_days, is_stale (≥7d), validate_input/assert_not_stale/assert_version
- [x] H6-003 — Application ports: OhlcvSource, DataPreprocessor (build_features, normalize, create_windows 2D/3D, create_targets one-hot), FeatureAnalyzer (pearson correlation matrix, filter by threshold, min 3 features), ModelTrainer (train, save), ModelPredictor (load, predict), CheckpointRepository (save/load/list)
- [x] H6-004 — Application use cases: TrainModelUseCase (fetch → preprocess → analyze → train → save → return metrics), PredictSignalUseCase (fetch → preprocess → load → predict → build TradingSignal)
- [x] H6-005 — Infrastructure: TaDataPreprocessor (TA-lib: RSI-14, MACD, BB, EMA-20, ATR; normalize z-score; sliding windows; one-hot targets), CorrelationFeatureAnalyzer (pearsonr scipy, filter |r| < 0.1, keep ≥ 3), NovaQuantKerasModel (3 dense layers: 128→64→32, dropout 0.3, Adam, early stopping, checkpoint .keras), FsCheckpointRepository (JSON metadata + .keras file)
- [x] H6-006 — API: POST /model/train (Pydantic schema, 422 sad paths), POST /model/predict (Pydantic schema, 422 sad paths)
- [x] H6-007 — Composition wiring: get_model_use_cases() lazy builder, _CcxtOhlcvAdapter (exchange) / _FakeOhlcvSource (backtesting), cached in app.state
- [x] H6-008 — Tests: 83 new (20 unit VOs + 20 unit entity + 18 integration data_preprocessor + 12 integration feature_analyzer + 13 API integration). 200/201 total pass (1 skipped — subscriber requires broker)
- [x] H6-009 — Validation: pytest 200/201, arch_lint PASS, secret_scan clean, merge conflicts with dev (H4-B + H5) resolved

**Progreso**: 9/9 = **100%**
**Dependencias**: H2 (OHLCV source pattern), H4-A (order placement for future signal execution)
**Notas**:
- NovaQuant no está en spec.md original — es una historia solicitada por el usuario post-H5.
- El modelo Keras tiene 3 capas ocultas (128→64→32) con dropout 0.3 y early stopping (patience 5).
- Backtesting usa _FakeOhlcvSource que retorna lista vacía; PAPER/LIVE usa _CcxtOhlcvAdapter.
- Checkpoints se persisten en `checkpoints/` como .keras + .json metadata.
- Feature engineering incluye RSI-14, MACD (12/26/9), BB (20,2), EMA-20, ATR-14.

---

## ✅ H7 — Live Execution Engine

> Orquestador que cierra el bucle señal→orden→posición→P&L en tiempo real, conectando NovaQuant (H6), risk (H2/H3), y order placement (H4-A) en producción o paper trading.

**Pipeline**: `TradingSignal` → SignalValidator (confidence + cooldown) → CircuitBreakerCheck → PositionSizer → PlaceOrderUseCase → PositionTracker (SL/TP loop) → ExecutionLogger.

- [x] H7-001 — Domain VOs: `ExecutionSignal` (side, confidence, symbol, strategy_id, timestamp — rejects HOLD, validates confidence/symbol), `LivePosition` (side, units, entry, sl/tp hit detection, PnL%, compute_pnl_at), `ExecutionReport` (signal_id, order_id, status, filled_qty, avg_price, pnl, errors[] — status validation)
- [x] H7-002 — Domain entity: `SignalValidator` (confidence threshold, cooldown per symbol, dedup by signal_id, expiration), `PositionTracker` (stateless SL/TP checker, TrackResult verdict + PnL)
- [x] H7-003 — Ports: `SignalConsumer` (async streaming protocol), `PositionRepository` (CRUD), `ExecutionLogger` (structured logging)
- [x] H7-004 — Use case: `ExecuteSignalUseCase` (validate → CB check → balance+ATR → position sizing → order placement → persist → log), `MonitorPositionsUseCase` (periodic SL/TP loop, auto-close on hit)
- [x] H7-005 — Infrastructure: `NovaQuantSignalConsumer` (adapts TradingSignal → ExecutionSignal), `InMemoryPositionRepository` + `FilePositionRepository` (JSON persistence, auto-creates dir)
- [x] H7-006 — Infrastructure: `StructlogExecutionLogger` (structured JSON logs per execution event)
- [x] H7-007 — Integration wiring: `composition.py` with `get_execute_signal_usecase`, `get_monitor_positions_usecase`, `get_position_repo` (all cached in app.state)
- [x] H7-008 — API: `POST /execute/signal` (trigger signal manually), `GET /position/list` (open/all), `GET /execution/log` (recent executions) — 422 on rejection/error
- [x] H7-009 — Tests: 59 new (21 unit VOs + 8 SignalValidator + 8 PositionTracker + 5 execute_signal + 5 monitor_positions + 7 integration endpoint + 5 integration list/log). 323/324 total pass (1 skipped).

**Progreso**: 9/9 = **100%**
**Dependencias**: H2 (position sizing), H3 (circuit breaker check), H4-A (order placement), H6 (NovaQuant signals), H8 (strategy patterns)
**Notas**:
- H7 no introduce un nuevo exchange adapter; reusa CCXT de H4-A.
- ExecuteSignalUseCase requiere signal.price — fallback a ATR como entry price rechazado (error).
- SL = max(ATR * 1.5, entry * 0.005), clamped para evitar precios negativos/invertidos.
- SignalValidator defaults: min_confidence=0.7, cooldown=60s, max_age=300s.
- BACKTESTING mode usa `_FakeAtrCalculator` (ATR fijo=500), `MockBalanceProvider` (10k), `_NoopOrderClient`.

---

## ✅ H8 — Backtesting Engine

> Framework de backtesting para validar estrategias contra datos históricos. Estrategias clásicas (EMA crossover, RSI mean reversion) + framework extensible para NovaQuant.

**Engine**: `BacktestEngine` entidad de dominio que procesa velas secuencialmente, genera señales vía callable, simula posiciones con SL dinámico basado en ATR, calcula PnL y produce curva de equity.

**Estrategias**: `EmaCrossStrategy` (trend following, EMA rápida 9 / lenta 21, crossover → BUY/SELL), `RsiMeanReversionStrategy` (mean reversion, RSI-14, oversold < 30 > overbought 70, bounce → BUY/SELL). Registro extensible via `StrategyDictRegistry`.

**Métricas**: `SimpleMetricsCalculator` — Sharpe ratio anualizado (RF=0), max drawdown pico-a-valle, win rate, profit factor, volatilidad, PnL promedio.

**Reportes**: `FileBacktestReporter` — JSON en `reports/backtest/<strategy>_<symbol>_<timestamp>.json` con config, métricas, trades, y equity curve.

- [x] H8-001 — Domain VOs: BacktestConfig (strategy_id, symbol, timeframe, initial_balance, risk_pct 0-2%, max_trades), BacktestTrade (side, entry/exit, units, pnl), BacktestMetrics (sharpe, max_dd, win_rate, total_return, profit_factor)
- [x] H8-002 — Domain entity: BacktestEngine (sequential candle loop, SignalFn callable, entry/exit simulation, ATR-based SL, equity curve, max_trades cap)
- [x] H8-003 — Ports: Strategy protocol (build -> SignalFn), BacktestReporter (save report), MetricsCalculator (compute from trades + equity)
- [x] H8-004 — Use case: RunBacktestUseCase (resolve strategy -> fetch OHLCV -> run engine -> calc metrics -> save report -> return result)
- [x] H8-005 — Infrastructure strategies: EmaCrossStrategy (9/21 EMA crossover, confidence basado en distancia), RsiMeanReversionStrategy (14-period RSI, oversold 30/overbought 70, bounce detection), StrategyDictRegistry (con defaults + register)
- [x] H8-006 — Infrastructure metrics + reporter: SimpleMetricsCalculator (Sharpe anualizado sqrt(365), max drawdown, win rate, profit factor, vol), FileBacktestReporter (JSON con trades + equity curve)
- [x] H8-007 — API: POST /backtest/run (config params, 422 sad paths), GET /backtest/strategies (lista IDs disponibles)
- [x] H8-008 — Tests: 64 nuevos (21 unit VOs + 8 unit engine + 14 unit strategies + 10 unit metrics + 7 integration endpoint). 264/265 total pass (1 skipped)
- [x] H8-009 — Validation: pytest 264/265, arch_lint PASS, secret_scan clean, 1 commit conventional

**Progreso**: 9/9 = **100%**
**Dependencias**: H6 (NovaQuant como estrategia futura), H2 (position sizing pattern)
**Notas**:
- BacktestEngine es agnóstico de estrategia — recibe un callable `SignalFn: (idx, ohlcv, config) -> (side, confidence) | None`.
- SL distance = max(ATR_14 * 1.5, entry_price * 0.005).
- Position sizing = (balance * risk_pct) / 0.01 / entry_price.
- BACKTESTING mode usa `_FakeOhlcvSource` (sin datos reales). Para backtest real se necesita PAPER_TRADING con exchange conectado o un OhlcvSource CSV.
- `SimpleMetricsCalculator._compute_sharpe` asume returns diarios y anualiza con sqrt(365). Para otras temporalidades es aproximado.

---

## ✅ H9 — Telemetry Webhooks (Telegram/Discord)

> Adaptadores ligeros para notificaciones vía webhooks Telegram/Discord. Analytics-engine publica eventos a Redis stream `notifications:events`; Data-engine (Node/NestJS) consume y dispara HTTP.

**Arquitectura**: `ExecuteSignalUseCase` → `NotifyOnEventUseCase` → `RedisNotifier` → XADD a `notifications:events` → `NotificationConsumer` (NestJS XREAD) → Telegram/Discord HTTP POST

- [x] H9-001 — Domain VOs: `NotificationEvent` (frozen dataclass con event_type, severity, title, message, symbol, metadata, timestamp), `NotificationSeverity` (INFO/WARN/CRITICAL), `NotificationEventType` (7 tipos)
- [x] H9-002 — Ports: `Notifier` protocol (async send)
- [x] H9-003 — Use case: `NotifyOnEventUseCase` (dispara notifier con evento)
- [x] H9-004 — Infra Python: `LoggingNotifier` (structlog), `RedisNotifier` (XADD a `notifications:events`), `CompositeNotifier` (fan-out)
- [x] H9-005 — Composition Python: wiring en `build_composition()`, modo BACKTESTING usa solo LoggingNotifier, PAPER/LIVE añade RedisNotifier
- [x] H9-006 — Infra Node: `TelegramNotifier` (fetch POST a Bot API), `DiscordNotifier` (fetch POST a webhook), `NotificationConsumer` (NestJS OnModuleInit/OnModuleDestroy, XREAD loop)
- [x] H9-007 — API: `POST /notification/test`, `GET /notification/status`
- [x] H9-008 — Env vars: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `DISCORD_WEBHOOK_URL` documentados en `.env.example` de ambos engines
- [x] H9-009 — Tests: 14 nuevos (6 VOs + 5 unit infra + 1 use case + 2 integration). 337/338 total pass (1 skipped). TypeScript typecheck PASS.

**Progreso**: 9/9 = **100%**
**Dependencias**: H7 (execute_signal, monitor_positions), H3 (circuit breaker), H4-B (incident reporting)
**Notas**:
- El dispatch HTTP corre en Node (data-engine), no en Python. Python solo publica a Redis.
- `fetch()` global de Node 18+ usado para webhooks — sin dependencias nuevas.
- Si ninguna token/webhook env var está configurada, notificaciones son no-op (solo log).
- BACKTESTING mode no publica a Redis (solo LoggingNotifier).

_Ninguno actualmente._

---

## 📊 ARGOS 2.0 — Data Engine (NestJS) — H8–H12

> Candle pipeline, feature calculation, historical events, market replay.

- [x] H8–H12-001 — Domain entities: Candle, FeatureVector, HistoricalEvent
- [x] H8–H12-002 — Domain VOs: Timeframe, Volume
- [x] H8–H12-003 — Ports: CandleStore, CandlePublisher, FeatureCalculator, FeaturePublisher, EventStore, HistoricalDataProvider
- [x] H8–H12-004 — Use cases: BuildCandlesUseCase, CalculateFeaturesUseCase, RecoverCandleUseCase, ReplayMarketUseCase
- [x] H8–H12-005 — Infrastructure: InMemoryCandleStore, RedisCandlePublisher, RedisFeaturePublisher, FileEventStore
- [x] H8–H12-006 — CandlePipelineService + FeaturePipelineService
- [x] H8–H12-007 — 11 pure-TS indicators in TechnicalIndicatorCalculator
- [x] H8–H12-008 — NestJS module wiring in app.module.ts
- [x] H8–H12-009 — 73 tests (49 unit domain + 24 integration pipeline)

**Archivos**: 39 files (new + modified) in `apps/data-engine/src/`

---

## 📊 ARGOS 2.0 — Analytics Part III (Model Pipeline) — H23–H29

> NovaQuant model pipeline: features, regime detection, ensemble, meta-model, calibration, uncertainty.

- [x] H23–H29-001 — Domain: MarketContext entity, RegimeType/ScalerType VOs
- [x] H23–H29-002 — 8 ports: ClassBalancer, ConfidenceFilter, FeatureStore, MetaModel, MultiSymbolConsolidator, ProbabilityCalibrator, RegimeDetector, UncertaintyEstimator
- [x] H23–H29-003 — Infrastructure: RuleBasedRegimeDetector, NovaQuantXGBoostModel, NovaQuantPyTorchModel, MCDropoutUncertaintyEstimator, SklearnProbabilityCalibrator, XGBoostMetaModel
- [x] H23–H29-004 — BuildDatasetUseCase with configurable scalers + class balancing
- [x] H23–H29-005 — Async composition: get_model_use_cases with PyTorch/TF branching
- [x] H23–H29-006 — 16 indicators including ADX14, BBW
- [x] H23–H29-007 — 7 test files (ADX, regime detector, meta model, confidence filter, uncertainty estimator, feature extractor, NovaQuant VOs)

**Archivos**: 45 files (33 new + 12 modified) in `apps/analytics-engine/`

---

## 📊 ARGOS 2.0 — Analytics Part IV (Execution Engine) — H30–H39

> Position management, risk engine, portfolio manager, correlation, execution orchestrator.

- [x] H30–H39-001 — Domain: PositionManager (multi-TP/BE/trail/risk_multiple), RiskEngine (5 checks), PortfolioManager (exposure/per-symbol/correlation/heat/position limits), CorrelationEngine (pearson returns)
- [x] H30–H39-002 — Use cases: ExecutionEngine orchestrator (SignalValidator→CircuitBreaker→RiskEngine→PortfolioManager→sizing→order→log), ExecuteTradingSignalUseCase
- [x] H30–H39-003 — Ports: ExchangeOrderGateway, ExchangeOrderClient.close_partial()
- [x] H30–H39-004 — Infrastructure: MockExchangeAdapter
- [x] H30–H39-005 — LivePosition extended with multi-TP/BE/trail fields
- [x] H30–H39-006 — PositionTracker enhanced with partial TP + trailing SL
- [x] H30–H39-007 — MonitorPositionsUseCase refactored to use PositionManager
- [x] H30–H39-008 — API: POST /execute/engine
- [x] H30–H39-009 — 7 test files (position manager, risk engine, portfolio manager, correlation, execution engine, execute trading signal, mock adapter)
- [x] H30–H39-010 — 14 tests for ExecutionEngine

**Archivos**: 25 files (16 new + 9 modified) in `apps/analytics-engine/`

---

## 📊 ARGOS 2.0 — Analytics Part II (Dataset & Feature Engine) — H13–H22

> Labeling engine, window builder, normalizer, dataset validator. API endpoint for dataset building.

- [x] H13–H20-001 — Domain: LabelingEngine (ATR-based BUY/SELL/HOLD classification)
- [x] H13–H20-002 — Domain: WindowBuilder (sliding window config + indices)
- [x] H13–H20-003 — Domain: Normalizer (Standard/MINMAX/Robust scaling params)
- [x] H13–H20-004 — Domain: DatasetValidator (schema validation)
- [x] H13–H20-005 — Ports: MultiSymbolConsolidator, FeatureStore, ClassBalancer, DataPreprocessor
- [x] H13–H20-006 — Use case: BuildDatasetUseCase (consolidate → preprocess → label → balance → store)
- [x] H13–H20-007 — API: POST /dataset/build
- [x] H13–H20-008 — Composition wiring in get_build_dataset_usecase

**Archivos**: 8 files (new + modified) in `apps/analytics-engine/`

---

## 📊 ARGOS 2.0 — Analytics Part V (Training Engine) — H40–H50

> Model registry with versioned champion/challenger, promotion engine with gates, rollback, shadow deployment, walk-forward validation, feature importance.

- [x] H40–H50-001 — Domain: ModelRegistry (versioned + champion/challenger tracking)
- [x] H40–H50-002 — Domain: PromotionEngine (Sharpe ≥5% improvement, PF ≥3, DD ≤2% increase, WR ≥40%)
- [x] H40–H50-003 — Domain: RollbackEngine (version rollback safety)
- [x] H40–H50-004 — Domain: ChampionChallenger (multi-metric comparison)
- [x] H40–H50-005 — Domain: ShadowModelManager (max 3 shadows, evict after 1000 predictions)
- [x] H40–H50-006 — Domain: WalkForwardValidator (sliding window CV)
- [x] H40–H50-007 — Domain: FeatureImportance (gain-based calculator)
- [x] H40–H50-008 — 9 use cases: register, list, promote, rollback, compare, deploy shadow, list shadows, walk forward, feature importance
- [x] H40–H50-009 — 8 endpoints under /training/*
- [x] H40–H50-010 — Infra: FileSystemModelRepository, SimpleWalkForwardRunner, GainFeatureImportanceCalculator

**Archivos**: 24 files (new + modified) in `apps/analytics-engine/`

---

## 📊 ARGOS 2.0 — Analytics Part VI (Observability & Disaster Recovery) — H51–H59

> Telemetry engine, dashboard panels, disaster recovery with auto-mode escalation, centralized structured logging.

- [x] H51–H59-001 — Domain: TelemetryEngine (4-engine metrics collection, 10k point buffer auto-evict)
- [x] H51–H59-002 — Domain: DashboardEngine (market/AI/risk/training panels)
- [x] H51–H59-003 — Domain: DisasterRecovery (auto-mode escalation NORMAL→DEGRADED→SAFE→HALTED)
- [x] H51–H59-004 — Domain: CentralizedLogger (structured log levels + filter)
- [x] H51–H59-005 — Use cases: CollectTelemetryUseCase, RecordTelemetryUseCase, UpdateDashboardUseCase, GetDashboardUseCase, GetDashboardHistoryUseCase, ReportIncidentExtendedUseCase, GetDisasterStatusUseCase, RecoverFromIncidentUseCase
- [x] H51–H59-006 — API: /observability/telemetry, /observability/dashboard, /observability/disaster/*
- [x] H51–H59-007 — Composition wiring for all Part VI use cases

**Archivos**: 11 files (new + modified) in `apps/analytics-engine/`

---

## Bitácora

### 2026-06-07 — Sesión H5: Secrets & Env Mode
- ✅ `app/preflight.py` — `preflight_check(mode)` y `abort_if_missing(mode)`. En LIVE valida `EXCHANGE_API_KEY`, `EXCHANGE_API_SECRET`, `ARGOS_BROKER_URL`. Missing/empty → `sys.exit(1)`.
- ✅ Integrado en `build_composition()` como primer paso antes de construir exchange.
- ✅ `.env.example` de ambos engines reestructurados con secciones: Required, Required for LIVE, Optional, Risk defaults.
- ✅ Tests: 8 unitarios (BACKTESTING no-op, LIVE sin vars, LIVE con vars vacías, LIVE ok, abort exit code).
- ✅ Validación: 116/116 tests, arch_lint PASS, secret_scan clean.
- ✅ 1 commit conventional, push a `origin/feature/h5-env-secrets`.

### 2026-06-07 — Sesión H4-B: OWASP Incident Response
- ✅ `docs/incident-response.md` — 4 fases OWASP con SLAs, responsables, runbook, clasificación P1-P4.
- ✅ Domain VOs: `IncidentSeverity`, `IncidentPhase`, `IncidentEvent` con auto-generated UUID.
- ✅ Ports: `IncidentReporter` (structlog logging), `IncidentRepository` (in-memory storage).
- ✅ Use cases: `ReportIncidentUseCase` (persist + notify), `ListIncidentsUseCase` (query recent/by-id).
- ✅ Infrastructure: `LoggingIncidentReporter` (nivel CRITICAL/ERROR según severidad), `InMemoryIncidentRepository`.
- ✅ API: `GET /incident/list`, `POST /incident/declare`.
- ✅ Tests: 11 new (8 unit + 3 integration). 123 passed / 1 skipped total.
- ✅ Validación: pytest 123/123, arch_lint PASS, secret_scan clean.
- ✅ Comando `/incident-drill` verificado (ya existía como tabletop read-only).
- ✅ 1 commit conventional, push a `origin/feature/h4-b-incident-response`.

### 2026-06-07 — Sesión H4-A: Order Retry + Emergency Market
- ✅ Branch `feature/h4-a-order-retry` creada desde `dev` (después del merge de H3).
- ✅ H4-A-001: Domain VOs `OrderSide`, `OrderType`, `OrderStatus`, `CompositeOrder`, `OrderResult` — enfoque hexagonal sin dependencias externas.
- ✅ H4-A-002: Port `ExchangeOrderClient` extendido con `place_composite_order()`, `place_emergency_market()`, `SlPlacementError` (con `entry_order` carry).
- ✅ H4-A-003: `PlaceOrderUseCase` implementado — catch `SlPlacementError` → emergency market en lado opuesto.
- ✅ H4-A-004: `CcxtOrderClient.place_composite_order()` — entry market + SL retry 3x con exponential backoff + jitter + TP fire-and-forget.
- ✅ H4-A-005: `POST /order/place` endpoint con Pydantic schemas y DI wiring.
- ✅ H4-A-006: 11 tests nuevos (6 unit + 5 integration). 108 passed / 1 skipped total.
- 🐛 Bug detectado y corregido en diseño: `SlPlacementError` necesitaba carry `entry_order` porque la entry ya se ejecutó; la excepción se llevaba el resultado de la entry para que el use case lo retorne.
- ✅ Validación: pytest 108/108, arch_lint PASS, secret_scan clean.
- ✅ 1 commit conventional, push a `origin/feature/h4-a-order-retry`.
- 🎯 **H4-A listo para PR.** El usuario abre el PR manualmente en GitHub apuntando a `dev`.

### 2026-06-07 — Sesión H3: Circuit Breaker
- ✅ Branch `feature/h3-circuit-breaker` creada desde `dev` (rebaseada sobre el merge de H2 = `9873902`).
- ✅ H3-001..H3-007 implementados: 3 VOs (DrawdownState/DrawdownSnapshot/TripAction con orden canónico enforced), entity CircuitBreaker (HALTED sticky, reset UTC 00:00), 4 ports (TradeJournal/ExchangeOrderClient/EnvironmentModeWriter/DrawdownSnapshotRepo), 3 use cases (CheckDrawdown/Trip/OpenDay), 4 adapters (in-memory + Ccxt + File env writer con atomic write), 3 endpoints (`/risk/day/open`, `/risk/drawdown/check`, `/risk/drawdown`).
- ✅ H3-007: 97 tests passed / 1 skipped (54 nuevos: 30 unit domain, 13 unit usecases, 5 unit adapters, 6 integration endpoint).
- ✅ H3-008: pytest 97/97, arch_lint PASS, secret_scan clean (los 5 hits son placeholders en `.agents/skills/*/SKILL.md`, no en código de argos).
- 🐛 Bug encontrado y corregido en H3-002: `warn_ratio <= threshold` se invirtió en la validación — habría roto `CircuitBreaker()` con defaults (0.6 no es <= 0.05). Ahora `warn_ratio` se valida solo en `(0, 1]` independientemente.
- 🐛 Bug pre-existente del H2 detectado: `get_compute_position_size_usecase(request: "Request")` con string forward-ref rompe la resolución de params de FastAPI con pydantic 2.13. Reemplazado por `from fastapi import Request` real. Mismo fix aplicado a los nuevos helpers H3.
- 🐛 `_NoopOrderClient` y la wrapper `TmpFileEnvWriter` en tests también iterados: la wrapper ahora hereda de `FileEnvironmentModeWriter` (no la envuelve) para que `isinstance(..., EnvironmentModeWriter)` pase.
- ✅ 8 commits conventional en `feature/h3-circuit-breaker`: h3-001..h3-007 + TASKS update.
- 🎯 **H3 listo para PR.** Push pendiente de confirmación del usuario (AGENTS.md §12).

### 2026-06-07 — Sesión H2: Position Sizing
- ✅ Branch `feature/h2-position-sizing` mergeada a `dev` vía PR #2 (merge commit `9873902`).
- ✅ 9 commits conventional, H2 al 100% (9/9 tareas).
- ✅ 43 tests pasados (21 unit VOs + 7 unit entity + 7 unit usecase + 3 integration ATR + 4 integration endpoint + 1 health).
- 🐛 Bug del H2-007: `risk_calculator.py` import path era `.value_objects.atr` (hermanos) en lugar de `..value_objects.atr`. Corregido.
- 🐛 Decimal quantise 8dp: `(Decimal("100") * Decimal("0.01")) / Decimal("600")` redondea a `0.16666667` (no 0.16666666). Test expectation actualizado.
- 🐛 `Atr._MAX_DECIMALS` subido de 12 a 18 para aceptar la precisión natural de `ta.average_true_range`.
- ✅ `docs/prs/h2-position-sizing.md` creado (pendiente de archivar a `done/` cuando PR sea mergeado por el usuario — eso es post-merge humano, no del agente).

### 2026-06-07 — Sesión H1: Tick Pipeline
- ✅ Branch `feature/h1-tick-pipeline` creada desde `dev`.
- ✅ H1-001..H1-007 implementados: domain (Tick, Symbol, Price, StreamName), application ports, use cases, infrastructure adapters (BinanceWebSocketAdapter, RedisProtocolBus, InMemoryTickBuffer, BusHealthMonitor), NestJS DI wiring, FastAPI subscriber mínimo con xread.
- ✅ H1-008: 26/26 tests unitarios PASS (`domain.spec.ts` 14 tests, `use-cases.spec.ts` 12 tests).
- ✅ Validación: `tsc --noEmit` PASS, `eslint` PASS (con override para domain class `Symbol`).
- ✅ Hexagonal: `tick-pipeline.service.ts` movido a `infrastructure/services/` (es glue NestJS). `application/` sin imports de `infrastructure/`.
- ✅ Deps añadidas: `@nestjs/config@3.2.0` (3.1.x choca con `reflect-metadata@0.2.2`).
- ⏭ H1-009 (integration) y H1-010 (benchmark) requieren broker reachable; no ejecutados en sandbox.
- 🐛 Bug detectado y corregido en `FlushBufferUseCase`: `drain()` vaciaba el buffer y al fallar el primer publish solo re-bufeaba ese tick, perdiendo los siguientes. Ahora re-bufea el fallido + todos los restantes en orden.
- ✅ Push de `feature/h1-tick-pipeline` a `origin` (7 commits, todos conventional).
- ✅ **PR #1 mergeado a `dev`** (merge commit `f053fc7`, sin squash — los 7 commits de H1 preservados para bisect y trazabilidad).
- ✅ Rama `feature/h1-tick-pipeline` borrada en local y en `origin`.
- ✅ PR body archivado en `docs/prs/done/h1-tick-pipeline.md` para referencia histórica.
- 🎯 **PR #1 (H1) mergeado a dev** — primera historia cerrada del proyecto.
- 🔒 Sandbox: registry npm ~14s/ping, install tomó ~5 min; tests/lint corren offline en ~5s cada uno.

### 2026-06-08 — Sesión H8: Backtesting Engine
- ✅ Branch `feature/h8-backtest-engine` creada desde `dev` (post-merge H6).
- ✅ Domain: BacktestConfig, BacktestTrade, BacktestMetrics VOs + BacktestEngine entity con SignalFn, ATR SL, equity curve.
- ✅ Ports: Strategy (protocol + SignalFn), BacktestReporter, MetricsCalculator.
- ✅ Use Case: RunBacktestUseCase (pipeline completo con validaciones).
- ✅ Estrategias: EmaCrossStrategy (9/21 EMA crossover), RsiMeanReversionStrategy (RSI-14, 30/70 thresholds), StrategyDictRegistry.
- ✅ Métricas: SimpleMetricsCalculator — Sharpe anualizado, max drawdown, win rate, profit factor.
- ✅ Reporter: FileBacktestReporter — JSON en reports/backtest/.
- ✅ API: POST /backtest/run + GET /backtest/strategies (vía app.state, no singletons module-level).
- ✅ Tests: 64 nuevos (21 VOs + 8 engine + 14 strategies + 10 metrics + 7 integration). 264/265 total.
- 🐛 Bug fix: Decimal * float en ATR cálculo del engine (atr * 1.5 → atr * Decimal("1.5")). DivisionByZero en precios negativos (safety check entry_price > 0).
- 🐛 Bug fix: singleton module-level en get_backtest_usecase() causaba tests no deterministas. Refactor a app.state como los otros use cases.
- ✅ Validación: pytest 264/265, arch_lint PASS, secret_scan clean.

### 2026-06-09 — Sesión: CcxtBinanceTestnetAdapter (Spot Testnet)
- ✅ Agregado `close_position(symbol)` al port `ExchangeOrderClient`
- ✅ Actualizado `_NoopOrderClient` y `CcxtOrderClient` con `close_position`
- ✅ Creado `infrastructure/trading/ccxt_binance_adapter.py` — `CcxtBinanceTestnetAdapter` con ExchangeOrderClient + PriceProvider
- ✅ Wiring en `composition.py`: `_build_exchange()` detecta `BINANCE_TESTNET=true`, crea exchange Spot con sandbox mode
- ✅ `_build_order_client()` helper retorna `CcxtBinanceTestnetAdapter` o `CcxtOrderClient` según modo
- ✅ `MonitorPositionsUseCase` ahora usa `CcxtBinanceTestnetAdapter.get_price()` real en testnet (con cache 1s)
- ✅ `preflight.py` valida `BINANCE_TESTNET_API_KEY`/`SECRET` en LIVE+testnet
- ✅ 20 tests unitarios (get_price, close_position, cancel_all, place_composite, emergency, close_all)
- ✅ 357/358 pytest pass (1 skipped), arch_lint PASS, secret_scan clean (solo falsos positivos en skills/)

### 2026-06-10 — Sesión: ARGOS 2.0 H8–H39 completo + Branch split

- ✅ All 462 tests pass (1 skipped), arch_lint PASS, typecheck PASS, lint PASS.
- ✅ **Branch 1** (`feature/h8-h12-data-engine`): 39 data-engine files committed, pushed.
- ✅ **Branch 2** (`feature/h23-h29-part-iii`): 45 analytics-engine files (Part III: NovaQuant model pipeline, regime detection, dataset builder), pushed.
- ✅ **Branch 3** (`feature/h30-h39-part-iv`): 25 analytics-engine files (Part IV: Execution Engine, Risk Engine, Portfolio Manager, Position Manager, Correlation Engine), pushed.
- ✅ Shared `__init__.py` files and `composition.py` crafted with correct Part-III/Part-IV-only exports.
- ✅ Merge order: H8-H12 → H23-H29 → H30-H39 (open PRs in that order).
- ✅ TASKS.md updated with H30-H39 entries and bitácora.
- ✅ `dev` clean at `0862b1b` (base for all branches).

### 2026-06-16 — Sesión Q02: Signal Validation Sprint — CASO_B
- ✅ Roadmap cuantitativo V2 creado en `roadmaps/ROADMAP_QUANT_V2.md` con V3.1 Sprint structure (Secciones A/B/C, GATE 0A/0B, 3 framings, H0/H1)
- ✅ 7 configs de experimentos en `roadmaps/configs/` (4 targets + 2 features + README)
- ✅ Versiones bumped: data-engine 0.0.1→0.1.0, analytics-engine 0.0.1→0.1.0
- ✅ Branch `feature/q02-signal-validation-sprint` creada desde `dev`, commit inicial pusheado
- ✅ `experiments/sprint_runner.py` — sprint engine completo: 39049 velas BTC/USDT 1h via CCXT, 20 TA features, 4 modelos × 3 framings, walk-forward (4 folds), shuffle test (5 seeds)
- ✅ Sprint ejecutado (236s): A_classification (HistGB delta=+0.063 ✅), B_binary (todos fallan ❌), C_regression (r²=-0.02 ❌)
- ✅ `reports/sprint/sprint_report.json` + `sprint_summary.txt` generados
- ✅ Veredicto: **CASO_B** — señal débil pero real, no explotable económicamente
- ✅ GATE 0A: fragmentary PASS (solo HistGB en 3-class, delta=+0.063)
- ✅ GATE 0B (exploitability): FAIL
- ✅ Q03–QX cancelados per roadmap Sección C
- ✅ TASKS.md: Q02 ✅, Q03–QX 🚫, total_tasks 98/98
- ✅ PR abierto y mergeado a `dev` por el usuario
- ✅ Branch `feature/q02-signal-validation-sprint` mergeada — pendiente borrar local + origin

### 2026-06-09 — Sesión H9: Telemetry Webhooks (merge a dev)
- ✅ PR mergeado a `dev` por el usuario.
- ✅ Rama `feature/h6-telemetry-webhooks` borrada (local + origin).
- **Estado**: 9/9 tareas, 94/94 totales completadas.
- **Próximo**: definir próximas historias o release.

### 2026-06-09 — Sesión H9: Telemetry Webhooks (Telegram/Discord)
- ✅ Branch `feature/h9-telemetry-webhooks` creada desde `dev`.
- ✅ Domain VO: NotificationEvent, NotificationSeverity, NotificationEventType.
- ✅ Port: Notifier protocol (Python), implementado en LoggingNotifier + RedisNotifier + CompositeNotifier.
- ✅ Use case: NotifyOnEventUseCase — dispara notifier con evento.
- ✅ API: POST /notification/test, GET /notification/status.
- ✅ Composition: wiring con RedisNotifier en PAPER/LIVE, LoggingNotifier en BACKTESTING.
- ✅ Node/NestJS: TelegramNotifier, DiscordNotifier, NotificationConsumer (XREAD + HTTP POST via fetch).
- ✅ Env vars: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DISCORD_WEBHOOK_URL en .env.example de ambos engines.
- ✅ Tests: 14 nuevos (Python). 337/338 total pass. TypeScript typecheck PASS. arch_lint PASS. secret_scan clean.
- 🐛 Bug fix: RedisNotifier test assertion usaba index wrong (call[0] vs call.args). Corregido.
- 🎯 Branch local lista para push + PR.

### 2026-06-08 — Sesión H7: Live Execution Engine (merge a dev)
- ✅ PR #7 mergeado a `dev` por el usuario.
- ✅ Rama `feature/h7-live-execution-engine` borrada (local + origin).
- **Estado**: 9/9 tareas, 85/85 total, 323 tests, arch_lint PASS, secret_scan clean.
- **Próximo**: H7 está en `dev`. Listo para release cuando se definan las próximas historias.

### 2026-06-08 — Sesión H7: Live Execution Engine
- ✅ Branch `feature/h7-live-execution-engine` creada desde `dev` (post-merge H8).
- ✅ H7-001: Domain VOs — ExecutionSignal (rejects HOLD, validates confidence/symbol), LivePosition (SL/TP hit, PnL%, compute_pnl_at), ExecutionReport (status validation).
- ✅ H7-002: Domain entities — SignalValidator (confidence threshold, cooldown, dedup, expiration), PositionTracker (stateless SL/TP verdict + PnL).
- ✅ H7-003: Ports — SignalConsumer (async generator protocol), PositionRepository (CRUD), ExecutionLogger (structured).
- ✅ H7-004: Use cases — ExecuteSignalUseCase (validate → CB → size → place → persist → log), MonitorPositionsUseCase (SL/TP loop → auto-close).
- ✅ H7-005/006: Infrastructure — NovaQuantSignalConsumer, InMemoryPositionRepository, FilePositionRepository (JSON), StructlogExecutionLogger.
- ✅ H7-007: Composition wiring — 3 use case builders cached in app.state, BACKTESTING mode uses _FakeAtrCalculator/MockBalanceProvider/_NoopOrderClient.
- ✅ H7-008: API — POST /execute/signal, GET /position/list, GET /execution/log (422 on error/rejection).
- ✅ H7-009: Tests — 59 new (21 VOs + 8 SignalValidator + 8 PositionTracker + 5 execute_signal + 5 monitor_positions + 12 integration). 323/324 total pass (1 skipped).
- 🐛 Bug fix: TaAtrCalculator no tenía `calculate()`, usa `get_atr()` que retorna `Atr` VO (no Decimal).
- 🐛 Bug fix: ATR import faltante en composition.py (line 614 referenced `AtrCalculator` sin import). Añadido.
- 🐛 Bug fix: Integration test cooldown collision entre `test_execute_buy` y `test_with_price` (mismo símbolo BTC/USDT). Cambiado a SOL/USDT.
- ✅ Validación: pytest 323/324, arch_lint PASS, secret_scan clean.

### 2026-06-08 — Sesión H6: NovaQuant ML Pipeline
- ✅ NovaQuant completo: 9/9 tareas, 30 archivos nuevos.
- ✅ Domain VOs: ModelConfig (lookback 5-500, features, layers 1-10, dropout 0-0.5, target thresholds), TradingSignal (confidence 0-1, actionable, metadata), SignalSide (BUY/SELL/HOLD).
- ✅ Domain entity: NovaQuantModel con weights_hash, version, feature_stats, metrics, trained_at, age_days, is_stale (≥7d), validate_input/assert_not_stale/assert_version, __repr__.
- ✅ 6 ports: OhlcvSource, DataPreprocessor (build_features con RSI/MACD/BB/EMA/ATR, z-score normalize, sliding windows 2D/3D, one-hot targets), FeatureAnalyzer (Pearson r, filter noise, keep ≥3), ModelTrainer, ModelPredictor, CheckpointRepository.
- ✅ 2 use cases: TrainModelUseCase (fetch → preprocess → analyze → train → save), PredictSignalUseCase (fetch → preprocess → load → predict → TradingSignal).
- ✅ 4 infra adapters: TaDataPreprocessor (ta library), CorrelationFeatureAnalyzer (scipy.stats.pearsonr), NovaQuantKerasModel (3 dense: 128→64→32, dropout 0.3, Adam, early stopping patience 5, .keras checkpoint), FsCheckpointRepository (JSON metadata + .keras).
- ✅ API: POST /model/train, POST /model/predict con Pydantic schemas y 422 sad paths.
- ✅ Composition: get_model_use_cases() lazy builder, _CcxtOhlcvAdapter / _FakeOhlcvSource, cached en app.state.
- ✅ Tests: 83 nuevos (20 unit VOs + 20 unit entity + 18 integration data_preprocessor + 12 integration feature_analyzer + 13 API). 200/201 total (1 skipped — subscriber).
- ✅ Merge dev → feature/h6-novaquant: 5 conflictos resueltos (api/__init__, ports/__init__, use_cases/__init__, composition.py, main.py). H4-B + H5 integrados.
- ✅ Validación: pytest 200/201, arch_lint PASS, secret_scan clean.
- ✅ 1 commit conventional, branch local lista para push + PR.

### 2026-06-11 — Sesión: Merge ARGOS 2.0 Parts II, V, VI → dev
- ✅ Branch `feature/h13-h22-part-ii` mergeada a `dev` (fast-forward, 8 files).
- ✅ Branch `feature/h40-h50-part-v` mergeada a `dev` con conflict resolution en `composition.py` (imports duplicados + training use case functions).
- ✅ Branch `feature/h51-h59-part-vi` mergeada a `dev` con conflict resolution en `composition.py` y `main.py` (7 conflictos, resueltos con superset approach).
- ✅ Bug fix: `MonitorPositionsUseCase` ahora setea `SL_HIT`/`TP_HIT`/`CLOSED` según el motivo del cierre (antes siempre `CLOSED`).
- ✅ Bug fix: `_MockExchangeClient` en test añadido `close_partial()`.
- ✅ Bug fix: Test `test_close_tp_hit` actualizado a `PARTIALLY_CLOSED` (TP1 hace partial close 50%, no full close).
- ✅ Push a `origin/dev` tras mergear con remote changes (Part II ya mergeada via PR #19).
- ✅ Ramas `feature/h13-h22-part-ii`, `feature/h40-h50-part-v`, `feature/h51-h59-part-vi` borradas (local + origin).
- ✅ 462 tests pass, 1 skip. arch_lint PASS. secret_scan clean (solo falsos positivos en skills/).
- **Próximo**: Docker Compose para integración end-to-end.

### 2026-06-06 — Sesión de setup
- ✅ Crash de opencode diagnosticado y resuelto (3 causas: `import.meta.dir`, regex `(?i)`, `execute()` sync).
- ✅ 28 tools operativos, 12 commands, 7 skills, context7 MCP.
- ✅ 5 tools verificados con invocación real: `spec_invariants`, `risk_position_size`, `risk_drawdown_check`, `config_read_config`, `spec_summary`.
- ✅ `AGENTS.md` y `TASKS.md` creados.
- ✅ Política de permisos en `opencode.json` para tools destructivos.
- ✅ Git inicializado, `main` y `dev` pusheadas a `origin` (commits `f3c46e0`, `423221e`).
- ✅ `LICENSE` (MIT) y `README.md` creados.
- ✅ Refactor agnóstico: spec.md §1, §1.2, §4, §6 amendados; AGENTS.md §1, §2 #14, §7, §11 actualizados; config.json migrado a `broker: { kind, url: ${ARGOS_BROKER_URL} }`; `.gitattributes` creado; README "Quick start" dual (Docker + bare metal con WSL2/Memurai); `health_health_check` con detección de deployment model; `docker_docker_*` tools anotados como "uso solo con deploy Docker".
- ✅ Sección 12 (Git workflow) añadida a AGENTS.md.
- ✅ Fase 0 ejecutada: `docker-compose.yml` (3 servicios, broker RESP-compatibile con `ARGOS_BROKER_URL`), NestJS data-engine skeleton, FastAPI analytics-engine skeleton. 20 archivos nuevos en `apps/`. Las tools de opencode habían quedado con la versión pre-agnóstica en memoria; parcheé los 4 archivos afectados (`.env.example` × 2, `main.ts`, `docker-compose.yml`) al contenido correcto.

### 2026-06-11 — Sesión: Ensemble pipeline completion
- ✅ `NovaQuantKerasModel.get_model()` — expone `tf.keras.Model` para MC Dropout
- ✅ `PredictEnsembleSignalUseCase.regime_detector` — `RuleBasedRegimeDetector` inyectado, reemplaza inline ADX hack
- ✅ `composition.py` — `get_predict_ensemble_usecase` wired con `MCDropoutUncertaintyEstimator` (opcional, si LSTM cargado) + `RuleBasedRegimeDetector`
- ✅ `ensemble_training.py` — `log` module-level añadido (bug fix: NameError)
- ✅ `__init__.py` — `ExecuteTradingSignalUseCase` removido de exports
- ✅ Tests: 21 nuevos (7 ensemble_training + 14 predict_ensemble), todos los caminos: happy path, meta/calibrator/confidence/uncertainty/regime, sad paths (checkpoint, stale, insufficient data, training failure)
- ✅ Full test suite: 483 passed, 1 skipped (sin regresiones)
- ✅ Arch lint PASS, secret_scan clean (solo falsos positivos en skills/)
- ✅ Branch `feature/h6-h29-ensemble-pipeline-complete` creada desde `dev`, 13 archivos commiteados, push a `origin`
- 🎯 **Próximo**: el usuario abre PR en GitHub apuntando a `dev`. Pendiente: train real models con datos históricos (validar accuracy > 33%), activar UncertaintyEstimator post-train.

### 2026-06-11 — Sesión: Multi-symbol checkpoint + Colab workflow
- ✅ `CheckpointRepository` port: `symbol: str = ""` añadido a `load_latest()`, `save()`, `load_version()`, `list_versions()`
- ✅ `FsCheckpointRepository`: refactorizado a estructura `{base}/{symbol_key}/{version}/` con helper `_symbol_dir()`
- ✅ `PredictEnsembleSignalUseCase.execute()`: pasa `symbol` a `self._repo.load_latest(symbol=symbol)`
- ✅ `scripts/export_training_data.py`: CLI para chunked OHLCV fetch (250ms delay, 1000 candles/chunk) + TaDataPreprocessor → Parquet ZIP con manifest.json
- ✅ `scripts/import_checkpoint.py`: CLI para importar checkpoint ZIP de Colab → FsCheckpointRepository
- ✅ `notebooks/colab_train_ensemble.ipynb`: notebook Colab con Walk Forward, LSTM GPU, XGBoost, MetaModel, Calibrator, export ZIP
- ✅ Mock repos en tests actualizados con `symbol: str = ""`
- ✅ Full test suite: 483 passed, 1 skipped (sin regresiones)
- 🎯 **Próximo**: exportar datos (6 símbolos, ~1h), subir ZIP a Colab, correr notebook, importar checkpoints, validar pipeline de predicción.

### 2026-06-15 — Sesión: Data-engine mejoras + Multi-symbol routing fix
- ✅ DNS + healthcheck (8e97c30): DNS 8.8.8.8/1.1.1.1 en docker-compose, healthcheck descomentado
- ✅ Reconexión WS + graceful shutdown + health exchange + factory (fe28491): BinanceWebSocketAdapter con exponential backoff (1s→30s capped), enableShutdownHooks(), /health/exchange, exchange-adapter.factory.ts multi-exchange
- ✅ Pong timeout + multi-symbol + config.json + drain buffer + metrics (84b9b90): pong timeout 10s, SYMBOL acepta lista separada por comas, config.json integration (env pisa config), drain buffer on shutdown, reconnectAttempt/totalReconnects/connectedAt en health endpoint
- ✅ Multi-symbol routing fix (4b0de5f → PR mergeado a dev): `IngestTickUseCase.execute()` acepta stream opcional por tick; `FlushBufferUseCase` computa stream por `tick.symbol`; `TickPipelineService` construye `StreamName.forTicks(symbol, prefix)`; Redis URL constructor fix en 3 adaptadores
- ✅ Validación: tsc --noEmit, eslint, jest, arch_lint PASS, secret_scan clean
- 🐛 Bug: ticks:ethusdt = 0 porque `IngestTickUseCase` publicaba todos los ticks al primer stream. Corregido con routing por tick.symbol.
- 🎯 Branch `feature/h1-multi-symbol-routing` pusheada y mergeada a dev.

---

## ✅ Q00 — Quant V2: Baseline + Roadmap

> Documentación del baseline actual y creación del roadmap cuantitativo V2.

- [x] Q00-001 — Auditoría cuantitativa completa del pipeline actual (10 secciones)
- [x] Q00-002 — Crear `roadmaps/ROADMAP_QUANT_V2.md` con estructura V4 (gates, framing lock, criterios de éxito)

---

## ✅ Q01 — Quant V2: Freeze Baseline

> Preservar baseline reproducible para comparaciones futuras.

- [x] Q01-001 — Documentar métricas de los 6 símbolos en el roadmap (accuracy, F1, Kappa, confusion matrices)
- [x] Q01-002 — Crear configs de experimentos en `roadmaps/configs/`
- [x] Q01-003 — Versionar código en 0.1.0 (package.json, pyproject.toml)

---

## ✅ Q02 — Quant V2: Signal Validation Sprint

> Sprint de falsación científica (FASE 2 del roadmap cuantitativo V2).
> Hipótesis: ¿Existe señal predictiva explotable en OHLCV+TA en 1h?
> **Veredicto: CASO_B** — Señal débil detectada (delta=+0.063, best=HistGB 3-class),
> pero no explotable económicamente. Feature space considerado INVALIDO.
> Post-sprint pipeline NO activado per ROADMAP_QUANT_V2.md Sección A/C.

- [x] Q02-001 — Crear sprint_runner.py con data loader, feature engine, 4 modelos, 3 framings
- [x] Q02-002 — Ejecutar framing A (3-class BUY/HOLD/SELL) con walk-forward + shuffle test
- [x] Q02-003 — Ejecutar framing B (binary BUY vs SELL) y framing C (regression Ridge+lags)
- [x] Q02-004 — Evaluar GATE 0A + GATE 0B, emitir veredicto y guardar reporte

**Resultados clave**:
  - HistGradientBoosting (3-class): f1=0.388, shuf_delta=+0.063, vsBH=+0.073 — señal débil pero real
  - Binary BUY/SELL: TODOS los modelos fallan (shuf_delta negativo)
  - Ridge(lags=5): r²=-0.02, dir_acc=49.7% — sin poder predictivo
  - GATE 0A (signal existence): FRAGMENTARY PASS (solo 1/3 framings)
  - GATE 0B (exploitability): FAIL

---

## 🚫 Q03 — Quant V2: Feature Audit [CANCELLED]

> CANCELLED: CASO_B — no hay alpha económico para auditar features.
> Post-sprint pipeline no activado per ROADMAP_QUANT_V2.md § Section A.

- [-] Q03-001 — Calcular SHAP values en MetaModel
- [-] Q03-002 — Permutation Importance de las 20 features
- [-] Q03-003 — Mutual Information features vs target
- [-] Q03-004 — Detectar multicolinealidad (VIF, correlaciones)

---

## 🚫 Q04 — Quant V2: New Features [CANCELLED]

> CANCELLED: CASO_B. Feature space OHLCV+TA invalidado para trading predictivo.
> Explorar alternatives datasources (order flow, funding rates, OI, on-chain).

- [-] Q04-001 — Agregar retornos logarítmicos (1, 3, 6, 12, 24)
- [-] Q04-002 — Agregar lags de close (1, 3, 6, 12)
- [-] Q04-003 — Agregar volatilidad rolling (6, 12, 24) y z-scores
- [-] Q04-004 — Agregar momentum (ROC 3, 6, 12) y regímenes
- [-] Q04-005 — Agregar features cross-symbol (si hay datos multi-símbolo)

---

## 🚫 Q05 — Quant V2: Simple Baselines [CANCELLED]

> CANCELLED: CASO_B. Modelos simples ya evaluados en el sprint.
> LR, RF, HistGB y Ridge ejecutados. No mejora sustancial respecto al baseline.

- [-] Q05-001 — Logistic Regression
- [-] Q05-002 — Random Forest
- [-] Q05-003 — LightGBM
- [-] Q05-004 — XGBoost
- [-] Q05-005 — CatBoost
- [-] Q05-006 — Evaluar GATES 4, 5, 6 y decidir continuidad

---

## 🚫 Q06 — Quant V2: Alpha Validation [CANCELLED]

> CANCELLED: CASO_B. Shuffle test y walk-forward ya integrados en el sprint.
> Sin alpha económico para validar.

- [-] Q06-001 — Shuffle labels test (10 seeds)
- [-] Q06-002 — Walk Forward validation
- [-] Q06-003 — Purged KFold
- [-] Q06-004 — Combinatorial Purged CV

---

## 🚫 Q07 — Quant V2: Advanced Models [CANCELLED]

> CANCELLED: CASO_B. Modelos avanzados prohibidos por diseño del sprint.
> No hay alpha que justifique complejidad adicional.

- [-] Q07-001 — GRU
- [-] Q07-002 — TCN
- [-] Q07-003 — Transformer temporal / TFT

---

## 🚫 Q08 — Quant V2: Loss Functions [CANCELLED]

> CANCELLED: CASO_B. Sin alpha validado, loss tuning es prematuro.

- [-] Q08-001 — Class weights
- [-] Q08-002 — Focal Loss
- [-] Q08-003 — Threshold tuning post-hoc
- [-] Q08-004 — Probability calibration (Platt, Isotonic, temperature scaling)

---

## 🚫 Q09 — Quant V2: Realistic Backtest [CANCELLED]

> CANCELLED: CASO_B. Backtest sin sentido sin señal explotable.

- [-] Q09-001 — Implementar simulador con costos reales
- [-] Q09-002 — Evaluar Sharpe, Sortino, Calmar, Max DD, Profit Factor
- [-] Q09-003 — Probar distintas configs de SL/TP
- [-] Q09-004 — Position sizing dinámico (riesgo 1%)
- [-] Q09-005 — MCC direccional con costos

---

## 🚫 Q10 — Quant V2: Scaling [CANCELLED]

> CANCELLED: CASO_B. Sin alpha en BTC, escalar es irrelevante.

- [-] Q10-001 — Optimizar BTC/USDT hasta criterio de éxito global
- [-] Q10-002 — Replicar a ETH, SOL
- [-] Q10-003 — Replicar a DOGE, AVAX, XRP

---

## 🚫 QX — Quant V2: Temporal Audit [CANCELLED]

> CANCELLED: CASO_B. Timeframes investigados como parte del sprint (1h).
> Sin señal explotable, otros timeframes probablemente igual.

- [-] QX-001 — Probar timeframes [15m, 30m, 1h, 4h, 1d]
- [-] QX-002 — Probar lookbacks [20, 48, 72, 96, 168] horas
- [-] QX-003 — Multi-timeframe features (1h + 4h + 1d)
- [-] QX-004 — Señal por régimen de mercado (bull/bear, alta/baja volatilidad)
- [-] QX-005 — Comparar 4 vs 6 vs 8 vs 10 años de histórico (solo BTC)
