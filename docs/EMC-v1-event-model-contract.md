# EMC v1 — Event Model Contract

> Contrato de soberanía del sistema: quién puede cambiar el estado,
> bajo qué reglas, y qué es solo observable.
>
> Esto NO es un schema de logs. Es el modelo de autoridad del sistema.

---

## 0. Estado actual (baseline)

### 0.1 Problemas diagnosticados

| Síntoma | Causa raíz | Tipo |
|---|---|---|
| CSV duplication | Sin idempotency key en writes | Pipeline |
| HALT ignorado por runtime | Simulation y runtime comparten autoridad de estado | Governance |
| SQLite inexistente | State model mal definido: nadie sabe qué es "verdad" | Arquitectura |
| forward_test_btc.csv vacío | Log pipeline no tiene dueño | Pipeline |
| 3 fuentes de mode | Sin autoridad de configuración | Governance |

### 0.2 Principio rector del EMC

> El sistema no tendrá un solo bug de consistencia si:
>
> 1. Una sola entidad puede escribir cada tipo de evento
> 2. Cualquier escritura es idempotente por construcción
> 3. La simulación solo propone — nunca ordena
> 4. El estado actual es una proyección derivada, no una fuente primaria

---

## 1. State Authority Model

### 1.1 Mapa de soberanía

| Componente | Autoridad | Puede emitir | NO puede emitir |
|---|---|---|---|
| **ExecutionCore** | Escribe estado de ejecución | `OrderCreated`, `OrderFilled`, `OrderCancelled`, `PositionOpened`, `PositionUpdated`, `PositionClosed` | Riesgo, simulación, señales |
| **SignalEngine** | Propone señales | `SignalGenerated`, `SignalRejected` | Órdenes, posiciones, riesgo |
| **RiskPolicyEngine** | Decide si se puede operar | `RiskEvaluated`, `RiskLimitBreached`, `RiskStateChanged` | Órdenes, posiciones, señales |
| **SimulationEngine** | Solo informa | `SimulationEvaluated`, `MonteCarloCompleted`, `RiskProjection` | Cualquier evento de ejecución |
| **ProjectionLayer** | Deriva estado | Ninguno (solo lee) | N/A |
| **ExchangeAdapter** | Truth externa | `FillConfirmed`, `BalanceChangeDetected` | Decisiones internas |
| **ConfigStore** | Autoridad de configuración | `ConfigChanged` | Eventos de trading |

### 1.2 Reglas de autoridad (invariantes)

```
Regla 1: Un evento tiene exactamente un source type.
Regla 2: ExecutionCore NO lee de SimulationEngine para decidir.
         Sí lee de RiskPolicyEngine.
Regla 3: SimulationEngine NO escribe nada que ExecutionCore lea como orden.
Regla 4: RiskPolicyEngine es la única entidad que puede emitir RiskStateChanged.
Regla 5: RiskPolicyEngine es la única entidad que puede revivir el sistema
         (transición HALT → RECOVERY_PENDING → ACTIVE).
Regla 6: Ninguna proyección puede escribir eventos.
Regla 7: El ConfigStore es la única fuente de environment_mode.
Regla 8: SimulationEngine NO puede reactivar el sistema.
Regla 9: ExecutionCore NO puede reactivar el sistema.
Regla 10: Replay NO puede cambiar estado de riesgo (solo reconstruye).
```

---

## 2. Event Catalog

### 2.1 Market / Signal Domain

```json
{
  "event_type": "SignalGenerated",
  "source": "SignalEngine",
  "version": 1,
  "event_id": "sig_<deterministic_hash>",
  "timestamp": "<ISO8601>",
  "data": {
    "symbol": "BTC/USDT",
    "signal_id": "<uuid>",
    "side": "BUY | SELL | HOLD",
    "confidence": 0.9447,
    "model_version": "1.0.0",
    "regime": "TRENDING",
    "features_hash": "<sha256_of_input_features>"
  }
}
```

```json
{
  "event_type": "SignalRejected",
  "source": "ExecutionGuard",
  "version": 1,
  "event_id": "rej_<deterministic_hash>",
  "timestamp": "<ISO8601>",
  "data": {
    "signal_id": "<uuid>",
    "reason": "low_confidence | soft_circuit_breaker_active | volatility_spike",
    "confidence": 0.61,
    "threshold": 0.75
  }
}
```

### 2.2 Execution Domain

```json
{
  "event_type": "OrderCreated",
  "source": "ExecutionCore",
  "version": 1,
  "event_id": "oc_<deterministic_hash>",
  "timestamp": "<ISO8601>",
  "data": {
    "order_id": "<exchange_order_id>",
    "symbol": "BTC/USDT",
    "side": "BUY | SELL",
    "type": "LIMIT | MARKET | STOP",
    "quantity": 0.01,
    "price": 62470.7,
    "sl_price": 61846.0,
    "tp_price": null,
    "signal_id": "<uuid>"
  }
}
```

```json
{
  "event_type": "OrderFilled",
  "source": "ExecutionCore",
  "version": 1,
  "event_id": "of_<deterministic_hash>",
  "timestamp": "<ISO8601>",
  "data": {
    "order_id": "<exchange_order_id>",
    "fill_price": 62470.7,
    "fill_qty": 0.01,
    "fee": 0.0001,
    "fee_currency": "USDT",
    "commission_asset": "BNB"
  }
}
```

```json
{
  "event_type": "OrderCancelled",
  "source": "ExecutionCore",
  "version": 1,
  "event_id": "ox_<deterministic_hash>",
  "timestamp": "<ISO8601>",
  "data": {
    "order_id": "<exchange_order_id>",
    "reason": "manual | circuit_breaker | sl_updated | position_closed",
    "remaining_qty": 0.0
  }
}
```

### 2.3 Position Domain

```json
{
  "event_type": "PositionOpened",
  "source": "ExecutionCore",
  "version": 1,
  "event_id": "po_<deterministic_hash>",
  "timestamp": "<ISO8601>",
  "data": {
    "position_id": "<uuid>",
    "symbol": "BTC/USDT",
    "side": "LONG | SHORT",
    "entry_price": 62470.7,
    "quantity": 0.01,
    "sl_price": 61846.0,
    "tp_price": null,
    "order_id": "<exchange_order_id>"
  }
}
```

```json
{
  "event_type": "PositionUpdated",
  "source": "ExecutionCore",
  "version": 1,
  "event_id": "pu_<deterministic_hash>",
  "timestamp": "<ISO8601>",
  "data": {
    "position_id": "<uuid>",
    "sl_price": 61900.0,
    "tp_price": null,
    "reason": "trailing_sl | manual_adjust"
  }
}
```

```json
{
  "event_type": "PositionClosed",
  "source": "ExecutionCore",
  "version": 1,
  "event_id": "pc_<deterministic_hash>",
  "timestamp": "<ISO8601>",
  "data": {
    "position_id": "<uuid>",
    "close_price": 61846.0,
    "pnl": -62.47,
    "pnl_pct": -1.0,
    "close_reason": "stop_loss | take_profit | circuit_breaker | manual",
    "hold_bars": 3,
    "order_id": "<exchange_order_id>"
  }
}
```

### 2.4 Risk Domain

```json
{
  "event_type": "RiskEvaluated",
  "source": "RiskPolicyEngine",
  "version": 1,
  "event_id": "rk_<deterministic_hash>",
  "timestamp": "<ISO8601>",
  "data": {
    "drawdown_pct": 0.023,
    "daily_pnl": -124.50,
    "starting_balance": 86578.04,
    "current_balance": 86453.54,
    "circuit_breaker": "NORMAL | WARN | TRIP",
    "evaluated_at": "<ISO8601>"
  }
}
```

```json
{
  "event_type": "RiskLimitBreached",
  "source": "RiskPolicyEngine",
  "version": 1,
  "event_id": "rb_<deterministic_hash>",
  "timestamp": "<ISO8601>",
  "data": {
    "limit_type": "DRAWDOWN | POSITION_SIZE | CONSECUTIVE_LOSSES",
    "threshold": 0.05,
    "current_value": 0.062,
    "action_taken": "WARN | REDUCE | HALT",
    "details": "daily_drawdown_exceeded"
  }
}
```

```json
{
  "event_type": "RiskStateChanged",
  "source": "RiskPolicyEngine",
  "version": 1,
  "event_id": "rs_<deterministic_hash>",
  "timestamp": "<ISO8601>",
  "data": {
    "old_status": "ACTIVE | DEGRADED | HALT | RECOVERY_PENDING",
    "new_status": "ACTIVE | DEGRADED | HALT | RECOVERY_PENDING",
    "reason": "drawdown_breach | monte_carlo_failure | manual_halt | consecutive_failures | recovery_time_elapsed | operator_resume",
    "trigger": "RiskEvaluated | SimulationEvaluated | ManualOverride | RecoveryTimer",
    "previous_mode": "LIVE_SIMULATION",
    "new_mode": "LIVE_SIMULATION | PASIVO",
    "changed_by": "RiskPolicyEngine"
  }
}
```

### 2.5 Simulation Domain (read-only influence)

```json
{
  "event_type": "SimulationEvaluated",
  "source": "SimulationEngine",
  "version": 1,
  "event_id": "se_<deterministic_hash>",
  "timestamp": "<ISO8601>",
  "data": {
    "symbol": "BTC/USDT",
    "bar_idx": 999,
    "p_failure": 1.0,
    "n_failed": 5000,
    "n_total": 5000,
    "expected_drawdown": 0.15,
    "hazard_immediate": 1.0,
    "regime": "HIGH_RISK",
    "projection_horizon_bars": 100
  }
}
```

```json
{
  "event_type": "MonteCarloCompleted",
  "source": "SimulationEngine",
  "version": 1,
  "event_id": "mc_<deterministic_hash>",
  "timestamp": "<ISO8601>",
  "data": {
    "n_scenarios": 5000,
    "p_failure": 1.0,
    "expected_return_pct": -4.2,
    "max_drawdown_p90": 0.22,
    "sharpe_ratio": -0.87,
    "completed_at": "<ISO8601>"
  }
}
```

### 2.6 Configuration Domain

```json
{
  "event_type": "ConfigChanged",
  "source": "ConfigStore",
  "version": 1,
  "event_id": "cc_<deterministic_hash>",
  "timestamp": "<ISO8601>",
  "data": {
    "changed_keys": ["environment_mode", "risk_pct"],
    "previous_values": {
      "environment_mode": "PAPER_TRADING",
      "risk_pct": 0.01
    },
    "new_values": {
      "environment_mode": "LIVE_SIMULATION",
      "risk_pct": 0.02
    },
    "changed_by": "operator | deploy | circuit_breaker"
  }
}
```

---

## 3. Idempotency Contract

### 3.1 Deterministic event_id

```
event_id = hash(
    event_type          # "OrderCreated" | "PositionClosed" | ...
  + source              # "ExecutionCore" | "RiskPolicyEngine"
  + symbol              # "BTC/USDT"
  + timestamp_bucket    # event_time // 1000  (1s granularity)
  + deterministic_inputs_hash  # sha256 de los inputs que generaron el evento
)
```

### 3.2 Write rule

```
function write_event(event):
    if exists(event.event_id):
        skip    # idempotent — no error, no duplicate
    else:
        append(event)
```

### 3.3 Anti-patterns

| Anti-patrón | Problema |
|---|---|
| `event_id = uuid4()` | Cada intento genera ID diferente → duplicados |
| `event_id = timestamp` | Dos eventos en mismo ms colisionan |
| `event_id = f(entire_payload)` | Un cambio mínimo en metadata cambia el ID |
| Uniqueness check en DB en vez de en writer | Race condition en writes paralelos |
| Writer que acepta `force=True` | Rompe la garantía de idempotencia |

---

## 4. Replay Semantics

### 4.1 Pure projection rule

```
state_t = f(state_{t-1}, event_t)
```

Donde:

- `state_0` = estado inicial vacío (o snapshot conocido)
- `f` es una función pura:
  - Sin I/O externo
  - Sin random
  - Sin llamadas a API
  - Sin reloj del sistema (usa `event.timestamp`)
  - Sin lecturas a Redis, SQLite, o disco

### 4.2 Projectors

| Proyector | Events que consume | Estado que produce |
|---|---|---|
| `PositionProjector` | `OrderCreated`, `OrderFilled`, `OrderCancelled`, `PositionOpened`, `PositionUpdated`, `PositionClosed` | Posiciones abiertas + historial |
| `PnLProjector` | `PositionClosed`, `OrderFilled` | P&L acumulado + por trade |
| `RiskProjector` | `RiskEvaluated`, `RiskLimitBreached`, `RiskStateChanged` | Estado del risk state machine |
| `BalanceProjector` | `PositionClosed`, `OrderFilled` | Balance disponible + margin used |
| `SignalProjector` | `SignalGenerated`, `SignalRejected` | Historial de señales |

### 4.3 Snapshot strategy

```
cada N eventos (N = 10000 por defecto):
    snapshot = state_actual
    persistir snapshot
    al hacer replay:
        cargar último snapshot
        replay desde snapshot.event_index + 1
```

### 4.4 Guarantees

```
- Deterministic replay: mismo event log → mismo estado final
- Replay no necesita Redis, API keys, ni exchange
- Replay es O(n) en tamaño del event log
- Snapshot reduce cold start a O(n - snapshot_index)
```

---

## 5. RiskPolicyEngine — Simulation↔Runtime Bridge

### 5.1 Arquitectura

```
SimulationEngine ──→ SimulationEvaluated ──→ RiskPolicyEngine ──→ RiskEvaluated ──→ ExecutionCore
                         MonteCarloCompleted      │              RiskStateChanged
                                                  │
                                                  ▼
                                          ConfigStore (mode rewrite)
```

### 5.2 Reglas del puente

```
Regla A: SimulationEngine emite SimulationEvaluated. Punto. No escribe nada más.
Regla B: RiskPolicyEngine escucha SimulationEvaluated y MonteCarloCompleted.
Regla C: RiskPolicyEngine evalúa ventana rodante (N=3) y emite RiskEvaluated o RiskStateChanged.
Regla D: ExecutionCore solo escucha RiskEvaluated y RiskStateChanged. NUNCA lee SimulationEvaluated directamente.
```

### 5.3 Rolling evaluation window

> CRÍTICO: Ninguna decisión de riesgo se toma sobre un snapshot individual.
> Todas usan una ventana rodante para evitar falsos positivos.

```
RiskDecision = function rolling_window(evaluaciones, N)

  donde N = 3 por defecto

  Regla:
    - Se mantienen las últimas N evaluaciones de SimulationEvaluated
    - Si la mediana de p_failure en la ventana > threshold → accion
    - Si hazard_immediate > 0.8 en CUALQUIER evaluacion → HALT inmediato
      (exception: hazard es señal de peligro inminente, no espera ventana)
```

#### Thresholds

| Señal | Ventana | Threshold | Acción del RiskPolicyEngine |
|---|---|---|---|
| `median(p_failure, N=3)` | 3 evaluaciones | > 0.5 | Emitir `RiskLimitBreached(action=REDUCE)` |
| `median(p_failure, N=3)` | 3 evaluaciones | > 0.8 | Emitir `RiskStateChanged(new_status=DEGRADED)` + REDUCE |
| `max(hazard_immediate, N=3)` | 3 evaluaciones | > 0.8 | Emitir `RiskStateChanged(new_status=HALT)` inmediato |
| Drawdown real | 1 barra (no ventana) | > 5% | Emitir `RiskStateChanged(new_status=HALT)` (independiente de simulación) |
| Consecutive losses | 1 racha | > 3 | REDUCE position size en 50% |

### 5.4 System State Lifecycle

```
                     ┌─────────────────────────────────────┐
                     │                                     │
                     ▼                                     │
              ┌──────────┐     breach/recovery_elapsed     │
         ┌───▶│  ACTIVE  │────────────────────────────┐    │
         │    └──────────┘                            │    │
         │         │                                  │    │
         │    p_failure > 0.5                    p_failure │
         │    (rolling window)                   > 0.8     │
         │         │                            (rolling)  │
         │         ▼                                  │    │
         │    ┌──────────┐                    drawdown>5%  │
         │    │ DEGRADED │◀──────────────────────────┘    │
         │    └──────────┘                                │
         │         │                                      │
         │    p_failure > 0.8 (confirmed window)          │
         │    OR drawdown > 5%                            │
         │    OR hazard_immediate > 0.8                   │
         │         │                                      │
         │         ▼                                      │
         │    ┌──────────┐                                │
         │    │   HALT   │──── recovery_timer_elapsed ────┘
         │    └──────────┘     OR operator_resume
         │         │
         │    recovery iniciado manualmente
         │    (único camino: operator)
         │         │
         │         ▼
         │    ┌──────────────────┐
         └────│ RECOVERY_PENDING │
              └──────────────────┘
                │
      ┌─────────┴──────────┐
      │                    │
      ▼                    ▼
  replay valido      replay invalido
  (projection OK)    (datos corruptos)
      │                    │
      ▼                    ▼
   ACTIVE              HALT (permanente,
                         requiere fix manual)
```

#### Reglas del lifecycle

| Transición | Autorizado por | Condición |
|---|---|---|
| ACTIVE → DEGRADED | RiskPolicyEngine | `median(p_failure, N=3) > 0.5` |
| ACTIVE → HALT | RiskPolicyEngine | hazard_immediate > 0.8 o drawdown > 5% |
| DEGRADED → HALT | RiskPolicyEngine | `median(p_failure, N=3) > 0.8` o drawdown > 5% |
| HALT → RECOVERY_PENDING | RiskPolicyEngine | recovery_timer_elapsed (30 min por defecto) O operator_resume manual |
| RECOVERY_PENDING → ACTIVE | RiskPolicyEngine | replay válido: proyección desde event log produce estado consistente |
| RECOVERY_PENDING → HALT | RiskPolicyEngine | replay inválido: datos corruptos, requiere fix manual |

#### Autoridad de revival

```
Rule A: RiskPolicyEngine es la ÚNICA entidad que puede sacar al sistema de HALT.
Rule B: El recovery timer (30 min) es un evento interno del RiskPolicyEngine,
        no del sistema operativo ni del operador.
Rule C: operator_resume requiere intervención humana explícita.
         NO existe auto-resume sin validación.
Rule D: SimulationEngine NO puede reactivar el sistema.
Rule E: ExecutionCore NO puede reactivar el sistema.
Rule F: Replay no es una reactivación. Replay solo reconstruye estado histórico.
         La reactivación ocurre DESPUÉS del replay, cuando RiskPolicyEngine
         evalúa el estado reconstruido y decide ACTIVE.
```

### 5.5 Caso actual del sistema

Estado hoy:

```
SimulationEngine emitió:    p_failure=1.0, hazard_immediate=1.0, HALT
RiskPolicyEngine debería:   emitir RiskStateChanged(new_status=HALT)
Estado real:                Runtime ignora completamente → CRITICAL
```

Con el bridge:

```
SimulationEngine → SimulationEvaluated(p_failure=1.0)
                 → SimulationEvaluated(p_failure=1.0)   # ventana N=3
                 → SimulationEvaluated(p_failure=1.0)   # mediana = 1.0 > 0.8
RiskPolicyEngine → RiskLimitBreached(action=DEGRADE)
                 → RiskStateChanged(old=ACTIVE, new=DEGRADED)
                 → RiskStateChanged(old=DEGRADED, new=HALT)
ExecutionCore   → detiene ejecución
ConfigStore     → mode = PASIVO

# 30 minutos después (recovery timer)
RiskPolicyEngine → RiskStateChanged(old=HALT, new=RECOVERY_PENDING)
                 → replay validation
                 → RiskStateChanged(old=RECOVERY_PENDING, new=ACTIVE)
ConfigStore     → mode = LIVE_SIMULATION
```

---

## 6. Migration Plan (sin romper forward test)

### 6.1 Fases

| Fase | Qué se hace | Riesgo |
|---|---|---|
| **F1** | Implementar `EventWriter` con idempotency key (event_id determinístico). Convive con CSV writers actuales. | Bajo — write dual |
| **F2** | Implementar `PositionProjector`, `PnLProjector`, `RiskProjector`. Leen del event log. | Medio — nuevo código, no reemplaza nada aún |
| **F3** | Implementar `RiskPolicyEngine`. Conectar `SimulationEvaluated` → `RiskPolicyEngine`. | Alto — cambia governance |
| **F4** | Desconectar CSV writers. Event log es única fuente. | Alto — requiere validación |
| **F5** | Remover SQLite como fuente primaria. SQLite solo como cache de proyección. | Medio — refactor |
| **F6** | Eliminar código CSV legacy. | Bajo — limpieza |

### 6.2 Convivencia con forward test actual

Durante F1-F3:

- Forward test sigue escribiendo CSVs (sin cambios)
- Event log se escribe en paralelo
- Ambos sistemas conviven hasta F4
- Validación: event log replay debe producir mismo estado que CSV actual (corregido por dedup)

### 6.3 Rollback plan

Si algo falla:

- Desactivar `RiskPolicyEngine` → runtime vuelve a comportamiento actual
- Mantener CSV writers como fallback hasta F4
- Event log puede truncarse (es append-only, no destructivo)

---

## 7. Dual-Lane Replay Architecture

> El sistema tiene DOS lanes de replay independientes. No existe un "replay order correcto" único.
> Entity Lane garantiza correctitud por símbolo. Global Lane garantiza correctitud de riesgo.
> El Join Layer compone ambas proyecciones — no decide, no escribe eventos, no tiene autoridad.

### 7.1 Replay Lanes

#### Entity Lane (per-symbol state machines)

Propósito: Reconstruir el estado de cada símbolo independientemente.

```
Eventos que consume:
  SignalGenerated, SignalRejected
  OrderCreated, OrderFilled, OrderCancelled
  PositionOpened, PositionUpdated, PositionClosed

Orden:
  (entity_id, stream_priority, timestamp_bucket, timestamp, event_id)

Reducers:
  PositionReducers — uno por entidad, puros, sin I/O.

Proyección:
  PositionState — un diccionario keyed por symbol.
```

El entity_id grouping garantiza que eventos de BTC y ETH nunca se interleaven.
Cada entidad es aislada en orden.

#### Global Lane (risk/portfolio/system state)

Propósito: Reconstruir el estado global del sistema — riesgo, portfolio, configuración, simulación.

```
Eventos que consume:
  RiskEvaluated, RiskLimitBreached, RiskStateChanged
  SimulationEvaluated, MonteCarloCompleted
  ConfigChanged
  PortfolioSnapshot (futuro)

Orden:
  (stream_priority, timestamp, event_id)
  — Sin entity_id grouping. El orden es global.

Reducers:
  RiskReducers — puros, sin I/O.

Proyección:
  RiskState — una sola instancia global.
```

Sin entity_id grouping porque un `RiskStateChanged(new_status=HALT)` debe afectar
a TODO el portfolio, no solo a un símbolo.

### 7.2 Projection Types

Toda proyección incluye un `version` semántico (MAJOR.MINOR) para detectar
incompatibilidades entre snapshots guardados y el código de replay actual.

```
Regla de versionado:
  MAJOR: cambia cuando se elimina o renombra un campo (rompe compatibilidad).
  MINOR: cambia cuando se añade un campo opcional (compatible hacia atrás).

  Snapshot guardado con version != current_version:
    MAJOR mismatch → rechazar snapshot, replay completo desde el origen.
    MINOR mismatch → aceptar, valores por defecto para campos faltantes.
```

#### PositionState

```
Version:    1.0
Entidad:   una por símbolo
Producido: Entity Lane
Consumido: Risk Join Layer (solo lectura), ExecutionGate (solo lectura)
Contiene:  version: str, symbol, side, quantity, entry_price, sl_price, tp_price,
           status (OPEN | CLOSED), pnl, hold_bars
```

#### RiskState

```
Version:    1.0
Entidad:   única global
Producido: Global Lane
Consumido: Risk Join Layer (solo lectura), ExecutionGate (solo lectura)
Contiene:  version: str, risk_status (ACTIVE | DEGRADED | HALT | RECOVERY_PENDING),
           drawdown_pct, daily_pnl, circuit_breaker,
           p_failure_rolling (ventana N=3),
           current_balance, starting_balance
```

#### SystemState

```
Version:    1.0
Schema:     { risk: RiskState, positions: dict[symbol, PositionState],
              mode: EnvironmentMode, generated_at: timestamp, version: str }
Producido por: Risk Join Layer
Consumido por: ExecutionGate (enforcement), operators (observabilidad)
```

#### Contrato de determinismo del Join Layer

```
SystemState = Join(PositionStates, RiskState)

Propiedades:
  1. PURA:         Join(P, R) siempre produce el mismo SystemState para los mismos inputs.
  2. SIN EFECTOS:  Join no escribe eventos, no muta estado externo, no hace I/O.
  3. SIN DECISIÓN: Join no evalúa si HALT > ACTIVE. Solo refleja el RiskState recibido.
  4. SIN EXCEPCIÓN: Join no puede fallar. Si un input es inválido, el error se detecta
                    en Validator (7.5), no en Join.
```

El Join Layer NO escribe eventos y NO toma decisiones. Es una proyección de solo lectura.

### 7.3 Authority Matrix

| Componente | Replay Lane | Puede escribir | NO puede escribir |
|---|---|---|---|
| **Entity Reducers** (per-symbol) | Entity Lane | PositionState | RiskState, SystemState |
| **Risk Reducers** | Global Lane | RiskState | PositionState, SystemState |
| **Risk Join Layer** | — | SystemState (solo composición) | PositionState, RiskState, eventos |
| **SimulationEngine** | Global Lane (source) | SimulationEvaluated, MonteCarloCompleted | RiskStateChanged, órdenes, posiciones |
| **RiskPolicyEngine** | Global Lane (source + reducer) | RiskEvaluated, RiskLimitBreached, RiskStateChanged | PositionState, SystemState, órdenes |
| **ExecutionCore** | Entity Lane (source) | OrderCreated, OrderFilled, OrderCancelled, PositionOpened, PositionUpdated, PositionClosed | RiskState, SystemState, SimulationEvaluated |
| **SignalEngine** | Entity Lane (source) | SignalGenerated, SignalRejected | RiskState, SystemState, órdenes |
| **ConfigStore** | Global Lane (source) | ConfigChanged | Eventos de trading |
| **ExecutionGate** | — | Nada (solo enforcement) | Cualquier evento |

#### Regla fundamental

> El Join Layer lee, compone, y expone. No decide. No escribe eventos.

La decisión es siempre del RiskPolicyEngine (autoridad de riesgo) o del
ExecutionCore (autoridad de ejecución). El Join Layer solo hace visible
el estado compuesto para que otros componentes actúen.

### 7.4 Recovery Semantics

Orden obligatorio al reiniciar el sistema:

```
PASO 1: Replay Global Lane
        RiskState reconstruido desde event log.

PASO 2: Replay Entity Lane
        PositionState por símbolo reconstruido.

PASO 3: Build SystemState
        Join Layer compone RiskState + PositionState.

PASO 4: Validate SystemState
        Ejecutar invariantes de consistencia (7.5).

PASO 5: Enable Execution
        Solo si risk_status != HALT y validator PASS.
```

Diagrama:

```
RECOVERY_FLOW

    Global Lane ──► RiskState
    Entity Lane ──► PositionState
          │
          ▼
    Risk Join Layer ──► SystemState
          │
          ▼
    Validator (STRICT | RECOVERY)
          │
    ┌─────┴─────┐
    │           │
    PASS        FAIL
    │           │
    ▼           ▼
  ACTIVE      HALT (permanente,
               requiere fix manual)
```

Modos del Validator:

- **STRICT**: aborta si cualquier invariante falla. No se habilita ejecución.
- **RECOVERY**: registra violaciones, habilita en modo DEGRADED, emite alerta.

### 7.5 Consistency Invariants

Invariantes que el Validator verifica en cada SystemState producido por el Join Layer:

Cada invariante tiene un **Owner** responsable de implementar la validación.
Un invariante sin dueño explícito no se implementa — o se implementa en el lugar equivocado.

| # | Invariante | Regla | Owner | Detección |
|---|---|---|---|---|
| **I1** | Cantidad no negativa | `position.quantity >= 0` | Position Reducer | quantity < 0 → datos corruptos |
| **I2** | PnL derivable | `pnl ≈ (current_price - entry_price) × quantity` | Position Validator | Desviación > 0.01 → ghost pnl |
| **I3** | Sin fills en posición cerrada | PositionClosed no puede recibir OrderFilled | Position Validator | Fill post-close → error de proyección |
| **I4** | HALT implica sin nuevas órdenes | ExecutionGate bloquea si `risk_status == HALT` | ExecutionGate | Órdenes nuevas en HALT → violación de governance |
| **I5** | Máquina de estados de riesgo válida | ACTIVE → DEGRADED → HALT → RECOVERY_PENDING → ACTIVE (o HALT permanente) | Risk Validator | Transición inválida → error de autoridad |
| **I6** | Señal dentro de rango | `0.0 <= confidence <= 1.0` | Signal Reducer | confidence fuera de rango |
| **I7** | Portfolio exposure consistente | `sum(position.notional) ≈ portfolio_exposure` | Risk Validator | Diferencia > margin_threshold → error de exposición |
| **I8** | Entry price no cero | OPEN position con `entry_price = 0.0` | Position Reducer | Cero en posición abierta → datos corruptos |
| **I9** | Recovery no bypassa HALT | HALT → RECOVERY_PENDING requiere evento explícito del RiskPolicyEngine | Risk Validator | Transición sin evento de autoridad |

### 7.6 Snapshot Migration Policy

Cuando el schema de una proyección cambia (por refactor, nueva feature, o fix),
los snapshots existentes quedan en una version anterior.

#### Reglas de migración

```
Regla A: Toda proyección tiene un version explícito (MAJOR.MINOR).

Regla B: Al cargar un snapshot para recovery:
           Si snapshot.version.MAJOR == current.MAJOR:
               usar snapshot como base
           Si snapshot.version.MAJOR != current.MAJOR:
               ignorar snapshot, replay completo desde event log

Regla C: Al persistir un snapshot:
           Siempre incluir la version actual del código.

Regla D: Una migración MAJOR requiere un evento de migración explícito:
           event_type: "ProjectionMigrated"
           data: { from_version, to_version, migrated_at, n_events_replayed }
           Esto permite auditar cuándo y por qué se descartaron snapshots.

Regla E: No existe "upgrade in place" de snapshots.
           Para migrar de v1 a v2:
             1. Ignorar snapshot v1
             2. Replay completo desde event log
             3. Persistir nuevo snapshot v2
           Esto garantiza que el estado migrado es exactamente el mismo
           que produciría un replay limpio.
```

#### Ejemplo de ciclo

```
Semana 1:
  Código:     PositionState v1.0
  Snapshot:   v1.0 → ok

Semana 2 (se añade trailing_sl):
  Código:     PositionState v1.1 (MINOR bump)
  Snapshot:   v1.0 → compatible → usar con default trailing_sl=None

Semana 3 (se renombra quantity → size):
  Código:     PositionState v2.0 (MAJOR bump)
  Snapshot:   v1.0 → INCOMPATIBLE → replay completo → nuevo snapshot v2.0
  Evento:     ProjectionMigrated(from="1.0", to="2.0", n_events_replayed=15000)
```

### 7.7 Event Ordering Edge Cases

> El orden de replay está definido. Pero los eventos no siempre llegan en orden.
> Esta sección define cómo responde cada lane ante eventos fuera de orden,
> duplicados, faltantes, o corruptos — sin perder determinismo.

#### 7.7.1 Late events

Un evento es "late" cuando su `event_time` es menor al del último evento procesado
para la misma entidad (Entity Lane) o globalmente (Global Lane).

```
Ejemplo Entity Lane:
  Evento recibido:     PositionOpened(symbol=BTC, event_time=12:05:00) → procesado
  Evento recibido:     PositionUpdated(symbol=BTC, event_time=12:04:30) → LATE

Regla:
  Entity Lane:  El late event se procesa SIEMPRE. El sort key
                (entity_id, stream_priority, bucket, event_time, event_id)
                lo coloca en su posición correcta dentro del replay.
                El reducer recibe eventos en orden temporal, no en orden
                de ingesta.

  Global Lane:  Ídem. El sort key no depende del orden de escritura.
```

**El sistema no rechaza late events. Los reordena por construcción.**

#### Regla crítica

```
Replay ordering MUST use event_time.
Replay ordering MUST NEVER use persisted_at, received_at, o created_at.

event_time  = el momento lógico en que el evento ocurrió (emitido por el source).
persisted_at = el momento físico en que el evento se escribió en la store.
received_at  = el momento en que el evento llegó al adapter.

Si persisted_at o received_at se usan como orden:
  → Eventos tardíos por latencia de red quedan fuera de orden lógico.
  → Replay no determinista: mismo evento log, distinto resultado según el
     instante físico de ingesta.
  → Regresión directa al problema que late events resuelve.
```

#### Late event reconciliation policy

El único riesgo real de un late event no es el replay — el sort key lo
ubica correctamente. El riesgo es el **coste** de re-procesar eventos
desde el punto de inserción hasta el presente.

```
Tres modelos posibles:

  Modelo A — Full rewind
    Volver al snapshot anterior al late event y re-procesar todo.
    Resultado: estado perfecto.
    Coste: O(n) desde el snapshot, potencialmente millones de eventos.

  Modelo B — Compensation event
    Emitir un CompensationEvent que ajusta el estado hacia adelante.
    Resultado: estado aproximado.
    Coste: O(1), pero introduce estado no derivable del event log original.

  Modelo C — Sliding rewind
    Si event_time está dentro de una ventana W desde el presente:
      → full rewind (Modelo A).
    Si event_time está fuera de W:
      → compensation event (Modelo B).
    Resultado: estado perfecto para eventos recientes, aproximado para antiguos.
    Coste: acotado por W.
```

Decisión para ARGOS (EMC v1):

```
POLÍTICA: Modelo A — Full rewind.

Razones:
  1. ARGOS no opera a latencia de HFT. Un replay de 1M eventos en SQLite
     toma ~1-2 segundos. El coste computacional es despreciable frente al
     coste de un estado inconsistente.
  2. Compensation events introducen lógica de negocio en el Join Layer,
     violando el principio de "Join = composición pura" (§7.2).
  3. El event log es la única fuente de verdad. Los compensation events
     crearían una segunda categoría de eventos (derivados vs originales)
     que erosiona la auditabilidad.
  4. Forward test actual: ~5000 eventos en 5+ días. A escala de producción
     (~10k eventos/día), un full rewind semanal sigue siendo trivial.

Excepción (futuro):
  Si el volumen supera 1M eventos/día y el tiempo de rewind supera 5s,
  se puede evaluar sliding rewind con W = 1 hora. Pero esto requiere:
    - Nueva sección en el EMC.
    - Nuevos invariantes de consistencia para compensaciones.
    - Eventos de compensación con su propio idempotency key.
  Hasta entonces: siempre full rewind.
```

#### 7.7.2 Duplicate events

Cubierto por idempotency contract (§3):

```
INSERT OR IGNORE por event_id
```

El replay encuentra el evento exactamente una vez. Si dos filas con el mismo
event_id existen en la store (por bug en otro adapter), el replay debe:

```
Regla: Al cargar eventos, hacer SELECT DISTINCT event_id.
       Si dos eventos tienen el mismo event_id pero distinto payload
       → violación de idempotencia → STRICT: abort. RECOVERY: primer
         evento gana, log de warning.
```

#### 7.7.3 Missing events (gaps)

El replay no puede detectar "eventos que nunca existieron".
Pero sí puede detectar "estados imposibles causados por eventos ausentes".

```
Formalmente:
  - Tipo A: El evento 15 nunca se emitió.
            → Imposible de detectar. No hay huella.

  - Tipo B: Un evento necesario para la consistencia del estado no está.
            → Detectable mediante violación de invariantes (§7.5).

Ejemplo Tipo B:
  PositionClosed(p1) sin PositionOpened(p1) previo.
  → I8 se viola: entry_price = 0.0 en posición cerrada.
  → El validador reporta: "posición cerrada sin apertura — posible gap".

Regla:
  El replay NO infiere eventos faltantes.
  Las invariantes pueden revelar su existencia indirectamente.
  Un gap detectado via invariante NO es reparado automáticamente.
  El health check externo decide si el gap es tolerable o requiere
  intervención manual.
```

#### 7.7.4 Corrupted events

Un evento corrupto tiene un payload que no se puede deserializar según su schema.

```
Regla:
  STRICT mode:  abortar replay con CorruptedEventError.
                Incluir event_id, expected_schema, parse_error.
  RECOVERY mode: saltar el evento, registrar en violations, continuar.
                 El estado resultante será incompleto.

  Siempre: log del evento corrupto con event_id y offset físico
           en la store para reparación manual.
```

#### Marcador de corrupción

En RECOVERY mode, al saltar un evento corrupto, el replay DEBE emitir un
evento técnico de corrupción:

```
event_type: "ProjectionCorruptionDetected"
source: "ReplayValidator"
version: 1
data: {
  "event_id": "<id_del_evento_corrupto>",
  "error": "<parse_error>",
  "action": "skipped",
  "n_skipped_total": <count>,
  "replay_id": "<uuid_de_esta_sesion_de_replay>",
  "mode": "RECOVERY"
}
```

Esto garantiza que:

- Un replay completado en RECOVERY mode NO es silenciosamente exitoso.
- El operador tiene un rastro auditable de qué eventos se omitieron.
- El health check puede alertar si `n_skipped_total > 0`.

```
Regla de marcador:
  RECOVERY mode + skip > 0 → ProjectionCorruptionDetected es OBLIGATORIO.
  STRICT mode → no aplica (aborta antes).
```

No hay "reparación automática" de eventos corruptos. El operador debe:

1. Identificar el event_id desde el marcador de corrupción
2. Decidir si reemplazar, eliminar, o ignorar
3. Ejecutar la acción fuera del replay loop

#### 7.7.5 Replay from partial snapshot

Cuando el sistema se recupera desde un snapshot (no desde evento 0):

```
Riesgo:
  El snapshot puede estar desactualizado respecto al event log
  si eventos se escribieron después del último snapshot.

Solución:
  PASO 1: Cargar snapshot más reciente compatible (version MAJOR match).
  PASO 2: Verificar integridad del snapshot:
            - event_index debe existir y ser ≥ 0
            - last_event_hash debe coincidir con hash(event_index) en store
  PASO 3: Replay solo los eventos con index > snapshot.event_index.
  PASO 4: Validar SystemState resultante.

  Si el snapshot no tiene event_index o last_event_hash:
    → Rechazar snapshot. Replay completo.
    → Esto fuerza a todos los snapshots a incluir metadatos de origen.

Regla de integridad:
  snapshot.event_index debe ser ≤ min(event_index en store).
  Si snapshot.event_index > algún evento en store:
    → El snapshot es más reciente que los datos disponibles.
    → Corrupción: abortar, requerir replay completo.

Regla del hash:
  snapshot.last_event_hash = hash(event_id del último evento incluido).
  Si last_event_hash no coincide con el evento real en ese index:
    → El snapshot apunta a un evento distinto del que cree.
    → Posible reescritura o migración que cambió event_id.
    → Corrupción: rechazar snapshot, replay completo.

Regla de invalidación por late event:
  Un snapshot es válido solo si su `last_event_time` es menor o igual
  al `event_time` del primer evento procesado después de él.

  Si un late event tiene `event_time < snapshot.last_event_time`:
    → El snapshot incorporó estado de ese período sin el evento.
    → El snapshot está corrupto aunque los checksums coincidan.
    → Invalidar el snapshot, buscar el snapshot anterior más cercano
      cuyo `last_event_time < event_time` del late event.
    → Si no existe un snapshot anterior compatible: replay completo.

  Por tanto, la metadata del snapshot debe incluir:
    - event_index: índice del último evento incluido
    - last_event_id: event_id del último evento incluido
    - last_event_hash: hash(event_id) (para detectar reescrituras)
    - last_event_time: event_time del último evento incluido (para detectar
      contaminación por late events)

  ```
  Ejemplo:
    Snapshot S100: last_event_time = 12:00:00, event_index = 50000
    Llega late event E: event_time = 11:59:30, se inserta en index 49500
    → S100.last_event_time (12:00:00) > E.event_time (11:59:30)
    → S100 está contaminado: fue calculado sin considerar E
    → Invalidar S100, buscar snapshot anterior a 11:59:30
    → Si S90 tiene last_event_time = 11:50:00 → usar S90, replay 49001→actual
  ```
```

#### 7.7.6 Resumen de política

| Caso | Entity Lane | Global Lane | STRICT | RECOVERY |
|---|---|---|---|---|
| Late event | Reordenado por sort key + invalidación de snapshots contaminados | Reordenado por sort key + invalidación de snapshots contaminados | OK (rewind) | OK (rewind) |
| Duplicate event (mismo payload) | Idempotencia lo ignora | Idempotencia lo ignora | OK | OK |
| Duplicate event (distinto payload) | Primer evento gana | Primer evento gana | Abort | Warning + primer evento |
| Missing event | Invariantes pueden revelarlo | Invariantes pueden revelarlo | N/A (detectado post-replay) | N/A (detectado post-replay) |
| Corrupted event (deserialización) | Saltado + emite `ProjectionCorruptionDetected` | Saltado + emite `ProjectionCorruptionDetected` | Abort | Skip + marker event |
| Snapshot sin event_index o last_event_hash | Rechazado | Rechazado | Replay completo | Replay completo |
| Snapshot > store | Corrupción | Corrupción | Abort | Abort |
| Snapshot hash mismatch | Corrupción (event_id reescrito) | Corrupción (event_id reescrito) | Abort | Abort |
| Snapshot invalidado por late event | Rechazado si `last_event_time > event_time` | Rechazado si `last_event_time > event_time` | Replay desde snapshot anterior | Replay desde snapshot anterior |

### 7.8 Reducer Transition Contract

> Cada reducer es una máquina de estados. Esta sección define las transiciones
> válidas para cada una. El dominio define al reducer — no al revés.

Toda transición que no esté en esta tabla es **ilegal** y debe ser detectada
por el validador de invariantes (§7.5) o por el propio reducer.

#### Position State Machine

```
Estados: NONE → OPEN → CLOSED
```

| Estado actual | Evento | Estado resultante | ¿Legal? | Condición adicional |
|---|---|---|---|---|
| NONE | PositionOpened | OPEN | ✅ | entry_price > 0, quantity > 0 |
| NONE | cualquier otro | NONE (no-op) | ✅ | Evento irrelevante |
| OPEN | PositionOpened | ERROR | ❌ | Doble apertura — datos corruptos |
| OPEN | PositionUpdated | OPEN | ✅ | Solo SL/TP adjustment |
| OPEN | PositionMerged | OPEN | ✅ | Suma de cantidades (futuro) |
| OPEN | PositionClosed | CLOSED | ✅ | close_price > 0, close_reason requerido |
| CLOSED | PositionOpened | OPEN | ✅ | Nueva posición (mismo símbolo) |
| CLOSED | PositionUpdated | ERROR | ❌ | No se puede modificar posición cerrada |
| CLOSED | PositionMerged | ERROR | ❌ | No se puede mergear posición cerrada |
| CLOSED | PositionClosed | ERROR | ❌ | Doble cierre — datos corruptos |

#### Risk State Machine

```
Estados: ACTIVE ↔ DEGRADED ↔ HALT ↔ RECOVERY_PENDING
         (con transiciones controladas)
```

| Estado actual | Evento | Estado resultante | ¿Legal? | Condición |
|---|---|---|---|---|
| * | RiskEvaluated | mismo | ✅ | Solo actualiza métricas, no cambia estado |
| * | RiskLimitBreached | mismo (con WARN/TRIP) | ✅ | Solo alerta, no transición |
| ACTIVE | RiskStateChanged(DEGRADED) | DEGRADED | ✅ | rolling p_failure > 0.5 |
| ACTIVE | RiskStateChanged(HALT) | HALT | ✅ | hazard > 0.8 o drawdown > 5% |
| ACTIVE | RiskStateChanged(ACTIVE) | ACTIVE (no-op) | ✅ | Idempotente |
| ACTIVE | RiskStateChanged(RECOVERY_PENDING) | ERROR | ❌ | No se puede recovery desde ACTIVE |
| DEGRADED | RiskStateChanged(ACTIVE) | ACTIVE | ✅ | Mejora de condiciones |
| DEGRADED | RiskStateChanged(HALT) | HALT | ✅ | Empeoramiento |
| DEGRADED | RiskStateChanged(RECOVERY_PENDING) | ERROR | ❌ | Recovery solo desde HALT |
| HALT | RiskStateChanged(RECOVERY_PENDING) | RECOVERY_PENDING | ✅ | Timer (30 min) o intervención manual |
| HALT | RiskStateChanged(ACTIVE) | ERROR | ❌ | No se puede saltar RECOVERY_PENDING |
| HALT | RiskStateChanged(DEGRADED) | ERROR | ❌ | No se puede degradar desde HALT |
| RECOVERY_PENDING | RiskStateChanged(ACTIVE) | ACTIVE | ✅ | Replay válido |
| RECOVERY_PENDING | RiskStateChanged(HALT) | HALT | ✅ | Replay inválido (corrupción permanente) |
| RECOVERY_PENDING | RiskStateChanged(RECOVERY_PENDING) | RECOVERY_PENDING (no-op) | ✅ | Idempotente |
| RECOVERY_PENDING | RiskStateChanged(DEGRADED) | ERROR | ❌ | No se puede degradar desde recovery |

#### Signal State Machine

```
No es una máquina de estados — es último-valor-conocido.
SignalGenerated y SignalRejected sobrescriben el estado anterior.
```

| Estado actual | Evento | Estado resultante | ¿Legal? |
|---|---|---|---|
| * | SignalGenerated | nuevo signal | ✅ |
| * | SignalRejected | signal con rejected=true | ✅ |
| * | cualquier otro | mismo | ✅ |

#### Reglas generales

```
R1: El reducer NO puede producir un estado ilegal.
    Si el evento no está en la tabla → el reducer retorna el estado sin cambios (no-op).

R2: El reducer NO puede lanzar excepción por transición ilegal.
    La detección es responsabilidad del validador de invariantes (§7.5),
    no del reducer. El reducer es una función total.

R3: La transición ilegal detectada por el validador produce:
    STRICT mode: abort.
    RECOVERY mode: violación registrada, estado sin cambios.

R4: Cada máquina de estados tiene exactamente un EventType de "creación".
    PositionOpened → estado OPEN.
    RiskStateChanged con status → nueva estado de riesgo.

R5: No existe "reset" de una máquina de estados vía evento.
    Para reiniciar el sistema: RiskStateChanged(RECOVERY_PENDING → ACTIVE)
    después de replay válido.
```

#### Implementación esperada

```python
def reduce_position(event: DomainEvent, state: PositionState) -> PositionState:
    """Pure reducer for Position state machine.

    Only implements transitions from the matrix above.
    Unknown transitions → return state unchanged.
    """
    mappings = {
        "PositionOpened": _on_opened,
        "PositionUpdated": _on_updated,
        "PositionMerged": _on_merged,
        "PositionClosed": _on_closed,
    }
    handler = mappings.get(event.event_type)
    if handler is None:
        return state  # no-op for unknown transitions
    return handler(event, state)
```

---

## 8. Anti-patterns (lo que NO haremos)

| Anti-patrón | Por qué no |
|---|---|
| SimulationEngine emite `OrderCreated` | Rompe regla de autoridad. Recrea el bug actual. |
| Event log es también la fuente de configuración | Config y events tienen ciclos de vida distintos. Config es governance; events son historia. |
| RiskPolicyEngine lee directo de SimulationEngine | Bridge implícito y no rastreable. Siempre debe ser vía eventos. |
| Proyectores leen de Redis o API externa | Rompe deterministic replay. Si necesitas data externa, debe ser otro evento. |
| event_id basado en UUID aleatorio | No hay idempotencia real. |
| Un solo event log para todo el sistema | Domain events vs system events tienen diferentes consumidores y retention. |

---

## 9. Apéndice: Estado actual vs EMC v1

| Aspecto | Hoy | Con EMC v1 |
|---|---|---|
| Write autority | Difuso (cualquiera escribe CSV) | Solo ExecutionCore para ejecución |
| Idempotencia | Ninguna | event_id determinístico |
| Simulation → Runtime | HALT directo ignorado | SimulationEvaluated → RiskPolicyEngine (rolling N=3) → RiskStateChanged |
| Log pipeline | Roto (CSV vacío) | Event log inmutable |
| SQLite | Huérfano (no existe) | Cache de proyección |
| Replay | Imposible (datos duplicados) | Determinístico |
| Config source | 3 fuentes en conflicto | ConfigStore único |

---

---

## 10. F3 — Projection Integrity Layer

> Auditoría de ejecución: el sistema no solo verifica que el estado sea correcto,
> sino que fue construido bajo las mismas reglas de ejecución.

F3 separa tres conceptos que en sistemas inmaduros se confunden:

| Concepto | No es | Es |
|---|---|---|
| Projection Hash | Hash del state | Hash de (state + execution trace + fingerprint) |
| Equivalence | "mismo resultado" | "misma ejecución" |
| Invalidation | "código diferente" | "ejecución no reproducible exactamente" |

---

### 10.1 Equivalence Contract

Dos proyecciones son equivalentes **si y solo si**:

```text
state_equivalence
  AND trace_equivalence
  AND fingerprint_equivalence
```

#### State Equivalence

```text
state_A == state_B
⇔
∀ field ∈ schema(state_A):
  field_A == field_B
```

El state se serializa canónicamente (sorted keys, sin `generated_at` ni clock-based fields).

#### Trace Equivalence

```text
trace_A == trace_B
⇔
len(trace_A) == len(trace_B)
  AND ∀ i: trace_entry_A[i] == trace_entry_B[i]
```

Dos entries son iguales cuando todos sus campos coinciden:

```text
entry_A == entry_B
⇔
event_id_A == event_id_B
  AND lane_A == lane_B
  AND reducer_id_A == reducer_id_B
  AND transition_id_A == transition_id_B
  AND entity_id_A == entity_id_B
  AND ordering_key_A == ordering_key_B
```

#### Fingerprint Equivalence

```text
fingerprint_A == fingerprint_B
⇔
  lane_scheduler_version_A == lane_scheduler_version_B
  AND reducer_versions_A == reducer_versions_B
  AND join_composer_version_A == join_composer_version_B
  AND sort_key_spec_hash_A == sort_key_spec_hash_B
  AND event_schema_registry_hash_A == event_schema_registry_hash_B
```

#### Core Property

```text
∀ events:
  replay(events, initial_state) deterministically defines:
    (state, trace, fingerprint)
```

```text
∀ events, split:
  full_replay(events) == snapshot_replay(events, split)
  ⇔
  full_replay(events).(state, trace, fingerprint)
  ==
  snapshot_replay(events, split).(state, trace, fingerprint)
```

---

### 10.2 Execution Trace Schema

#### TraceEntry

```python
@dataclass(frozen=True)
class TraceEntry:
    event_id: str          # SHA-256 del evento (referencia cruzada con event store)
    lane: str              # "ENTITY" | "GLOBAL"
    reducer_id: str        # e.g. "PositionReducer/v1", "RiskReducer/v1"
    transition_id: str     # SHA-256(event_type + reducer_id + state_invariant_pattern)
    entity_id: str         # symbol or "global"
    ordering_key: str      # (entity_id, stream_priority, bucket, timestamp, event_id) serializado
```

#### reducer_id

```text
Formato: "{ReducerName}/v{major}"

Ejemplos:
  PositionReducer/v1
  RiskReducer/v1
  SignalReducer/v1
```

**MAJOR bump**: ocurre cuando el reducer cambia semánticamente — transiciones
distintas, nuevos estados, o cualquier cambio que afecte el resultado observable.

**NO hay MINOR en reducer_id**: cualquier cambio detectable debe ser MAJOR,
porque el trace lo captura a nivel de entrada individual.

#### transition_id

```text
transition_id = SHA-256(event_type + "|" + reducer_id + "|" + invariant_pattern)

invariant_pattern:
  "POSITION_STATE_MACHINE_v1"
  "RISK_STATE_MACHINE_v1"
  "SIGNAL_LAST_VALUE_v1"
```

Esto vincula cada transición a la máquina de estados exacta que la produjo.
Si la máquina de estados cambia (nuevas reglas en la tabla de §7.8),
`invariant_pattern` cambia → `transition_id` cambia → trace diverge → hash invalida.

---

### 10.3 Projection Fingerprint (two-level)

El fingerprint se divide en dos niveles para distinguir **cambios de semántica
de ejecución** de **cambios de metadata de build**.

```python
@dataclass(frozen=True)
class StructuralFingerprint:
    """Ejecución: cambios aquí pueden alterar el comportamiento del sistema.

    Se computa desde la lógica real (tablas de routing, matrices de transición,
    fórmulas de orden), no desde version strings.
    """
    lane_scheduler_routing_hash: str      # SHA-256 de la tabla route(event_type) → lane
    reducer_dispatch_hash: str            # SHA-256 de _REDUCERS + transition matrices
    sort_key_spec_hash: str               # SHA-256 de la fórmula de orden
    schema_semantics_hash: str            # SHA-256 de schemas + reglas de interpretación


@dataclass(frozen=True)
class DeploymentFingerprint:
    """Metadata: cambios aquí no afectan semántica de ejecución."""
    lane_scheduler_version: str           # "v1"
    reducer_versions: dict[str, str]      # {entity_type: "v1"}
    join_composer_version: str            # "v1"
    build_hash: str                       # git commit hash o CI build ID
```

#### StructuralFingerprint — cuándo cambia

| Campo | Se computa desde | Cambia cuando |
|---|---|---|
| `lane_scheduler_routing_hash` | Tabla `{event_type → lane}` + default rule | Se agrega/elimina una ruta o cambia el default |
| `reducer_dispatch_hash` | `_REDUCERS` mapping + matrices de transición de §7.8 | Se agrega/elimina/modifica un reducer |
| `sort_key_spec_hash` | Fórmula de orden `(entity_id, stream_priority, bucket, timestamp, event_id)` | Cambia el orden de replay |
| `schema_semantics_hash` | Schemas de eventos del catálogo §2 | Cambia la interpretación semántica de un campo |

#### DeploymentFingerprint — cuándo cambia

| Campo | Cambia cuando |
|---|---|
| `lane_scheduler_version` | Version bump del scheduler (no necesariamente cambia routing) |
| `reducer_versions` | Version bump de cualquier reducer |
| `join_composer_version` | Version bump del composer |
| `build_hash` | Nuevo build/commit |

#### Regla de clasificación

```text
structural_fingerprint mismatch
  → el sistema tiene lógica de ejecución distinta
  → DEGRADED potencial (riesgo de drift semántico)

deployment_fingerprint mismatch ONLY (structural igual)
  → el sistema tiene código distinto pero lógica idéntica
  → log only (benigno, different build same logic)
```

Esto es clave: **dos deploys del mismo código tienen structural_fingerprint
idéntico aunque deployment_fingerprint difiera**.

---

### 10.4 Projection Hash

```text
projection_hash = SHA-256(
    canonical_json(state)
    + "|TRACE|"
    + canonical_json(trace)
    + "|STRUCTURAL|"
    + canonical_json(structural_fingerprint)
    + "|DEPLOYMENT|"
    + canonical_json(deployment_fingerprint)
)
```

Donde `canonical_json()` produce JSON con sorted keys, sin espacios,
sin `generated_at` ni clock-based fields del SystemState.

El hash NO depende de:
- Timestamps de ejecución
- Metadata de sesión
- Paths de archivos
- Versiones de runtime (Python, SQLite)

**Regla**: el projection_hash cambia si cualquiera de los 4 componentes
cambia (state, trace, structural, deployment). Pero el sistema clasifica
el tipo de mismatch para decidir acción:

```text
structural mismatch → projection_hash cambia
deployment-only mismatch → projection_hash cambia (correcto)
pero el RiskPolicyEngine trata cada uno distinto (§11.4 Rule D)
```

---

### 10.5 Invalidation Rules

#### MAJOR — Invalida projection hash

| Categoría | ¿Qué lo detecta? | Ejemplo |
|---|---|---|
| Semantic change | State diff | Reducer cambia `SELL → HOLD` para mismo evento |
| Execution model change | Trace diff | Lane routing cambia, reducer version cambia, sort key cambia |
| Fingerprint change | Fingerprint diff | Nuevo schema de evento, nuevo lane scheduler |

Un cambio MAJOR significa que **el replay no es reproducible bajo las mismas reglas**.
El snapshot es inválido y se necesita full replay.

#### MINOR — NO invalida projection hash, solo versiona

| Categoría | Condición | Ejemplo |
|---|---|---|
| Representation change | State idéntico AND trace idéntico AND fingerprint idéntico | Refactor de reducer que produce mismo output en mismo orden |
| Additive schema | Solo campos nuevos opcionales (backward compat) | Campo `tags: list[str]` opcional en PositionState |
| Logging/Observabilidad | No afecta ni state ni trace ni fingerprint | `structlog` config, métricas, tracing |

Un cambio MINOR significa que **el replay produce exactamente la misma ejecución observable**.
Snapshots existentes siguen siendo válidos.

#### Regla de decisión (para implementación)

```python
def classify_change(
    old_state: OutputState, new_state: OutputState,
    old_trace: list[TraceEntry], new_trace: list[TraceEntry],
    old_fp: ProjectionFingerprint, new_fp: ProjectionFingerprint,
) -> str:
    if old_state != new_state:
        return "MAJOR"       # semantic change
    if old_trace != new_trace:
        return "MAJOR"       # execution model change
    if old_fp != new_fp:
        return "MAJOR"       # fingerprint change (same execution, different code)
    return "MINOR"           # representation change only
```

---

### 10.6 Verification Rules

#### One-shot verification

```python
def verify_projection(
    events: list[DomainEvent],
    expected_hash: str,
    engine: EventSourcedReplay,
    store: EventStore,
) -> bool:
    """Returns True if projection matches expected hash."""
    result = await engine.replay(store, mode="STRICT")
    actual_hash = compute_projection_hash(result.state, result.trace, result.fingerprint)
    return actual_hash == expected_hash
```

Esta verificación:
- No requiere comparar estados (es caro)
- No requiere almacenar el trace (solo el hash)
- Detecta cualquier divergencia de ejecución

#### Full verification (for audit)

```python
def verify_full_equivalence(
    events: list[DomainEvent],
    split: int,
    engine: EventSourcedReplay,
    store: EventStore,
) -> VerificationReport:
    """Compare full replay vs snapshot + incremental replay."""
    full_result = await engine.replay(store, mode="STRICT")
    snap_result = await engine.replay(
        store, mode="STRICT",
        snapshot=snapshot_metadata,
        initial_state=snapshot_state,
    )
    report = VerificationReport(
        full_hash=compute_projection_hash(full_result),
        snap_hash=compute_projection_hash(snap_result),
        state_match=full_result.state == snap_result.state,
        trace_match=full_result.trace == snap_result.trace,
        fingerprint_match=full_result.fingerprint == snap_result.fingerprint,
    )
    return report
```

#### VerificationReport

```python
@dataclass(frozen=True)
class VerificationReport:
    full_hash: str
    snap_hash: str
    state_match: bool
    trace_match: bool
    fingerprint_match: bool

    @property
    def is_equivalent(self) -> bool:
        return self.state_match and self.trace_match and self.fingerprint_match

    @property
    def full_hash_matches(self) -> bool:
        return self.full_hash == self.snap_hash
```

---

### 10.7 Canonical JSON Specification

Para que `canonical_json()` sea determinista entre implementaciones:

```text
Reglas:
  1. Sort dict keys lexicographically (Python default).
  2. Serialize floats con repr(float) para evitar ambigüedad de precisión.
  3. Excluir campos clock-based: generated_at, processed_at, session_id.
  4. Arrays: orden de inserción preservado (determinista por construcción).
  5. Dataclasses frozen → hash como dict con @property computed fields excluidos.
```

---

### 10.8 F3 Pipeline (cómo se integra en replay)

```text
EventSourcedReplay.replay() produce:

  (state, trace, fingerprint)
         │         │
         │         └──→ ProjectionFingerprint (generado internamente)
         │
         └──→ ExecutionTrace (generado durante reduction)
                └── cada evento aplicado al reducer produce un TraceEntry

compute_projection_hash(state, trace, fingerprint)
         │
         └──→ projection_hash (SHA-256 de los 3 componentes)

VerificationEngine.verify(events, expected_hash)
         │
         ├──→ full_replay → (state, trace, fingerprint)
         ├──→ compute_projection_hash
         └──→ compare with expected_hash
```

#### Dónde se genera cada componente

| Componente | Generado por | Dónde |
|---|---|---|
| `state` | Reducers (existente) | `event_sourced_replay.py` |
| `trace` | LaneScheduler + Reducers + Sort | NUEVO: `execution_trace.py` |
| `fingerprint` | Versiones de cada componente | NUEVO: `projection_metadata.py` |
| `projection_hash` | `projection_hasher.py` | NUEVO |

---

### 10.9 Anti-patterns de F3

| Anti-patrón | Por qué no |
|---|---|
| Hash del state solamente | No detecta execution drift — mismo estado puede venir de lógica distinta |
| Hash del trace sin fingerprint | No detecta cambios de código que no afectan trace actual (pero afectarían el próximo) |
| Timestamp en el hash | Rompe determinismo entre replays del mismo conjunto de eventos |
| Paths o versiones de runtime en el hash | Falsos positivos por entorno, no por lógica |
| Comparar traces enteros en producción | Es caro — comparar solo hashes. Traces enteros solo en property tests y auditoría |

---

---

## 11. Risk Integration Layer — Verification ↔ Authority Bridge

> Puente formal entre F3 (verificación de proyección) y RiskPolicyEngine
> (autoridad de estado). Separation of concerns:
>
>   - VerificationEngine determina qué es verdad (epistemología).
>   - RiskPolicyEngine decide qué hacer con esa verdad (política).

### 11.1 Signal Event: `ProjectionIntegrityBreached`

NO es un state event. Es un **signal event**: describe una anomalía de
verificación sin mutar estado de riesgo directamente.

```text
event_type:   ProjectionIntegrityBreached
source:       VerificationEngine
version:      1
schema:       {
                verification_id: str,        // UUID del ciclo de verificación
                trigger:         str,         // "hash_mismatch" | "state_divergence" | "trace_divergence" | "fingerprint_mismatch"
                expected_hash:   str,         // hash esperado
                actual_hash:     str,         // hash calculado
                state_match:     bool,
                trace_match:     bool,
                fingerprint_match: bool,
                event_count:     int,         // eventos verificados
                last_valid_hash: str | null,  // último hash conocido válido
                detected_at:     str,         // ISO timestamp (lógico, no clock)
              }
```

#### Routing

```
VerificationEngine ──→ ProjectionIntegrityBreached ──→ RiskPolicyEngine (Global Lane)
```

VerificationEngine nunca escribe otro evento. RiskPolicyEngine es el único
consumidor autorizado.

---

### 11.2 Estado extendido del RiskState

El RiskPolicyEngine mantiene, además del `RiskState` actual, un contexto de
verificación:

```text
verification_context: {
    status:          "CLEAN" | "DEGRADED" | "BREACHED",
    last_breach:     ISO timestamp | null,
    breach_count:    int,           // en ventana actual
    window_start:    ISO timestamp | null,  // inicio de ventana de cooldown
    last_valid_hash: str | null,    // último hash que pasó verificación
    last_failure:    {              // última falla registrada
        trigger:     str,
        expected:    str,
        actual:      str,
        state_match: bool,
        trace_match: bool,
        fingerprint_match: bool,
    } | null,
}
```

Este contexto NO es parte del `RiskState` (no se proyecta desde eventos).
Es estado volátil del RiskPolicyEngine que se pierde al reiniciar.
El replay siempre reconstruye desde cero — la verificación es responsabilidad
del runtime, no del event log.

---

### 11.3 Tabla de transición extendida

La máquina de estados de §7.8 se extiende con el nuevo evento `ProjectionIntegrityBreached`.
Las guard conditions usan el verification_context.

#### Guard clauses

| Guard | Definición |
|---|---|
| `cooldown_active` | `now - last_breach < COOLDOWN_WINDOW` (300s default) |
| `breach_window_exceeded` | `breach_count >= MAX_BREACHES_IN_WINDOW` (3 default) |
| `hash_previously_valid` | `last_valid_hash is not None` |
| `is_full_breach` | `state_match == False AND trace_match == False` |
| `is_trace_only_breach` | `state_match == True AND trace_match == False` |
| `is_fingerprint_breach` | `fingerprint_match == False AND state_match == True AND trace_match == True` |

#### Transiciones

| Estado actual | Evento | Guard | Estado resultante | Acción del RiskPolicyEngine |
|---|---|---|---|---|
| ACTIVE | `ProjectionIntegrityBreached` | `is_full_breach OR is_trace_only_breach` | DEGRADED | Iniciar ventana de cooldown. incrementar breach_count. |
| ACTIVE | `ProjectionIntegrityBreached` | `is_fingerprint_breach` | ACTIVE (no-op) | Solo log. Fingerprint mismatch sin divergencia de state/trace es código nuevo, no corrupción. |
| DEGRADED | `ProjectionIntegrityBreached` | `cooldown_active AND breach_window_exceeded` | HALT | Breaches repetidos en ventana → el sistema no es confiable. |
| DEGRADED | `ProjectionIntegrityBreached` | `cooldown_active AND NOT breach_window_exceeded` | DEGRADED | Incrementar breach_count. Seguir en DEGRADED. |
| DEGRADED | `ProjectionIntegrityBreached` | `NOT cooldown_active` | HALT | Breach fuera de ventana = nuevo episodio → escalar a HALT. |
| DEGRADED | `SimulationEvaluated` (mejora) | `median(p_failure, N=3) < 0.3 AND verification_context.status == CLEAN` | ACTIVE | Condiciones de mercado mejoraron Y verificación limpia. |
| DEGRADED | `timer: cooldown_elapsed` | `breach_count == 0` | ACTIVE | Sin breaches durante toda la ventana de cooldown. Auto-recovery. |
| HALT | `ProjectionIntegrityBreached` | (siempre) | HALT (no-op) | Ya en HALT. No empeora. |
| HALT | `timer: recovery_elapsed` | (siempre) | RECOVERY_PENDING | Timer de recovery (30 min por defecto). Iniciar replay validation. |
| RECOVERY_PENDING | `VerificationEngine: verify() PASS` | (siempre) | ACTIVE | Replay válido. Limpiar verification_context. |
| RECOVERY_PENDING | `VerificationEngine: verify() FAIL` | (siempre) | HALT | Replay inválido. Corrupción confirmada. Requiere fix manual. |

---

### 11.4 Anti-oscillation Rules

> El peor caso de integración verification ↔ risk es un loop:
>
>   ACTIVE → DEGRADED → ACTIVE → DEGRADED → ...
>
> causado por un breach que se resuelve solo antes del próximo ciclo de
> verificación.

#### Rule A: Cooldown window

```text
COOLDOWN_WINDOW = 300 seconds (configurable)

Después de un breach que causa DEGRADED:
  - Se inicia una ventana de cooldown de 300s.
  - Cualquier breach DENTRO de la ventana incrementa breach_count.
  - El sistema NO puede volver a ACTIVE hasta que la ventana expire
    SIN nuevos breaches.
```

#### Rule B: Minimum DEGRADED time

```text
MIN_DEGRADED_TIME = 60 seconds (configurable)

Desde que se emite DEGRADED hasta que se puede volver a ACTIVE:
  - Deben pasar al menos 60 segundos.
  - Esto evita flips rápidos ACTIVE ↔ DEGRADED por verificaciones
    consecutivas con resultados opuestos.
```

#### Rule C: Escalation threshold

```text
MAX_BREACHES_IN_WINDOW = 3 (configurable)

Si durante una ventana de cooldown ocurren 3 o más breaches:
  - El sistema escala a HALT.
  - Se requiere recovery manual o timer.
```

#### Rule D: Non-oscillation — structural vs deployment

```text
Structural-only breach (state_match=True, trace_match=True,
                         structural_match=False, deployment_match=*):
  → El sistema tiene lógica de ejecución distinta.
  → POSIBLE DEGRADED: el mismo event stream puede producir resultados
    diferentes bajo routing/reducer/sort-key/schema distintos.
  → Se evalúan guard clauses completas (§11.3).

Deployment-only breach (state_match=True, trace_match=True,
                         structural_match=True, deployment_match=False):
  → El sistema tiene código distinto pero lógica idéntica.
  → NO causan DEGRADED. Solo se registran en log.
  → Razón: same structural fingerprint → same semantic execution.
    Diferente build hash/version string es esperable entre deploys.
  → Esto evita oscilaciones falsas en cada deploy.

Classificación por el RiskPolicyEngine:

    if not report.structural_match:
        # Riesgo de drift semántico — evaluar DEGRADED
        guard_evaluation()
    elif not report.deployment_match and report.structural_match:
        # Benigno — different build, same logic
        log.info("deployment fingerprint mismatch (benign)", ...)
        # no-op, no state transition
```

#### Rule E: Verification cycle limit

```text
MAX_VERIFICATIONS_PER_WINDOW = 10 (configurable)

El VerificationEngine no puede ejecutar más de N verificaciones
por ventana de cooldown. Esto evita:
  - CPU thrashing por verificación continua
  - Auto-DoS del sistema por breach loop
```

---

### 11.5 Pipeline completo

```
Runtime (cada N eventos o timer):

  VerificationEngine.verify(store, expected_hash)
        │
        ├── PASS → no-op (solo actualizar last_valid_hash)
        │
        └── FAIL → emitir ProjectionIntegrityBreached
                        │
                        ▼
              RiskPolicyEngine
                        │
                        ├── is_structural_mismatch?
                        │   ├── YES → guard evaluation (11.3)
                        │   │          ├── cooldown + window check (11.4)
                        │   │          ├── DEGRADED → restricciones
                        │   │          └── HALT → circuit breaker
                        │   │
                        │   └── NO → is_deployment_only_mismatch?
                        │              └── YES → log only (benign, Rule D)
                        │
                        └── (no-op completo si structural_match AND
                              deployment_match, pero esto no ocurriría
                              porque verify() habría pasado)
```

#### ¿Quién inicia la verificación?

La verificación NO es un loop infinito. Se inicia por:

1. **Timer periódico**: cada N minutos (configurable, default 15).
2. **Evento disparador**: después de cada replay (snapshot load, full rebuild).
3. **Sospecha**: cuando el sistema detecta datos inconsistentes
   (por ejemplo, invariantes en modo RECOVERY reportan violaciones).

---

### 11.6 Diagrama de estados extendido

```
                      ┌──────────────────────────────────────┐
                      │                                      │
                      ▼                                      │
               ┌──────────┐     integrity_breach             │
          ┌───▶│  ACTIVE  │─────(full/trace only)──────────┐ │
          │    └──────────┘                                │ │
          │         │                                      │ │
          │    p_failure > 0.5                         breach_count │
          │    (rolling window)                         >= 3 in │
          │         │                                  window  │
          │         ▼                                      │ │
          │    ┌──────────┐                    drawdown>5%  │ │
          │    │ DEGRADED │◀──────────────────────────┘   │ │
          │    └──────────┘                                │ │
          │    │    │    │                                 │ │
          │    │    │    └── integrity_breach (cooldown) ──┘ │
          │    │    │                                        │
          │    │    └─── integrity_breach (window exceeded)  │
          │    │         OR drawdown > 5%                    │
          │    │         OR hazard_immediate > 0.8           │
          │    │         │                                   │
          │    │         ▼                                   │
          │    │    ┌──────────┐                             │
          │    │    │   HALT   │──── recovery_timer ─────────┘
          │    │    └──────────┘     OR operator_resume
          │    │         │
          │    │    recovery iniciado
          │    │         │
          │    │         ▼
          │    │    ┌──────────────────┐
          │    └────│ RECOVERY_PENDING │
          │         └──────────────────┘
          │              │        │
          │     verify() │        │ verify()
          │     PASS     │        │ FAIL
          │              ▼        ▼
          │           ACTIVE    HALT
          │           (limpio)  (permanente)
          │
          └── cooldown_elapsed (0 breaches in window)
              OR p_failure < 0.3 AND verification CLEAN
```

### 11.7 Regla de autoridad (refuerzo de §5)

```
Rule A: VerificationEngine emite ProjectionIntegrityBreached. Punto.
Rule B: RiskPolicyEngine decide DEGRADED | HALT | RECOVERY_PENDING.
Rule C: VerificationEngine NUNCA muta estado de riesgo.
Rule D: RiskPolicyEngine NUNCA ejecuta verificación de proyección.
Rule E: La transición RECOVERY_PENDING → ACTIVE requiere verify() PASS
        explícito. No existe auto-recovery sin verificación.
```

### 11.8 Anti-patterns específicos

| Anti-patrón | Por qué no |
|---|---|
| VerificationEngine escribe RiskStateChanged | Rompe separation of concerns. Mezcla epistemología con política. |
| RiskPolicyEngine ejecuta verify() | Duplica lógica de F3. RiskPolicyEngine no debe depender de proyección interna. |
| `ProjectionIntegrityBreached` como state event | No debe persistirse en event log. Es un signal de runtime, no un hecho histórico. |
| Auto-recovery sin verify() PASS | El sistema no puede auto-curarse sin verificación. Sería como operar con datos no validados. |
| Cooldown de 0 segundos | Garantiza oscilación ACTIVE ↔ DEGRADED bajo verificación periódica. |
| Fingerprint mismatch causa DEGRADED | Falso positivo en cada deploy. Fingerprint cambia con código nuevo, no con estado corrupto. |

---

*Documento de diseño — Fase 2 del plan de saneamiento del sistema.*
*Próximo paso: implementación del EventWriter con idempotency (F1).*
